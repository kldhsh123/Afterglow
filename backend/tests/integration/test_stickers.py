"""表情包 store + API 单测。"""

from __future__ import annotations

import base64

import pytest
import respx
from fastapi.testclient import TestClient

from xuwen.chat_api.app import create_app
from xuwen.chat_api.sticker_store import (
    StickerError,
    StickerStore,
    find_sticker_tokens,
    render_sticker_block_for_prompt,
)
from xuwen.config import Settings

_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAGklEQVQYV2P8z8DwHwAFBQIAJaPCNgAA"
    "AABJRU5ErkJggg=="
)
_TINY_PNG = f"data:image/png;base64,{_TINY_PNG_B64}"
_TINY_PNG2_B64 = base64.b64encode(b"DIFFERENT_BUT_VALID_FAKE_PNG_BYTES").decode()
_TINY_PNG2 = f"data:image/png;base64,{_TINY_PNG2_B64}"


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        image_data_dir=tmp_path / "images",
        sticker_data_dir=tmp_path / "stickers",
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        api_auth_required=False,
        sticker_max_image_bytes=1024 * 1024,
    )


# ---------------------------------------------------------------------------
# Store 单测
# ---------------------------------------------------------------------------


def test_store_add_and_get(settings: Settings):
    store = StickerStore(settings)
    s = store.add(name="嘿嘿", description="开心打趣", data_url=_TINY_PNG)
    assert s.name == "嘿嘿"
    assert s.owner == "shared"
    assert store.get("嘿嘿") is not None
    assert store.image_path("嘿嘿") is not None


def test_store_name_validation(settings: Settings):
    store = StickerStore(settings)
    with pytest.raises(StickerError):
        store.add(name="", description="x", data_url=_TINY_PNG)
    with pytest.raises(StickerError):
        store.add(name="bad name!", description="x", data_url=_TINY_PNG)


def test_store_description_required(settings: Settings):
    store = StickerStore(settings)
    with pytest.raises(StickerError):
        store.add(name="ok", description=" ", data_url=_TINY_PNG)


def test_store_reject_non_image(settings: Settings):
    store = StickerStore(settings)
    with pytest.raises(StickerError):
        store.add(
            name="bad", description="x",
            data_url="data:text/plain;base64,YQ==",
        )


def test_store_reject_oversize(tmp_path):
    settings = Settings(
        embedding_dim=8,
        sticker_data_dir=tmp_path / "stickers",
        sticker_max_image_bytes=10,  # 极小
    )
    store = StickerStore(settings)
    with pytest.raises(StickerError):
        store.add(name="x", description="y", data_url=_TINY_PNG)


def test_store_update_and_delete(settings: Settings):
    store = StickerStore(settings)
    store.add(name="a", description="开心", data_url=_TINY_PNG)
    store.update("a", description="调皮", tags=["俏皮", "日常"])
    s = store.get("a")
    assert s.description == "调皮"
    assert s.tags == ["俏皮", "日常"]

    assert store.delete("a") is True
    assert store.get("a") is None
    assert store.delete("a") is False


def test_store_list_by_owner(settings: Settings):
    store = StickerStore(settings)
    store.add(name="ai_only", description="x", data_url=_TINY_PNG, owner="ai")
    store.add(name="self_only", description="x", data_url=_TINY_PNG2, owner="self")
    store.add(name="shared", description="x", data_url=_TINY_PNG, owner="shared")
    ai_list = store.list_all(owner="ai")
    names = {s.name for s in ai_list}
    # ai 视角能看见 ai + shared
    assert "ai_only" in names
    assert "shared" in names
    assert "self_only" not in names


def test_store_available_for_ai_excludes_self_only(settings: Settings):
    store = StickerStore(settings)
    store.add(name="for_ai", description="x", data_url=_TINY_PNG, owner="ai")
    store.add(name="for_me", description="x", data_url=_TINY_PNG2, owner="self")
    avail = store.available_for_ai()
    names = {s.name for s in avail}
    assert "for_ai" in names
    assert "for_me" not in names


