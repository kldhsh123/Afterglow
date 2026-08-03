"""LLM 客户端单测：流 / 非流 / 重试 / 错误处理。"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.config import Settings
from xuwen.core.errors import LLMError
from xuwen.core.metrics import MetricsRecorder

LLM_BASE = "https://llm.test/v1"


class BrokenSSEStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield 'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'.encode()
        raise httpx.ReadError("stream closed")


def _settings():
    return Settings(
        chat_model="gpt-4o-mini",
        openai_base_url=LLM_BASE,
        openai_api_key="sk-test",  # type: ignore[arg-type]
        self_uid="u-self",
        self_name="Me",
        friend_uid="u-friend",
        friend_name="TA",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_protocol", "expected"),
    [
        ("chat_completions", {"reasoning_effort": "low"}),
        ("responses", {"reasoning": {"effort": "low"}}),
    ],
)
async def test_build_payload_uses_protocol_specific_reasoning_field(
    api_protocol, expected
):
    settings = _settings().model_copy(update={"llm_reasoning_effort": "low"})
    async with httpx.AsyncClient() as raw:
        client = LLMClient(
            settings,
            client=raw,
            api_protocol=api_protocol,
            include_reasoning_effort=True,
        )
        payload = client._build_payload(
            [{"role": "user", "content": "hi"}],
            None,
            model=None,
            stream=False,
        )

    assert payload.items() >= expected.items()
    if api_protocol == "responses":
        assert "reasoning_effort" not in payload
    else:
        assert "reasoning" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("api_protocol", ["chat_completions", "responses"])
async def test_build_payload_omits_null_reasoning_effort(api_protocol):
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(
            settings,
            client=raw,
            api_protocol=api_protocol,
            include_reasoning_effort=True,
        )
        payload = client._build_payload(
            [{"role": "user", "content": "hi"}],
            None,
            model=None,
            stream=False,
        )

    assert "reasoning_effort" not in payload
    assert "reasoning" not in payload


@pytest.mark.asyncio
async def test_complete_chat_returns_content():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "你好呀"}}],
                        "model": "gpt-4o-mini",
                    },
                )
            )
            text = await client.complete_chat(
                messages=[{"role": "user", "content": "hi"}],
                params=GenerationParams(temperature=0.7, max_tokens=100),
            )
    assert text == "你好呀"


@pytest.mark.asyncio
async def test_complete_chat_reports_content_filter_instead_of_returning_empty_text():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {"content": ""},
                                "finish_reason": "content_filter",
                            }
                        ]
                    },
                )
            )
            with pytest.raises(LLMError, match="内容过滤") as caught:
                await client.complete_chat([{"role": "user", "content": "x"}])

    assert caught.value.detail == {
        "request_id": None,
        "reason": "content_filter",
    }


@pytest.mark.asyncio
async def test_model_chain_summary_includes_schedule_debug():
    settings = _settings()
    metrics = MetricsRecorder()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "content": (
                                        "好呀"
                                        "<schedule-hint>2分钟后叫你水开了</schedule-hint>"
                                    )
                                }
                            }
                        ],
                        "model": "gpt-4o-mini",
                    },
                )
            )
            await client.complete_chat(
                messages=[
                    {
                        "role": "system",
                        "content": "【定时任务】需要时追加 <schedule-hint>...</schedule-hint>",
                    },
                    {"role": "user", "content": "2分钟后叫我"},
                ],
                trace_id="trace-schedule",
                metrics=metrics,
            )

    record = metrics.model_chain(trace_id="trace-schedule")[0]
    assert record.request["schedule"]["prompt_has_schedule_instruction"] is True
    assert record.request["schedule"]["schedule_hint_tag_mentions"] >= 1
    assert record.response["schedule"] == {
        "hint_count": 1,
        "hint_previews": ["2分钟后叫你水开了"],
        "has_open_tag": True,
        "has_close_tag": True,
    }


@pytest.mark.asyncio
async def test_cancelled_complete_chat_is_kept_in_model_chain():
    settings = _settings()
    metrics = MetricsRecorder()
    request_started = asyncio.Event()
    never_respond = asyncio.Event()

    async def handle_request(_request: httpx.Request) -> httpx.Response:
        request_started.set()
        await never_respond.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = LLMClient(settings, client=raw)
        task = asyncio.create_task(
            client.complete_chat(
                [{"role": "user", "content": "更新当前状态"}],
                model="life-model",
                trace_id="trace-life-timeout",
                stage="life.decide",
                metrics=metrics,
            )
        )
        await asyncio.wait_for(request_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    record = metrics.model_chain(trace_id="trace-life-timeout")[0]
    assert record.stage == "life.decide"
    assert record.model == "life-model"
    assert record.status == "cancelled"
    assert record.error == "CancelledError"
    assert record.request["message_count"] == 1


@pytest.mark.asyncio
async def test_complete_chat_retries_on_5xx():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            seq = iter(
                [
                    httpx.Response(503, text="busy"),
                    httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
                ]
            )
            router.post("/chat/completions").mock(side_effect=lambda req: next(seq))
            text = await client.complete_chat([{"role": "user", "content": "x"}])
    assert text == "ok"


@pytest.mark.asyncio
async def test_complete_chat_retries_redirect_without_following_location():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            seq = iter(
                [
                    httpx.Response(
                        301,
                        headers={"location": "https://redirect.test/chat/completions"},
                    ),
                    httpx.Response(
                        200,
                        json={"choices": [{"message": {"content": "ok"}}]},
                    ),
                ]
            )
            route = router.post("/chat/completions").mock(
                side_effect=lambda req: next(seq)
            )
            text = await client.complete_chat([{"role": "user", "content": "x"}])

    assert text == "ok"
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_complete_chat_4xx_not_retried():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            route = router.post("/chat/completions").mock(
                return_value=httpx.Response(401, text="invalid key")
            )
            with pytest.raises(LLMError):
                await client.complete_chat([{"role": "user", "content": "x"}])
            assert route.call_count == 1


@pytest.mark.asyncio
async def test_complete_chat_does_not_leak_response_body():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            sensitive = "SECRET_INTERNAL_PROMPT_LEAK"
            router.post("/chat/completions").mock(
                return_value=httpx.Response(400, text=sensitive)
            )
            with pytest.raises(LLMError) as exc:
                await client.complete_chat([{"role": "user", "content": "x"}])
            assert sensitive not in str(exc.value)
            assert sensitive not in str(exc.value.detail or "")


@pytest.mark.asyncio
async def test_complete_responses_rejects_failed_status():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw, api_protocol="responses")
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/responses").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "failed",
                        "error": {"code": "server_error"},
                        "output": [],
                    },
                )
            )
            with pytest.raises(LLMError, match="Responses 请求执行失败"):
                await client.complete_chat([{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_complete_responses_refusal_raises_without_reply():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw, api_protocol="responses")
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/responses").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {"type": "refusal", "refusal": "不能处理"}
                                ],
                            }
                        ],
                    },
                )
            )
            with pytest.raises(LLMError, match="主模型拒绝处理当前请求"):
                await client.complete_chat([{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_stream_chat_yields_delta_content():
    settings = _settings()
    sse_body = (
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"呀"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    content=sse_body.encode("utf-8"),
                    headers={"content-type": "text/event-stream"},
                )
            )
            chunks: list[str] = []
            async for piece in client.stream_chat([{"role": "user", "content": "x"}]):
                chunks.append(piece)
    assert "".join(chunks) == "你好呀"


@pytest.mark.asyncio
async def test_stream_responses_refusal_raises_without_yielding_text():
    settings = _settings()
    sse_body = (
        'data: {"type":"response.refusal.delta","delta":"不能处理"}\n\n'
        'data: {"type":"response.completed","response":{"usage":{}}}\n\n'
        "data: [DONE]\n\n"
    )
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw, api_protocol="responses")
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/responses").mock(
                return_value=httpx.Response(
                    200,
                    content=sse_body.encode("utf-8"),
                    headers={"content-type": "text/event-stream"},
                )
            )
            chunks: list[str] = []
            with pytest.raises(LLMError, match="主模型拒绝处理当前请求"):
                async for piece in client.stream_chat(
                    [{"role": "user", "content": "x"}]
                ):
                    chunks.append(piece)

    assert chunks == []


@pytest.mark.asyncio
async def test_stream_chat_rejects_redirect_response():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw, max_retries=1)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(302, headers={"location": "https://other.test"})
            )
            with pytest.raises(LLMError, match="重定向"):
                async for _piece in client.stream_chat(
                    [{"role": "user", "content": "x"}]
                ):
                    pass


@pytest.mark.asyncio
async def test_stream_chat_reports_content_filter_instead_of_empty_stream():
    settings = _settings()
    sse_body = (
        'data: {"choices":[{"delta":{},"finish_reason":"content_filter"}]}\n\n'
        "data: [DONE]\n\n"
    )
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(200, content=sse_body.encode("utf-8"))
            )
            with pytest.raises(LLMError, match="内容过滤") as caught:
                async for _piece in client.stream_chat(
                    [{"role": "user", "content": "x"}]
                ):
                    pass

    assert caught.value.detail["reason"] == "content_filter"


@pytest.mark.asyncio
async def test_stream_chat_handles_malformed_chunks():
    """单条 chunk 解析失败时不影响整流；上游偶发非 JSON 行应被跳过。"""
    settings = _settings()
    sse_body = (
        "data: not-json\n\n"
        'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        "data: \n\n"
        'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(200, content=sse_body.encode("utf-8"))
            )
            chunks: list[str] = []
            async for piece in client.stream_chat([{"role": "user", "content": "x"}]):
                chunks.append(piece)
    assert "".join(chunks) == "ab"


@pytest.mark.asyncio
async def test_stream_chat_allows_empty_content_stream():
    settings = _settings()
    sse_body = (
        'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        "data: not-json\n\n"
        "data: [DONE]\n\n"
    )
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(200, content=sse_body.encode("utf-8"))
            )
            chunks: list[str] = []
            async for piece in client.stream_chat([{"role": "user", "content": "x"}]):
                chunks.append(piece)

    assert chunks == []


@pytest.mark.asyncio
async def test_stream_chat_converts_midstream_disconnect_to_llm_error():
    settings = _settings()
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, stream=BrokenSSEStream())
    )
    async with httpx.AsyncClient(transport=transport) as raw:
        client = LLMClient(settings, client=raw)
        chunks: list[str] = []
        with pytest.raises(LLMError):
            async for piece in client.stream_chat([{"role": "user", "content": "x"}]):
                chunks.append(piece)

    assert chunks == ["你"]


@pytest.mark.asyncio
async def test_stream_chat_wraps_connect_error_as_llm_error():
    settings = _settings()

    def fail_connect(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=req)

    transport = httpx.MockTransport(fail_connect)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = LLMClient(settings, client=raw, max_retries=1)
        with pytest.raises(LLMError):
            async for _ in client.stream_chat([{"role": "user", "content": "x"}]):
                pass


@pytest.mark.asyncio
async def test_stream_chat_propagates_4xx_before_streaming():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = LLMClient(settings, client=raw)
        with respx.mock(base_url=LLM_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(401, text="invalid")
            )
            with pytest.raises(LLMError):
                async for _ in client.stream_chat([{"role": "user", "content": "x"}]):
                    pass
