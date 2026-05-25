"""配置 WebUI 的 HTTP 路由。

所有路径都挂在主 app 的 config_ui_path_prefix（默认 /config）下。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from xuwen.config import Settings
from xuwen.web_ui.connectivity import (
    TestResult,
    test_embedding,
    test_openai_chat,
)
from xuwen.web_ui.env_io import (
    list_backups,
    load_env,
    restore_backup,
    write_env_atomic,
)
from xuwen.web_ui.import_tasks import (
    get_manager,
    run_import_task,
    sse_stream,
)
from xuwen.web_ui.inspect_file import inspect_chat_file
from xuwen.web_ui.presets import (
    CHAT_PRESETS,
    EMBEDDING_PRESETS,
    LABEL_PRESETS,
    Preset,
)
from xuwen.web_ui.schema import FieldMeta, build_schema

router = APIRouter(tags=["config-ui"])


def _settings_dep(request: Request) -> Settings:
    """从子 app state 取 Settings（在 create_config_app 中注入）。"""
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        return settings
    from xuwen.config import get_settings

    return get_settings()


def _env_path(settings: Settings) -> Path:
    """定位 backend/.env。

    settings.model_config.env_file 默认就是 .env，相对当前工作目录。
    """
    return Path(".env").resolve()


def _example_path() -> Path:
    return Path(".env.example").resolve()


# ---------- 元信息 ----------


@router.get("/ping")
def ping() -> dict[str, Any]:
    """配置 UI 心跳，前端用来探活。无需鉴权。"""
    return {"ok": True, "ts": int(time.time())}


@router.get("/status")
def status(settings: Settings = Depends(_settings_dep)) -> dict[str, Any]:
    """整体配置状态：是否已完成基本配置、向导是否需要走。"""
    env = load_env(_env_path(settings))
    identity_ok = bool(
        env.get("SELF_NAME")
        and env.get("SELF_UID")
        and env.get("FRIEND_NAME")
        and env.get("FRIEND_UID")
    )
    chat_ok = bool(env.get("OPENAI_API_KEY") and env.get("CHAT_MODEL"))
    embedding_ok = bool(env.get("EMBEDDING_API_KEY") and env.get("EMBEDDING_MODEL"))
    auth_ok = bool(env.get("XUWEN_API_KEY"))
    return {
        "identity_ok": identity_ok,
        "chat_ok": chat_ok,
        "embedding_ok": embedding_ok,
        "auth_ok": auth_ok,
        "wizard_completed": identity_ok and chat_ok and embedding_ok and auth_ok,
        "env_path": str(_env_path(settings)),
        "example_path": str(_example_path()),
    }


# ---------- Schema ----------


@router.get("/schema")
def get_schema() -> dict[str, Any]:
    snap = build_schema(_example_path())
    return {
        "groups": snap.groups,
        "fields": [_field_to_dict(f) for f in snap.fields],
    }


def _field_to_dict(f: FieldMeta) -> dict[str, Any]:
    return {
        "key": f.key,
        "type": f.type,
        "group": f.group,
        "title": f.title,
        "description": f.description,
        "default": f.default,
        "required": f.required,
        "secret": f.secret,
        "advanced": f.advanced,
        "choices": f.choices,
    }


# ---------- 值读取 / 写入 ----------


def _mask_secret(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return "*" * (len(v) - 4) + v[-4:]


@router.get("/values")
def get_values(settings: Settings = Depends(_settings_dep)) -> dict[str, Any]:
    """读取当前 .env 值。secret 字段仅返回 set + 末 4 位预览。"""
    env = load_env(_env_path(settings))
    schema = build_schema(_example_path())
    secret_keys = {f.key for f in schema.fields if f.secret}

    out: dict[str, Any] = {}
    for f in schema.fields:
        v = env.get(f.key)
        if v is None:
            out[f.key] = {"set": False, "value": None}
            continue
        if f.key in secret_keys:
            out[f.key] = {"set": bool(v), "preview": _mask_secret(v)}
        else:
            out[f.key] = {"set": True, "value": v}
    return {"values": out}


class UpdateValuesPayload(BaseModel):
    values: dict[str, str]
    dry_run: bool = False


@router.put("/values")
def put_values(
    payload: UpdateValuesPayload,
    request: Request,
    settings: Settings = Depends(_settings_dep),
) -> dict[str, Any]:
    """更新 .env。
    1) 白名单过滤（必须在 schema 内）
    2) 合并到现有 .env
    3) 试构造 Settings 校验
    4) 通过则备份 + 原子写
    5) 热重载：把最新 settings 注入子 app state，让后续请求（如导入）拿到新值
    """
    schema = build_schema(_example_path())
    allowed = {f.key for f in schema.fields}
    secret_keys = {f.key for f in schema.fields if f.secret}

    incoming = {k: v for k, v in payload.values.items() if k in allowed}
    rejected = sorted(set(payload.values) - allowed)

    env_path = _env_path(settings)
    # 若 .env 尚不存在（首次配置），基于 .env.example 复制为模板，
    # 这样生成出来的 .env 保留全部注释和分组，对后续手改也更友好。
    if not env_path.exists() and _example_path().exists():
        doc = load_env(_example_path())
    else:
        doc = load_env(env_path)

    # secret 字段如果传入是 mask（含 *）则跳过，保留原值
    for key, value in incoming.items():
        if key in secret_keys and "*" in value and not value.startswith("sk-"):
            continue
        doc.set(key, value)

    # 用合并后的 dict 试构造 Settings
    merged: dict[str, Any] = {ln.key: ln.value for ln in doc.lines if ln.kind == "assign"}
    try:
        Settings(**{k.lower(): v for k, v in merged.items()})
    except ValidationError as e:
        return {
            "ok": False,
            "errors": [
                {
                    "field": ".".join(str(x) for x in err.get("loc", [])).upper(),
                    "message": err.get("msg", ""),
                }
                for err in e.errors()
            ],
            "rejected_keys": rejected,
        }

    if payload.dry_run:
        return {"ok": True, "dry_run": True, "preview": doc.render(), "rejected_keys": rejected}

    backup = write_env_atomic(env_path, doc, backup=True)

    # 热重载：清掉 LRU 缓存 + 重新构造 settings，让后续请求拿到最新 .env
    # 否则配置 UI 内的"启动导入"等流程仍会用启动时的旧 settings，
    # 出现"用户已填字段 → 导入仍报 require_identity 失败"的现象。
    # 但是 CONFIG_UI_* 字段决定本进程的路由 / 鉴权 / 监听行为，
    # 改了 .env 也得重启才合理 → 这里强行保留启动时的值，避免热重载把自己锁死。
    from xuwen.config import get_settings

    get_settings.cache_clear()
    new_settings = Settings()
    new_settings.config_ui_enabled = settings.config_ui_enabled
    new_settings.config_ui_path_prefix = settings.config_ui_path_prefix
    new_settings.config_ui_localhost_only = settings.config_ui_localhost_only
    new_settings.config_ui_setup_token = settings.config_ui_setup_token
    new_settings.config_ui_uploads_dir = settings.config_ui_uploads_dir
    request.app.state.settings = new_settings

    return {
        "ok": True,
        "restart_required": True,
        "backup": str(backup) if backup else None,
        "rejected_keys": rejected,
    }


# ---------- 预设 ----------


def _preset_to_dict(p: Preset) -> dict[str, Any]:
    return {
        "id": p.id,
        "label": p.label,
        "base_url": p.base_url,
        "default_model": p.default_model,
        "apply_url": p.apply_url,
        "hint": p.hint,
    }


@router.get("/presets")
def get_presets() -> dict[str, Any]:
    return {
        "chat": [_preset_to_dict(p) for p in CHAT_PRESETS],
        "embedding": [_preset_to_dict(p) for p in EMBEDDING_PRESETS],
        "label": [_preset_to_dict(p) for p in LABEL_PRESETS],
    }


# ---------- 连通性测试 ----------


class TestChatPayload(BaseModel):
    base_url: str
    api_key: str
    model: str


class TestEmbeddingPayload(BaseModel):
    base_url: str
    api_key: str
    model: str
    input_mode: str = "array"
    send_dimensions: bool = True
    dim: int | None = None


def _test_result_to_dict(r: TestResult) -> dict[str, Any]:
    return {"ok": r.ok, "message": r.message, "detail": r.detail, "extra": r.extra}


@router.post("/test/chat")
async def post_test_chat(payload: TestChatPayload) -> dict[str, Any]:
    result = await test_openai_chat(payload.base_url, payload.api_key, payload.model)
    return _test_result_to_dict(result)


@router.post("/test/embedding")
async def post_test_embedding(payload: TestEmbeddingPayload) -> dict[str, Any]:
    result = await test_embedding(
        payload.base_url,
        payload.api_key,
        payload.model,
        input_mode=payload.input_mode,
        send_dimensions=payload.send_dimensions,
        dim=payload.dim,
    )
    return _test_result_to_dict(result)


# ---------- 随机生成 ----------


@router.post("/generate/api-key")
def generate_api_key() -> dict[str, str]:
    """生成一个 32 字节长的随机 token 供 XUWEN_API_KEY 使用。"""
    return {"token": secrets.token_urlsafe(32)}


# ---------- 文件上传 + 导入 ----------


_MAX_UPLOAD_BYTES = 300 * 1024 * 1024  # 300MB


@router.post("/import/inspect")
async def inspect_upload(
    settings: Settings = Depends(_settings_dep),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """单文件嗅探：读 JSON 顶部识别格式和双方候选身份，不入库不保留。

    用于配置向导第 1 步"从聊天文件识别"按钮。返回结构：
        {
          "format": "qqexporter_v5" | "wechat_weflow" | "unknown",
          "total_messages": int,
          "candidates": [{ name, uid, role_hint: "self"/"friend"/"unknown" }, ...],
          "error": str | ""
        }
    """
    upload_dir = Path(settings.config_ui_uploads_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 写到临时位置（嗅探完保留即可，反正用户接下来通常会用 /import/upload 再传一次正式入库）
    # 但为避免重复占盘，inspect 用单独的 tmp 文件，5 分钟过期由调用方清理
    ts = int(time.time() * 1000)
    unique = secrets.token_urlsafe(6)
    safe_name = Path(file.filename or "inspect.json").name
    dest = upload_dir / f"inspect_{ts}_{unique}_{safe_name}"
    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"{safe_name} 超过最大允许大小（{_MAX_UPLOAD_BYTES // (1024 * 1024)}MB）",
                    )
                out.write(chunk)
        result = inspect_chat_file(dest)
    finally:
        # 嗅探结束立即清理临时文件；正式导入走 /import/upload
        dest.unlink(missing_ok=True)

    return {
        "format": result.format,
        "total_messages": result.total_messages,
        "candidates": [
            {"name": c.name, "uid": c.uid, "role_hint": c.role_hint}
            for c in result.candidates
        ],
        "error": result.error,
    }


@router.post("/import/upload")
async def upload_files(
    settings: Settings = Depends(_settings_dep),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """上传一个或多个聊天记录 JSON。仅保存到磁盘，不触发导入。

    每个文件同时做一次顶部嗅探，返回：消息数、格式识别结果、双方身份候选。
    这样前端在向导第 1 步选文件时一次请求就能拿到全部信息（持久上传 + 身份识别）。
    """
    upload_dir = Path(settings.config_ui_uploads_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for f in files:
        # 用 uuid 后缀避免一次循环里同毫秒 ts 撞名（多文件同名 / 极快循环都可能撞）
        ts = int(time.time() * 1000)
        unique = secrets.token_urlsafe(6)
        safe_name = Path(f.filename or "upload.json").name
        dest = upload_dir / f"{ts}_{unique}_{safe_name}"
        size = 0
        with dest.open("wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"{safe_name} 超过最大允许大小（{_MAX_UPLOAD_BYTES // (1024 * 1024)}MB）",
                    )
                out.write(chunk)

        # 嗅探：识别格式 + 双方候选
        try:
            ir = inspect_chat_file(dest)
        except Exception as e:
            results.append(
                {
                    "name": safe_name,
                    "saved_as": str(dest),
                    "size": size,
                    "format": "unknown",
                    "total_messages": 0,
                    "candidates": [],
                    "error": f"嗅探失败：{e}",
                }
            )
            continue

        results.append(
            {
                "name": safe_name,
                "saved_as": str(dest),
                "size": size,
                "format": ir.format,
                "total_messages": ir.total_messages,
                "candidates": [
                    {"name": c.name, "uid": c.uid, "role_hint": c.role_hint}
                    for c in ir.candidates
                ],
                "error": ir.error,
            }
        )
    return {"uploaded": results}


class StartImportPayload(BaseModel):
    files: list[str]  # 服务器侧绝对路径
    file_names: list[str]
    persona_source: str | None = None  # 作为画像参考的文件路径


@router.post("/import/start")
async def start_import(
    payload: StartImportPayload,
    settings: Settings = Depends(_settings_dep),
) -> dict[str, Any]:
    """启动后台导入任务。

    并发兜底：如果已经有进行中的导入任务，直接返回它的 task_id，
    防止前端疯狂点击触发多个后台 task 并发写库。
    """
    if not payload.files:
        raise HTTPException(status_code=400, detail="未提供文件")

    mgr = get_manager()
    active = mgr.list_active()
    if active:
        # 复用已有任务，告诉前端去订阅它的进度
        return {
            "task_id": active[0].task_id,
            "status": active[0].status,
            "reused": True,
        }

    upload_dir = Path(settings.config_ui_uploads_dir).resolve()
    paths: list[Path] = []
    for raw in payload.files:
        p = Path(raw).resolve()
        try:
            p.relative_to(upload_dir)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"非法路径：{raw}（仅允许 uploads 目录内的文件）",
            ) from None
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在：{raw}")
        paths.append(p)

    persona_source: Path | None = None
    if payload.persona_source:
        ps = Path(payload.persona_source).resolve()
        try:
            ps.relative_to(upload_dir)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"非法画像参考路径：{payload.persona_source}",
            ) from None
        if not ps.exists():
            raise HTTPException(status_code=404, detail=f"画像参考文件不存在：{payload.persona_source}")
        persona_source = ps

    task = mgr.create(paths, payload.file_names, persona_source=persona_source)
    handle = asyncio.create_task(run_import_task(task.task_id, settings))
    mgr.attach_handle(task.task_id, handle)
    return {"task_id": task.task_id, "status": task.status, "reused": False}


@router.get("/import")
def list_import_tasks() -> dict[str, Any]:
    """列出所有未结束的导入任务，前端用来在刷新页面后恢复跟踪。"""
    mgr = get_manager()
    return {
        "active": [t.to_dict() for t in mgr.list_active()],
        "all": [t.to_dict() for t in mgr.list()],
    }


@router.get("/import/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    task = get_manager().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task.to_dict()


@router.get("/import/{task_id}/stream")
async def stream_task(task_id: str) -> StreamingResponse:
    """SSE 推送任务进度。"""
    return StreamingResponse(
        sse_stream(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/import/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, Any]:
    ok = await get_manager().cancel(task_id)
    return {"cancelled": ok}


# ---------- 备份与回滚 ----------


@router.get("/backups")
def get_backups(settings: Settings = Depends(_settings_dep)) -> dict[str, Any]:
    backups = list_backups(_env_path(settings))
    return {
        "backups": [
            {
                "name": p.name,
                "path": str(p),
                "mtime": int(p.stat().st_mtime),
                "size": p.stat().st_size,
            }
            for p in backups
        ]
    }


class RestorePayload(BaseModel):
    name: str


@router.post("/backups/restore")
def post_restore(
    payload: RestorePayload,
    settings: Settings = Depends(_settings_dep),
) -> dict[str, Any]:
    env_path = _env_path(settings)
    # 防路径穿越：只允许 list_backups() 返回的文件
    # 即使有 setup token，也不应让请求方读取 .env-backups/ 之外的任意文件
    allowed = {p.name: p for p in list_backups(env_path)}
    target = allowed.get(payload.name)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"指定的备份不在备份目录中：{payload.name}",
        )
    restored = restore_backup(env_path, target)
    return {"ok": True, "restored": str(restored), "restart_required": True}
