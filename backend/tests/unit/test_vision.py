"""图片存储 + VisionClient 单测。"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from xuwen.chat_api.image_store import (
    ImageError,
    data_url_for,
    find_by_sha,
    save_data_url,
    validate_data_url,
)
from xuwen.chat_api.vision_client import VisionClient, VisionError
from xuwen.config import Settings

# 4×4 像素的 PNG（最小合法 PNG）
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAGklEQVQYV2P8z8DwHwAFBQIAJaPCNgAA"
    "AABJRU5ErkJggg=="
)
_TINY_PNG_DATA_URL = f"data:image/png;base64,{_TINY_PNG_B64}"


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        image_data_dir=tmp_path / "images",
        vision_enabled=True,
        vision_api_url="https://vlm.test/v1",
        vision_api_key="sk-test",  # type: ignore[arg-type]
        vision_model="qwen-vl-plus",
        vision_max_image_bytes=1024 * 1024,
    )


def test_validate_data_url_accepts_png(settings: Settings):
    mime, raw = validate_data_url(_TINY_PNG_DATA_URL, settings)
    assert mime == "png"
    assert raw == base64.b64decode(_TINY_PNG_B64)


def test_validate_data_url_rejects_non_image(settings: Settings):
    with pytest.raises(ImageError):
        validate_data_url("data:text/plain;base64,YQ==", settings)


def test_validate_data_url_rejects_oversize():
    big = "data:image/png;base64," + base64.b64encode(b"x" * 2048).decode()
    settings = Settings(
        embedding_dim=8,
        vision_max_image_bytes=512,  # 小于 2048 字节
    )
    with pytest.raises(ImageError):
        validate_data_url(big, settings)


def test_save_data_url_deduplicates(settings: Settings):
    ref1 = save_data_url(_TINY_PNG_DATA_URL, settings)
    ref2 = save_data_url(_TINY_PNG_DATA_URL, settings)
    # 同一内容 SHA 相同，文件只写一次
    assert ref1.sha == ref2.sha
    assert ref1.path == ref2.path
    assert ref1.path.exists()


def test_find_by_sha_roundtrip(settings: Settings):
    ref = save_data_url(_TINY_PNG_DATA_URL, settings)
    found = find_by_sha(ref.sha, settings)
    assert found is not None
    assert found == ref.path

    url = data_url_for(ref.sha, settings)
    assert url.startswith("data:image/png;base64,")


def test_find_by_sha_missing(settings: Settings):
    assert find_by_sha("a" * 64, settings) is None


@pytest.mark.asyncio
async def test_vision_client_preserves_default_http_timeout(settings: Settings):
    """路由预算不应缩短 VisionClient 供直接调用方使用的 HTTP 超时。"""
    settings.vision_timeout_seconds = 15.0
    client = VisionClient(settings)
    try:
        assert client._client.timeout.connect == 10.0
        assert client._client.timeout.read == 60.0
        assert client._client.timeout.write == 60.0
        assert client._client.timeout.pool == 60.0
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_vision_client_allows_explicit_http_timeout_override(settings: Settings):
    client = VisionClient(settings, timeout_seconds=25.0)
    try:
        assert client._client.timeout.connect == 10.0
        assert client._client.timeout.read == 25.0
        assert client._client.timeout.write == 25.0
        assert client._client.timeout.pool == 25.0
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_vision_client_describes_image(settings: Settings):
    async with httpx.AsyncClient() as raw:
        client = VisionClient(settings, client=raw)
        with respx.mock(base_url="https://vlm.test/v1") as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": "一张深蓝色的小方块"}, "finish_reason": "stop"}
                        ]
                    },
                )
            )
            descs = await client.describe_images([_TINY_PNG_DATA_URL])
    assert descs == ["一张深蓝色的小方块"]


@pytest.mark.asyncio
async def test_vision_client_handles_list_content(settings: Settings):
    """部分 VLM（Qwen-VL）的 content 可能是 list[{type:text,text:...}]，要兼容。"""
    async with httpx.AsyncClient() as raw:
        client = VisionClient(settings, client=raw)
        with respx.mock(base_url="https://vlm.test/v1") as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "content": [
                                        {"type": "text", "text": "夜空"},
                                        {"type": "text", "text": "里的月亮"},
                                    ]
                                }
                            }
                        ]
                    },
                )
            )
            descs = await client.describe_images([_TINY_PNG_DATA_URL])
    assert descs == ["夜空里的月亮"]


@pytest.mark.asyncio
async def test_vision_client_swallows_single_image_failure(settings: Settings):
    """单张图片识别失败应返回占位，不影响其它张。"""
    async with httpx.AsyncClient() as raw:
        client = VisionClient(settings, client=raw)
        with respx.mock(base_url="https://vlm.test/v1") as router:
            # 第一次成功，第二次报 400
            seq = iter(
                [
                    httpx.Response(
                        200,
                        json={"choices": [{"message": {"content": "ok"}}]},
                    ),
                    httpx.Response(400, text="bad image"),
                ]
            )
            router.post("/chat/completions").mock(side_effect=lambda req: next(seq))
            descs = await client.describe_images([_TINY_PNG_DATA_URL, _TINY_PNG_DATA_URL])
    assert descs[0] == "ok"
    assert "识别失败" in descs[1]


@pytest.mark.asyncio
async def test_vision_client_does_not_leak_response_body(settings: Settings):
    async with httpx.AsyncClient() as raw:
        client = VisionClient(settings, client=raw)
        with respx.mock(base_url="https://vlm.test/v1") as router:
            sensitive = "SECRET_VLM_INTERNAL_PROMPT"
            router.post("/chat/completions").mock(
                return_value=httpx.Response(401, text=sensitive)
            )
            # 错误被 describe_images 内部捕获并替换为占位
            descs = await client.describe_images([_TINY_PNG_DATA_URL])
            assert "识别失败" in descs[0]
            assert sensitive not in descs[0]


@pytest.mark.asyncio
async def test_vision_client_constructor_handles_url_with_suffix():
    """如果用户把 /chat/completions 写进 vision_api_url 也应该工作。"""
    settings = Settings(
        embedding_dim=8,
        vision_enabled=True,
        vision_api_url="https://vlm.test/v1/chat/completions",
        vision_api_key="sk-x",  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient() as raw:
        client = VisionClient(settings, client=raw)
        assert client._url == "https://vlm.test/v1/chat/completions"


@pytest.mark.asyncio
async def test_vision_client_propagates_4xx_via_exception(settings: Settings):
    """当 describe_one 直接被调用时 4xx 应抛 VisionError。"""
    async with httpx.AsyncClient() as raw:
        client = VisionClient(settings, client=raw)
        with respx.mock(base_url="https://vlm.test/v1") as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(401, text="invalid key")
            )
            with pytest.raises(VisionError):
                await client._describe_one(_TINY_PNG_DATA_URL)
