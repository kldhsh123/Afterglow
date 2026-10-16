"""Language contracts across HTTP middleware, validation, streams and fallbacks."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from string import Formatter

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from xuwen import error_i18n
from xuwen.api_i18n import request_language
from xuwen.chat_api.middleware import install_exception_handlers, install_middleware
from xuwen.config import Settings
from xuwen.core.errors import EmbeddingError
from xuwen.error_i18n import error_text
from xuwen.prompt_language import language_scope


@pytest.mark.parametrize(
    ("header", "default", "expected"),
    [
        (None, "en", "en"),
        (None, "zh-CN", "zh-CN"),
        ("zh-TW, en;q=0.8", "en", "zh-CN"),
        ("zh-HK;q=0.5,en-US;q=0.9", "zh-CN", "en"),
        ("zh;q=0,en;q=1", "zh-CN", "en"),
        ("zh;q=bad,en;q=0.5", "zh-CN", "en"),
        ("enough,zh;q=0.5", "en", "zh-CN"),
    ],
)
def test_request_language_negotiation(header, default, expected):
    assert request_language(header, default=default) == expected


def test_error_catalog_failure_keeps_fixed_readable_copy(monkeypatch):
    monkeypatch.setattr(error_i18n, "_catalog", lambda _: {})
    with language_scope("zh-CN"):
        assert error_text("Upload failed: {name}", name="对话.json") == "Upload failed: 对话.json"
    monkeypatch.setattr(
        error_i18n, "_catalog", lambda _: {"Upload failed: {name}": "失败：{missing}"}
    )
    assert error_text("Upload failed: {name}", name="chat.json") == "Upload failed: chat.json"


@pytest.mark.parametrize("folder", ["errors", "content"])
def test_catalogs_preserve_interpolation_contracts(folder):
    root = Path(error_i18n.__file__).parent / "locales" / folder
    english = json.loads((root / "en.json").read_text(encoding="utf-8"))
    chinese = json.loads((root / "zh-CN.json").read_text(encoding="utf-8"))
    assert english.keys() == chinese.keys()
    formatter = Formatter()

    def fields(value):
        return sorted((name, spec, conversion) for _, name, spec, conversion in formatter.parse(value) if name is not None)

    for key, value in english.items():
        assert fields(value) == fields(chinese[key]), key
        percent_fields = r"%(?:\([^)]+\))?[#0 +\-]?\d*(?:\.\d+)?[a-zA-Z%]"
        assert re.findall(percent_fields, value) == re.findall(percent_fields, chinese[key]), key


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_backend_language_loads_from_dotenv(tmp_path, monkeypatch, language):
    path = tmp_path / ".env"
    path.write_text(f"XUWEN_LANGUAGE={language}\n", encoding="utf-8")
    assert Settings(_env_file=path).xuwen_language == language
    monkeypatch.setenv("XUWEN_LANGUAGE", "en")
    assert Settings(_env_file=path).xuwen_language == "en"


def test_proactive_fallback_uses_configured_language_over_request_language():
    from xuwen.chat_api.routes.companion import _fallback_proactive_opening
    from xuwen.companion.life import LifeSnapshot

    life = LifeSnapshot(
        date="2026-10-16", time_slot="上午", current_activity="休息", recent_meal="",
        mood="", topic_seed="", availability="available", next_update_at="",
        reply_delay_seconds=0, reply_delay_reason="",
    )
    with language_scope("zh-CN"):
        english = _fallback_proactive_opening(life, language="en")
    with language_scope("en"):
        chinese = _fallback_proactive_opening(life, language="zh-CN")
    assert english == "If you have a moment, can I ask you something?"
    assert "有空的话" in chinese


class _Payload(BaseModel):
    count: int


def _app(default_language="en"):
    app = FastAPI()
    install_middleware(app, Settings(_env_file=None, api_auth_required=False, xuwen_language=default_language))
    install_exception_handlers(app)

    @app.get("/failure")
    async def failure():
        await asyncio.sleep(0)
        raise EmbeddingError(error_text("Retry loop exited unexpectedly"))

    @app.post("/validate")
    async def validate(payload: _Payload):
        return {"count": payload.count}

    @app.get("/stream")
    async def stream():
        async def chunks():
            await asyncio.sleep(0)
            yield error_text("Retry loop exited unexpectedly")

        return StreamingResponse(chunks())

    return app


async def test_concurrent_requests_and_streams_keep_their_own_language():
    transport = httpx.ASGITransport(app=_app("zh-CN"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        english, chinese, stream = await asyncio.gather(
            client.get("/failure", headers={"Accept-Language": "en"}),
            client.get("/failure"),
            client.get("/stream", headers={"Accept-Language": "en"}),
        )
    assert english.status_code == chinese.status_code == 502
    assert english.json()["error"]["message"] == "Retry loop exited unexpectedly"
    assert chinese.json()["error"]["message"] == "意外的重试退出"
    assert english.headers["x-request-id"] != chinese.headers["x-request-id"]
    assert stream.text == "Retry loop exited unexpectedly"


@pytest.mark.parametrize(("language", "message"), [("en", "This field is required"), ("zh-CN", "此项为必填项")])
async def test_validation_errors_use_the_request_language(language, message):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post("/validate", json={}, headers={"Accept-Language": language})
    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == message
