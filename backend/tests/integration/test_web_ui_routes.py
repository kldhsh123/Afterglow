"""web_ui 路由集成测试：TestClient 跑完整 HTTP 流程。

覆盖：
- ConfigUiAuth：无 token → 401；带正确 setup token → 200；/ping 与静态资源放行
- GET /config/schema、/values、/presets、/status 返回结构正确
- PUT /config/values 校验、写 .env、热重载 settings、备份机制
- PUT /config/values 在 .env 不存在时基于 .env.example 复制
- POST /config/import/upload 上传 + 嗅探一次完成
- POST /config/import/inspect 单文件嗅探
- POST /config/generate/api-key 返回 token
- localhost_only 中间件：非本机请求 403
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xuwen.chat_api.app import create_app
from xuwen.config import Settings


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
REPO_BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture()
def cfg_settings(tmp_path, monkeypatch) -> Settings:
    """子 app 跑在 tmp_path 工作目录，避免污染开发者 .env。"""
    monkeypatch.chdir(tmp_path)
    # 把 .env.example 拷一份到 tmp_path 让 put_values 能用模板复制行为
    (tmp_path / ".env.example").write_text(
        (REPO_BACKEND / ".env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return Settings(
        _env_file=None,
        config_ui_enabled=True,
        config_ui_localhost_only=False,
        api_auth_required=False,
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        config_ui_uploads_dir=tmp_path / "uploads",
    )


def _extract_token(app) -> str:
    sub = next(r.app for r in app.routes if getattr(r, "path", None) == "/config")
    for m in sub.user_middleware:
        if m.cls.__name__ == "ConfigUiAuth":
            return m.kwargs["setup_token"]
    raise AssertionError("ConfigUiAuth not mounted")


# ---------- 鉴权 ----------


def test_ping_open_without_token(cfg_settings) -> None:
    app = create_app(cfg_settings)
    with TestClient(app) as client:
        r = client.get("/config/ping")
        assert r.status_code == 200
        assert r.json()["ok"] is True


def test_schema_requires_token(cfg_settings) -> None:
    app = create_app(cfg_settings)
    with TestClient(app) as client:
        assert client.get("/config/schema").status_code == 401


def test_schema_with_valid_token(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        r = client.get("/config/schema", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert "fields" in data
        keys = {f["key"] for f in data["fields"]}
        assert "OPENAI_API_KEY" in keys


def test_xuwen_api_key_also_accepted_after_set(cfg_settings) -> None:
    """配置完成后用户用 XUWEN_API_KEY 也能访问配置 UI（覆盖 setup token）。"""
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        client.put(
            "/config/values",
            headers=h,
            json={"values": {"XUWEN_API_KEY": "user-key"}, "dry_run": False},
        )
        # 用 user-key 应当也能通过鉴权
        r = client.get(
            "/config/status",
            headers={"Authorization": "Bearer user-key"},
        )
        assert r.status_code == 200


def test_localhost_only_blocks_non_local(tmp_path, monkeypatch) -> None:
    """localhost_only=True 时非本机 IP 应被 403。"""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None,
        config_ui_enabled=True,
        config_ui_localhost_only=True,
        api_auth_required=False,
        config_ui_uploads_dir=tmp_path / "uploads",
        lance_db_path=tmp_path / "lance",
        persona_data_dir=tmp_path / "persona",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # TestClient 默认 client.host='testclient'，会被判为非本机
        r = client.get("/config/ping")
        assert r.status_code == 403
        assert "config_ui.localhost_only" in r.json()["error"]["code"]


# ---------- 元信息 ----------


def test_presets_returns_three_groups(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        r = client.get("/config/presets", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert {"chat", "embedding", "label"} <= set(data)
        ids = {p["id"] for p in data["chat"]}
        assert {"deepseek", "gemini", "custom", "ollama"} <= ids


def test_status_reflects_completeness(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        r = client.get("/config/status", headers=h)
        assert r.status_code == 200
        status = r.json()
        # 全空，所有 *_ok 都应 False
        assert status["identity_ok"] is False
        assert status["wizard_completed"] is False


def test_values_returns_secret_preview_only(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        # 先写一个 secret
        client.put(
            "/config/values",
            headers=h,
            json={"values": {"OPENAI_API_KEY": "sk-supersecret"}, "dry_run": False},
        )
        r = client.get("/config/values", headers=h)
        v = r.json()["values"]["OPENAI_API_KEY"]
        assert v["set"] is True
        # 仅返回 mask，不暴露完整 value
        assert "value" not in v
        assert v["preview"].endswith("cret")
        assert "*" in v["preview"]


# ---------- 写值 ----------


def test_put_values_creates_env_from_example(cfg_settings, tmp_path) -> None:
    """.env 不存在时基于 .env.example 复制，保留分组注释。"""
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        r = client.put(
            "/config/values",
            headers=h,
            json={
                "values": {
                    "SELF_NAME": "Me",
                    "SELF_UID": "u_me",
                    "FRIEND_NAME": "Friend",
                    "FRIEND_UID": "u_friend",
                },
                "dry_run": False,
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert (tmp_path / ".env").exists()

        content = (tmp_path / ".env").read_text(encoding="utf-8")
        # 保留 .env.example 顶部的注释
        assert "Afterglow" in content
        # 字段被精准更新
        assert "SELF_NAME=Me" in content
        assert "SELF_UID=u_me" in content


def test_put_values_hot_reloads_settings(cfg_settings, tmp_path) -> None:
    """写完 .env 后立即读 /status，应该看到新写入的字段。"""
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        assert client.get("/config/status", headers=h).json()["auth_ok"] is False
        client.put(
            "/config/values",
            headers=h,
            json={"values": {"XUWEN_API_KEY": "hot-key"}, "dry_run": False},
        )
        assert client.get("/config/status", headers=h).json()["auth_ok"] is True


def test_put_values_creates_backup_when_env_exists(cfg_settings, tmp_path) -> None:
    (tmp_path / ".env").write_text("SELF_NAME=Old\n", encoding="utf-8")
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        r = client.put(
            "/config/values",
            headers=h,
            json={"values": {"SELF_NAME": "New"}, "dry_run": False},
        )
        assert r.status_code == 200
        assert r.json()["backup"] is not None
        backup_dir = tmp_path / ".env-backups"
        assert backup_dir.exists(), "备份应该统一放到 .env-backups/ 子目录"
        backups = list(backup_dir.iterdir())
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "SELF_NAME=Old\n"


def test_put_values_dry_run_does_not_write(cfg_settings, tmp_path) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        r = client.put(
            "/config/values",
            headers=h,
            json={"values": {"SELF_NAME": "Test"}, "dry_run": True},
        )
        assert r.json()["ok"] is True
        assert r.json()["dry_run"] is True
        # 不应写入实际 .env
        assert not (tmp_path / ".env").exists()


def test_put_values_rejects_unknown_keys(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        r = client.put(
            "/config/values",
            headers=h,
            json={
                "values": {"SELF_NAME": "Me", "EVIL_INJECTED_KEY": "x"},
                "dry_run": True,
            },
        )
        # 未知字段不应进入 .env，但请求不算失败
        assert r.json()["ok"] is True
        assert "EVIL_INJECTED_KEY" in r.json()["rejected_keys"]


# ---------- 上传 + 嗅探 ----------


def test_upload_returns_candidates(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        with (FIXTURES / "sample_chat.json").open("rb") as f:
            r = client.post(
                "/config/import/upload",
                headers=h,
                files={"files": ("q.json", f, "application/json")},
            )
        assert r.status_code == 200
        body = r.json()
        assert len(body["uploaded"]) == 1
        u = body["uploaded"][0]
        assert u["format"] == "qqexporter_v5"
        assert u["total_messages"] > 0
        assert any(c["role_hint"] == "self" for c in u["candidates"])
        assert any(c["role_hint"] == "friend" for c in u["candidates"])


def test_upload_multi_files(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        with (FIXTURES / "sample_chat.json").open("rb") as f1, (
            FIXTURES / "sample_wechat_weflow.json"
        ).open("rb") as f2:
            r = client.post(
                "/config/import/upload",
                headers=h,
                files=[
                    ("files", ("qq.json", f1, "application/json")),
                    ("files", ("wx.json", f2, "application/json")),
                ],
            )
        body = r.json()
        assert len(body["uploaded"]) == 2
        formats = {u["format"] for u in body["uploaded"]}
        assert formats == {"qqexporter_v5", "wechat_weflow"}


def test_inspect_endpoint_runs_without_keeping_file(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    uploads_dir = Path(cfg_settings.config_ui_uploads_dir)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        with (FIXTURES / "sample_chat.json").open("rb") as f:
            r = client.post(
                "/config/import/inspect",
                headers=h,
                files={"file": ("q.json", f, "application/json")},
            )
        assert r.status_code == 200
        assert r.json()["format"] == "qqexporter_v5"

    # inspect 用完即删，uploads_dir 不该残留 inspect_*
    if uploads_dir.exists():
        assert not list(uploads_dir.glob("inspect_*"))


# ---------- 工具端点 ----------


def test_generate_api_key_returns_long_random(cfg_settings) -> None:
    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        r = client.post("/config/generate/api-key", headers=h)
        assert r.status_code == 200
        token_value = r.json()["token"]
        # token_urlsafe(32) → ~43 char
        assert len(token_value) >= 30


def test_healthz_open_under_first_run_mode(tmp_path, monkeypatch) -> None:
    """首次模式自动启用配置 UI，但 /healthz 仍必须免鉴权可用（容器存活探针场景）。"""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None,
        # 不设 XUWEN_API_KEY → 首次模式触发
        api_auth_required=True,
        config_ui_localhost_only=False,
        config_ui_uploads_dir=tmp_path / "uploads",
        lance_db_path=tmp_path / "lance",
        persona_data_dir=tmp_path / "persona",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200, "首次模式不应该让 /healthz 鉴权失败"
        assert r.json()["status"] == "ok"


def test_backup_restore_rejects_path_traversal(cfg_settings, tmp_path) -> None:
    """备份恢复必须只允许 .env-backups/ 里的文件，拒绝路径穿越。"""
    # 先准备一个合法备份
    (tmp_path / ".env").write_text("SELF_NAME=Old\n", encoding="utf-8")
    backup_dir = tmp_path / ".env-backups"
    backup_dir.mkdir()
    legit = backup_dir / ".env.bak.20260101-010101"
    legit.write_text("SELF_NAME=Legit\n", encoding="utf-8")
    # 准备一个 .env 同级的"敏感文件"，验证不能被拿来覆盖 .env
    (tmp_path / "secret.txt").write_text("PRIVATE", encoding="utf-8")

    app = create_app(cfg_settings)
    token = _extract_token(app)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {token}"}
        # 路径穿越：尝试用相对路径覆盖
        for evil in ("../secret.txt", "../../etc/passwd", "secret.txt"):
            r = client.post("/config/backups/restore", headers=h, json={"name": evil})
            assert r.status_code == 404, f"必须拒绝 {evil}"
        # 合法名仍然可用
        r = client.post(
            "/config/backups/restore",
            headers=h,
            json={"name": legit.name},
        )
        assert r.status_code == 200
        assert (tmp_path / ".env").read_text(encoding="utf-8") == "SELF_NAME=Legit\n"