def test_persistence_across_instances(settings: Settings):
    store1 = StickerStore(settings)
    store1.add(name="嘿嘿", description="x", data_url=_TINY_PNG)
    store2 = StickerStore(settings)
    assert store2.get("嘿嘿") is not None


# ---------------------------------------------------------------------------
# token 扫描 + prompt 渲染
# ---------------------------------------------------------------------------


def test_find_sticker_tokens_basic():
    text = "好的 [sticker:嘿嘿] 你来啦 [sticker:摸摸头] 真好"
    tokens = find_sticker_tokens(text)
    assert len(tokens) == 2
    assert tokens[0][2] == "嘿嘿"
    assert tokens[1][2] == "摸摸头"


def test_find_sticker_tokens_handles_no_match():
    assert find_sticker_tokens("没有表情包的句子") == []


def test_render_sticker_block_for_prompt(settings: Settings):
    store = StickerStore(settings)
    store.add(name="嘿嘿", description="开心打趣", data_url=_TINY_PNG)
    store.add(name="摸摸头", description="安慰", data_url=_TINY_PNG2)
    block = render_sticker_block_for_prompt(store.available_for_ai())
    assert "[sticker:嘿嘿]" in block
    assert "[sticker:摸摸头]" in block
    assert "开心打趣" in block


def test_render_sticker_block_empty():
    # 即使没有可用 sticker 也要返回一段警告，提醒模型"不要自创输出"
    block = render_sticker_block_for_prompt([])
    assert "绝对不要" in block
    assert "sticker" in block


# ---------------------------------------------------------------------------
# API 集成
# ---------------------------------------------------------------------------


def test_api_create_list_delete(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        # 列表初始为空
        r = client.get("/v1/stickers")
        assert r.status_code == 200
        assert r.json()["items"] == []

        # 创建
        r = client.post(
            "/v1/stickers",
            json={
                "name": "嘿嘿",
                "description": "开心打趣",
                "data_url": _TINY_PNG,
                "owner": "shared",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "嘿嘿"
        assert body["image_url"] == "/v1/stickers/嘿嘿/image"

        # 重复创建应失败
        r = client.post(
            "/v1/stickers",
            json={"name": "嘿嘿", "description": "x", "data_url": _TINY_PNG},
        )
        assert r.status_code == 409

        # 取图片
        r = client.get("/v1/stickers/嘿嘿/image")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

        # 列表能看到
        r = client.get("/v1/stickers")
        assert len(r.json()["items"]) == 1

        # 更新
        r = client.patch(
            "/v1/stickers/嘿嘿",
            json={"description": "新的说明", "tags": ["俏皮"]},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "新的说明"
        assert r.json()["tags"] == ["俏皮"]

        # 删除
        r = client.delete("/v1/stickers/嘿嘿")
        assert r.status_code == 200
        r = client.delete("/v1/stickers/嘿嘿")
        assert r.status_code == 404


def test_api_reject_invalid_data_url(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.post(
            "/v1/stickers",
            json={
                "name": "bad",
                "description": "x",
                "data_url": "not-a-data-url",
            },
        )
        assert r.status_code == 400


def test_api_image_endpoint_requires_key(tmp_path):
    """表情包图片也可能泄露私有素材，开启 XUWEN_API_KEY 后同样要鉴权。"""
    settings = Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        image_data_dir=tmp_path / "images",
        sticker_data_dir=tmp_path / "stickers",
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        xuwen_api_key="secret-local",  # type: ignore[arg-type]
    )
    # 先用 store 直接放一张
    store = StickerStore(settings)
    store.add(name="x", description="y", data_url=_TINY_PNG)

    app = create_app(settings)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        # 图片端点无 token 也拒绝
        r = client.get("/v1/stickers/x/image")
        assert r.status_code == 401
        # 带 token 才能拿
        r = client.get("/v1/stickers/x/image", headers={"x-api-key": "secret-local"})
        assert r.status_code == 200
        # 元数据端点需要 token
        r = client.get("/v1/stickers")
        assert r.status_code == 401
