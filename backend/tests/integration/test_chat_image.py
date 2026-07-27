"""chat_api 多模态/图片相关集成测试。"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from xuwen.chat_api.app import create_app
from xuwen.config import Settings

_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAGklEQVQYV2P8z8DwHwAFBQIAJaPCNgAA"
    "AABJRU5ErkJggg=="
)
_TINY_PNG_DATA_URL = f"data:image/png;base64,{_TINY_PNG_B64}"


@pytest.fixture()
def settings_vlm_fallback(tmp_path) -> Settings:
    """主模型不支持视觉，需要走 VLM fallback。"""
    return Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        image_data_dir=tmp_path / "images",
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        relationship_type="friend",
        chat_model="gpt-4o-mini",
        openai_base_url="https://llm.test/v1",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        embedding_api_url="https://embedding.test/v1",
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        api_auth_required=False,
        vision_enabled=True,
        chat_model_supports_vision=False,
        vision_api_url="https://vlm.test/v1",
        vision_api_key="sk-test",  # type: ignore[arg-type]
        vision_model="qwen-vl-plus",
        enable_pii_redaction=False,
    )


@pytest.fixture()
def settings_native_vision(tmp_path) -> Settings:
    """主模型原生支持视觉，不需要 VLM 中转。"""
    return Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        image_data_dir=tmp_path / "images",
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        relationship_type="friend",
        chat_model="gpt-4o",
        openai_base_url="https://llm.test/v1",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        embedding_api_url="https://embedding.test/v1",
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        api_auth_required=False,
        vision_enabled=True,
        chat_model_supports_vision=True,
        enable_pii_redaction=False,
    )


def _embedding_handler(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.read())
    input_val = body["input"]
    n = len(input_val) if isinstance(input_val, list) else 1
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": [0.1 * (i + 1)] * 8}
                for i in range(n)
            ],
            "model": body.get("model", "test"),
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        },
    )


def test_chat_with_image_via_vlm_fallback(settings_vlm_fallback: Settings):
    """主模型不支持视觉时：先 VLM 描述图片，再纯文本送主模型。"""
    app = create_app(settings_vlm_fallback)
    captured_payload: dict[str, Any] = {}

    def _vlm_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "一张深蓝色小方块"}}],
            },
        )

    def _llm_handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(req.read()))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "看到啦，是一张小图"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "gpt-4o-mini",
            },
        )

    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_handler)
        router.post("https://vlm.test/v1/chat/completions").mock(side_effect=_vlm_handler)
        router.post("https://llm.test/v1/chat/completions").mock(side_effect=_llm_handler)
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "这是什么"},
                                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URL}},
                            ],
                        }
                    ],
                    "stream": False,
                },
            )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "看到啦，是一张小图"

    # 主模型应该只收到文本（含 VLM 描述）
    user_message = captured_payload["messages"][-1]
    assert user_message["role"] == "user"
    assert isinstance(user_message["content"], str)
    assert "深蓝色" in user_message["content"]


def test_chat_with_image_passthrough_when_supported(settings_native_vision: Settings):
    """主模型原生支持视觉时：图片应原样转发，不调 VLM。"""
    app = create_app(settings_native_vision)
    captured_payload: dict[str, Any] = {}

    def _llm_handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(req.read()))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "看到了"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "gpt-4o",
            },
        )

    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_handler)
        router.post("https://llm.test/v1/chat/completions").mock(side_effect=_llm_handler)
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "看这个"},
                                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URL}},
                            ],
                        }
                    ],
                    "stream": False,
                },
            )

    assert r.status_code == 200, r.text
    # 主模型收到了 multimodal content（list 形式）
    user_message = captured_payload["messages"][-1]
    assert isinstance(user_message["content"], list)
    types = [part["type"] for part in user_message["content"]]
    assert "image_url" in types


def test_chat_with_image_rejected_when_disabled(tmp_path):
    """vision_enabled=false 时发图应返回 400。"""
    settings = Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        image_data_dir=tmp_path / "images",
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        api_auth_required=False,
        vision_enabled=False,
    )
    app = create_app(settings)
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "看"},
                                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URL}},
                            ],
                        }
                    ],
                    "stream": False,
                },
            )
    assert r.status_code == 400
    assert "VISION_ENABLED" in r.json()["detail"]


def test_images_endpoint_returns_persisted_image(settings_native_vision: Settings):
    """落盘的图片可以通过 /images/{sha} 取回。"""
    from xuwen.chat_api.image_store import save_data_url

    app = create_app(settings_native_vision)
    ref = save_data_url(_TINY_PNG_DATA_URL, settings_native_vision)
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            r = client.get(f"/images/{ref.sha}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == base64.b64decode(_TINY_PNG_B64)


def test_images_endpoint_rejects_bad_sha(settings_native_vision: Settings):
    app = create_app(settings_native_vision)
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            # 路径穿越尝试
            r = client.get("/images/../../etc/passwd")
    assert r.status_code in (400, 404)
