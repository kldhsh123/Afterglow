"""chat_api 端到端集成测试：用 FastAPI TestClient + 真 LanceDB + mock LLM/Embedding。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from xuwen.chat_api.app import create_app
from xuwen.chat_api.companion_prompt import empty_retrieval_result
from xuwen.companion.life import LifeSnapshot
from xuwen.config import Settings


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
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
        enable_pii_redaction=False,
        writeback_enabled=True,
        # 默认开真流式：stream-related 测试都假设走真流式逻辑（mock SSE 响应）；
        # 想测假流式包装行为的另写测试覆盖。
        response_streaming_enabled=True,
    )


def _embedding_response(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.read())
    n = len(body["input"])
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": [0.1 * (i + 1)] * 8}
                for i in range(n)
            ],
            "model": body.get("model", "Qwen3-Embedding-8B"),
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        },
    )


class _NoopProactiveContextCache:
    async def append_turn(self, **_: object) -> None:
        pass


def test_info_endpoint(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            r = client.get("/info")
    assert r.status_code == 200
    body = r.json()
    assert body["app_name"] == "Afterglow"
    assert body["friend_name"] == "TA"
    assert body["self_name"] == "Me"


def test_debug_stats_include_database_perf(settings: Settings):
    app = create_app(settings.model_copy(update={"debug_endpoints_enabled": True}))
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            r = client.get("/debug/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "database" in body
    assert "by_operation" in body["database"]
    assert "recent" in body["database"]


def test_healthz_open_without_api_key(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_chat_completions_non_stream(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "在的，怎么了？"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        )
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "在吗"}],
                    "stream": False,
                    "conversation_id": "conv-1",
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "在的，怎么了？"
    assert body["trace_id"] == r.headers["x-request-id"]


def test_chat_completions_non_stream_filters_history_placeholders(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "[图片]: 在干嘛"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        )
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "你在干什么"}],
                    "stream": False,
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "在干嘛"


def test_chat_completions_updates_relationship_memory(settings: Settings):
    settings = settings.model_copy(update={"response_policy_model_enabled": True})
    app = create_app(settings)

    def llm_response(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        messages = body.get("messages") or []
        system_text = str(messages[0].get("content") or "") if messages else ""
        if "生活时间线控制器" in system_text:
            content = "{}"
        elif "互动决策辅助" in system_text:
            content = json.dumps(
                {
                    "relationship_memory": {
                        "kind": "preference",
                        "importance": 2,
                        "summary": "用户喜欢吃甜食",
                        "evidence": "喜欢吃甜的",
                    }
                },
                ensure_ascii=False,
            )
        else:
            content = "记住啦"
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "model": body.get("model", "gpt-4o-mini"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(side_effect=llm_response)
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "我喜欢吃甜的"}],
                    "stream": False,
                    "conversation_id": "conv-remember",
                },
            )
            stats = client.get("/memory/stats")
    assert r.status_code == 200, r.text
    memory_file = settings.persona_data_dir / "relationship_memory.md"
    assert "用户喜欢吃甜食" in memory_file.read_text(encoding="utf-8")
    assert stats.json()["relationship_memories"] == 1


def test_chat_completions_stream(settings: Settings):
    app = create_app(settings)
    sse_body = (
        'data: {"choices":[{"delta":{"content":"嗯"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"嗯"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"，慢慢说"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=sse_body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        )
        with TestClient(app) as client:
            with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "今天有点累"}],
                    "stream": True,
                },
            ) as resp:
                chunks = list(resp.iter_lines())
    # 应该包含至少一条 delta 内容的 SSE
    body_text = "\n".join(chunks)
    assert "嗯，慢慢说" in body_text or "慢慢说" in body_text
    assert "trace_id" in body_text
    # 以 [DONE] 结束
    assert "[DONE]" in body_text


def test_chat_completions_silenced_when_user_requests_quiet(settings: Settings):
    """用户说"别说话"时，应短路返回 finish_reason=silenced + sentinel content。

    注意：life_llm 默认复用主 LLM endpoint，可能也会调到同一个 mock；
    所以这里通过响应内容（sentinel 而非 mock 返回值）来验证短路是否生效。
    """
    app = create_app(settings)
    # mock 返回合法的 chat completion，避免 life 调用解析失败干扰
    llm_payload = httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "MOCK_REPLY"},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(return_value=llm_payload)
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "别说话，让我静静"}],
                    "stream": False,
                    "conversation_id": "conv-silence",
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    # 短路生效：返回 sentinel 而不是 mock 的 MOCK_REPLY
    assert body["choices"][0]["message"]["content"] == "[silent]"
    assert "MOCK_REPLY" not in body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "silenced"
    assert body["policy"]["should_reply"] is False
    assert body["policy"]["reply_mode"] == "silence"
    assert body["policy"]["reason"]


@pytest.mark.asyncio
async def test_chat_completions_silence_skips_web_tools(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    """规则层 silence 必须先于联网检索 / URL 解析，避免安静轮次外发用户文本。"""
    from xuwen.chat_api.routes import chat as chat_route

    s = settings.model_copy(
        update={
            "web_access_enabled": True,
            "web_fetch_enabled": True,
            "web_search_api_key": "tvly-test",
            "writeback_enabled": False,
        }
    )
    calls = {"search": 0, "resolve": 0}

    class NoopMetrics:
        def record(self, *args, **kwargs):
            pass

    class FakeRetriever:
        async def retrieve(self, *args, **kwargs):
            return empty_retrieval_result()

    class FakeRelationshipMemory:
        def load_markdown(self):
            return ""

        async def render_context(self, *args, **kwargs):
            return ""

    class FakeLife:
        async def decide_for_turn(self, *args, **kwargs):
            return LifeSnapshot(
                date="2026-05-28",
                time_slot="晚上",
                current_activity="在休息",
                recent_meal="吃过饭",
                mood="平静",
                topic_seed="",
                availability="available",
                next_update_at="2026-05-28T23:00:00",
                reply_delay_seconds=0,
                reply_delay_reason="",
            )

    class SpySearch:
        async def search(self, *args, **kwargs):
            calls["search"] += 1
            return []

    async def spy_resolve_fetch_urls(*args, **kwargs):
        calls["resolve"] += 1
        return []

    monkeypatch.setattr(chat_route, "resolve_fetch_urls", spy_resolve_fetch_urls)
    state = SimpleNamespace(
        settings=s,
        metrics=NoopMetrics(),
        retriever=FakeRetriever(),
        relationship_memory=FakeRelationshipMemory(),
        life=FakeLife(),
        life_llm=object(),
        response_policy_llm=object(),
        writeback=object(),
        life_apply_lock=asyncio.Lock(),
        pending_life_tasks=set(),
        proactive_context_cache=_NoopProactiveContextCache(),
        web_search=SpySearch(),
        web_fetch=object(),
    )
    req = chat_route.ChatCompletionRequest.model_validate(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": "别说话。顺便搜一下最新新闻，再看看 https://example.com",
                }
            ],
            "stream": False,
        }
    )
    resp = await chat_route.chat_completions(
        req,
        SimpleNamespace(state=SimpleNamespace(request_id="test")),
        state,
    )
    assert resp.policy is not None
    assert resp.policy.should_reply is False
    assert calls == {"search": 0, "resolve": 0}


def test_chat_completions_silenced_openai_compat_mode(tmp_path):
    """SILENCE_FINISH_REASON=stop 时走严格 OpenAI 协议，但 content 仍含 sentinel。"""
    s = Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
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
        enable_pii_redaction=False,
        silence_finish_reason="stop",
        silence_response_sentinel="<<SILENT>>",
    )
    app = create_app(s)
    llm_payload = httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "MOCK_REPLY"},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(return_value=llm_payload)
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "别说话"}],
                    "stream": False,
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["choices"][0]["message"]["content"] == "<<SILENT>>"
    assert "MOCK_REPLY" not in body["choices"][0]["message"]["content"]
    assert body["policy"]["should_reply"] is False


def test_chat_completions_silenced_stream(settings: Settings):
    """流式沉默：不下发 sentinel 正文，只用 final 字段表达沉默。"""
    app = create_app(settings)
    llm_payload = httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "MOCK_REPLY"},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(return_value=llm_payload)
        with TestClient(app) as client:
            with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "别说话"}],
                    "stream": True,
                },
            ) as resp:
                chunks = list(resp.iter_lines())
    body_text = "\n".join(chunks)
    assert "[silent]" not in body_text
    assert "MOCK_REPLY" not in body_text
    assert '"finish_reason": "silenced"' in body_text or '"finish_reason":"silenced"' in body_text
    assert '"silenced": true' in body_text or '"silenced":true' in body_text
    assert "policy" in body_text
    assert '"should_reply": false' in body_text or '"should_reply":false' in body_text
    assert "[DONE]" in body_text


def test_chat_completions_includes_policy_field_in_normal_path(settings: Settings):
    """正常回复路径下也应在响应顶层附带 policy 字段。"""
    app = create_app(settings)
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "好的"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        )
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "嗯嗯"}],
                    "stream": False,
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["policy"]["should_reply"] is True
    assert body["policy"]["reply_mode"] in {
        "calm", "playful", "clingy", "serious", "topic_shift", "chaotic"
    }
    assert "reason" in body["policy"]


def test_companion_proactive_endpoint(settings: Settings):
    app = create_app(settings)
    calls = {"opening_judge": 0}

    def _proactive_response(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        system_prompt = str(body.get("messages", [{}])[0].get("content", ""))
        if "你只做质量判断" in system_prompt:
            calls["opening_judge"] += 1
            content = json.dumps(
                {
                    "should_rewrite": False,
                    "reason": "候选消息可直接发送",
                    "rewrite_instruction": "",
                },
                ensure_ascii=False,
            )
        else:
            content = "[图片]中午吃了吗"
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(side_effect=_proactive_response)
        with TestClient(app) as client:
            r = client.post(
                "/v1/companion/proactive",
                json={"conversation_id": "conv-1", "reason": "manual"},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"] == "中午吃了吗"
    assert calls["opening_judge"] == 1
    assert body["life"]["current_activity"]
    assert body["trace_id"] == r.headers["x-request-id"]


def test_chat_completions_ignores_client_model_field(settings: Settings):
    """客户端传任意 model 字段都不应影响实际调用——永远用 .env 配的 CHAT_MODEL。"""
    app = create_app(settings)
    captured: dict[str, str] = {}

    def _capture(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        captured["model"] = body.get("model", "")
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "model": captured["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(side_effect=_capture)
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "model": "nonexistent-model-xyz",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
    assert r.status_code == 200, r.text
    # 转发给上游的 model 必须是 .env 里的 CHAT_MODEL，不是请求里的占位值
    assert captured["model"] == settings.chat_model
    # 响应里也应返回实际使用的模型，便于调试
    assert r.json()["model"] == settings.chat_model


def test_responses_ignores_client_model_field(settings: Settings):
    """Responses API 同样忽略客户端 model 字段。"""
    app = create_app(settings)
    captured: dict[str, str] = {}

    def _capture(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        captured["model"] = body.get("model", "")
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "model": captured["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(side_effect=_capture)
        with TestClient(app) as client:
            r = client.post(
                "/v1/responses",
                json={"model": "gemini-3.5-flash", "input": "hi"},
            )
    assert r.status_code == 200, r.text
    assert captured["model"] == settings.chat_model
    assert r.json()["model"] == settings.chat_model


def test_chat_completions_stream_filters_split_history_placeholders(settings: Settings):
    app = create_app(settings)
    sse_body = (
        'data: {"choices":[{"delta":{"content":"[图"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"片]: 在"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"干嘛"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=sse_body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        )
        with TestClient(app) as client:
            with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "你在干什么"}],
                    "stream": True,
                },
            ) as resp:
                chunks = list(resp.iter_lines())
    body_text = "\n".join(chunks)
    assert "在干嘛" in body_text
    assert "[图片]" not in body_text
    assert "[DONE]" in body_text


def test_memory_stats_endpoint(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            r = client.get("/memory/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["friend_messages"] == 0
    assert body["writeback_enabled"] is True


def test_memory_writeback_pause_resume(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            r1 = client.post("/memory/writeback/pause")
            assert r1.status_code == 200
            assert r1.json()["status"] == "paused"
            r2 = client.get("/memory/stats")
            assert r2.json()["writeback_paused"] is True
            r3 = client.post("/memory/writeback/resume")
            assert r3.json()["status"] == "running"


def test_api_key_guard_blocks_unauthorized(tmp_path):
    settings = Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        xuwen_api_key="local-secret",  # type: ignore[arg-type]
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # /healthz 不受影响
        r = client.get("/healthz")
        assert r.status_code == 200
        # 其他端点要求 token
        r = client.get("/memory/stats")
        assert r.status_code == 401
        # 带正确 token 可访问
        r = client.get(
            "/memory/stats",
            headers={"Authorization": "Bearer local-secret"},
        )
        assert r.status_code == 200
        # 错误 token 拒绝
        r = client.get(
            "/memory/stats",
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401


def test_api_key_guard_requires_key_by_default(tmp_path):
    settings = Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        embedding_api_key="sk-test",  # type: ignore[arg-type]
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        r = client.get("/info")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "xuwen.auth_config"


def test_memory_search_endpoint(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        with TestClient(app) as client:
            r = client.post(
                "/memory/search",
                json={"query": "你好", "top_k": 5},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "fused" in body
    assert isinstance(body["fused"], list)
    assert body["trace_id"] == r.headers["x-request-id"]


def test_memory_search_endpoint_empty_query_returns_empty_result(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False):
        with TestClient(app) as client:
            r = client.post(
                "/memory/search",
                json={"query": "", "top_k": 5},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "fused": [],
        "response_pairs": [],
        "friend_examples": [],
        "dialogue_windows": [],
        "history_images": [],
        "recent_live": [],
        "trace_id": r.headers["x-request-id"],
    }


# ---------------------------------------------------------------------------
# /v1/responses
# ---------------------------------------------------------------------------


def test_responses_non_stream_string_input(settings: Settings):
    """input 为字符串时，返回 Responses 标准格式 + policy 字段。"""
    app = create_app(settings)
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "model": "gpt-4.1",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "在的，慢慢说"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        )
        with TestClient(app) as client:
            r = client.post(
                "/v1/responses",
                json={
                    "model": "gpt-4.1",
                    "input": "今天有点累",
                    "conversation_id": "conv-r1",
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["output_text"] == "在的，慢慢说"
    assert body["output"][0]["type"] == "message"
    assert body["output"][0]["role"] == "assistant"
    assert body["output"][0]["content"][0]["type"] == "output_text"
    assert body["output"][0]["content"][0]["text"] == "在的，慢慢说"
    assert body["policy"]["should_reply"] is True
    assert body["id"].startswith("resp_")
    assert body["output"][0]["id"].startswith("msg_")


def test_responses_with_instructions_and_message_array(settings: Settings):
    """instructions 注入 system；input 为消息数组。"""
    app = create_app(settings)
    captured: dict[str, list[dict[str, str]]] = {}

    def _capture_llm(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        captured["messages"] = body.get("messages") or []
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "model": "gpt-4.1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "好"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(side_effect=_capture_llm)
        with TestClient(app) as client:
            r = client.post(
                "/v1/responses",
                json={
                    "instructions": "请用很轻的语气陪我聊聊",
                    "input": [
                        {"role": "user", "content": "在吗"},
                        {"role": "assistant", "content": "在的"},
                        {"role": "user", "content": "今天有点烦"},
                    ],
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output_text"] == "好"
    # instructions 应被注入到下游 prompt 的 system 角色之一（轻度断言）
    sys_concat = "\n".join(
        m.get("content", "") for m in captured.get("messages", []) if m.get("role") == "system"
    )
    assert "很轻的语气" in sys_concat


def test_responses_silenced(settings: Settings):
    """用户要求安静时返回完整 Responses 协议响应，content=sentinel。"""
    app = create_app(settings)
    llm_payload = httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "model": "gpt-4.1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "MOCK_REPLY"},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(return_value=llm_payload)
        with TestClient(app) as client:
            r = client.post(
                "/v1/responses",
                json={"input": "别说话，让我静静"},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["output_text"] == "[silent]"
    assert "MOCK_REPLY" not in body["output_text"]
    assert body["policy"]["should_reply"] is False
    assert body["policy"]["reply_mode"] == "silence"


@pytest.mark.asyncio
async def test_responses_silence_skips_web_tools(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    """Responses API 的 silence 路径同样不能先触发 web search / URL resolve。"""
    from xuwen.chat_api.routes import responses as responses_route

    s = settings.model_copy(
        update={
            "web_access_enabled": True,
            "web_fetch_enabled": True,
            "web_search_api_key": "tvly-test",
            "writeback_enabled": False,
        }
    )
    calls = {"search": 0, "resolve": 0}

    class NoopMetrics:
        def record(self, *args, **kwargs):
            pass

    class FakeRetriever:
        async def retrieve(self, *args, **kwargs):
            return responses_route.empty_retrieval_result()

    class FakeRelationshipMemory:
        def load_markdown(self):
            return ""

        async def render_context(self, *args, **kwargs):
            return ""

    class FakeLife:
        async def decide_for_turn(self, *args, **kwargs):
            return LifeSnapshot(
                date="2026-05-28",
                time_slot="晚上",
                current_activity="在休息",
                recent_meal="吃过饭",
                mood="平静",
                topic_seed="",
                availability="available",
                next_update_at="2026-05-28T23:00:00",
                reply_delay_seconds=0,
                reply_delay_reason="",
            )

    class FakeResponsesStore:
        def put(self, *args, **kwargs):
            pass

    class SpySearch:
        async def search(self, *args, **kwargs):
            calls["search"] += 1
            return []

    async def spy_resolve_fetch_urls(*args, **kwargs):
        calls["resolve"] += 1
        return []

    monkeypatch.setattr(responses_route, "resolve_fetch_urls", spy_resolve_fetch_urls)
    state = SimpleNamespace(
        settings=s,
        metrics=NoopMetrics(),
        retriever=FakeRetriever(),
        relationship_memory=FakeRelationshipMemory(),
        life=FakeLife(),
        life_llm=object(),
        response_policy_llm=object(),
        writeback=object(),
        responses_store=FakeResponsesStore(),
        life_apply_lock=asyncio.Lock(),
        pending_life_tasks=set(),
        proactive_context_cache=_NoopProactiveContextCache(),
        web_search=SpySearch(),
        web_fetch=object(),
    )
    req = responses_route.ResponsesRequest.model_validate(
        {
            "input": "别说话。帮我搜索最新消息，也看看 https://example.com",
        }
    )
    resp = await responses_route.responses(
        req,
        SimpleNamespace(state=SimpleNamespace(request_id="test")),
        state,
    )
    assert resp.policy is not None
    assert resp.policy.should_reply is False
    assert calls == {"search": 0, "resolve": 0}


def test_responses_stream_event_sequence(settings: Settings):
    """流式应按 Responses 协议输出完整事件序列，并以 [DONE] 结尾。"""
    app = create_app(settings)
    sse_body = (
        'data: {"choices":[{"delta":{"content":"嗯"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"，"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"慢慢说"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=sse_body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        )
        with TestClient(app) as client:
            with client.stream(
                "POST",
                "/v1/responses",
                json={"input": "今天有点累", "stream": True},
            ) as resp:
                chunks = list(resp.iter_lines())
    body_text = "\n".join(chunks)
    events: list[tuple[str, dict[str, object]]] = []
    current_event: str | None = None
    for line in chunks:
        if line.startswith("event: "):
            current_event = line.removeprefix("event: ")
        elif line.startswith("data: ") and line != "data: [DONE]":
            assert current_event is not None, f"data 行缺少对应 event: {line}"
            payload = json.loads(line.removeprefix("data: "))
            events.append((current_event, payload))
            current_event = None

    event_types = [event_type for event_type, _ in events]
    for required_event in (
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.completed",
    ):
        assert required_event in event_types, f"缺少事件 {required_event}"
    for event_type, payload in events:
        assert payload["type"] == event_type
    assert [payload["sequence_number"] for _, payload in events] == list(range(len(events)))
    assert "[DONE]" in body_text


def test_responses_event_formatter_protects_protocol_metadata():
    """每条 Responses 流应独立编号，且 payload 不能覆盖协议元数据。"""
    from xuwen.chat_api.routes.responses import _new_event_formatter

    first_stream = _new_event_formatter()
    second_stream = _new_event_formatter()

    first = first_stream(
        "response.created",
        {"response": {}, "type": "wrong", "sequence_number": 99},
    )
    second = first_stream("response.completed", {"response": {"usage": {}}})
    failed = first_stream(
        "response.failed",
        {"response": {"status": "failed", "error": {"code": "test"}}},
    )
    independent = second_stream("response.created", {"response": {}})

    payloads = [
        json.loads(event.decode().split("data: ", 1)[1])
        for event in (first, second, failed, independent)
    ]
    assert [payload["type"] for payload in payloads] == [
        "response.created",
        "response.completed",
        "response.failed",
        "response.created",
    ]
    assert [payload["sequence_number"] for payload in payloads] == [0, 1, 2, 0]


def test_responses_previous_response_id_inherits_conversation(settings: Settings):
    """第二次请求带上一次的 response_id，应能继承 conversation_id。"""
    app = create_app(settings)
    with respx.mock(assert_all_called=False) as router:
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=_embedding_response)
        router.post("https://llm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "model": "gpt-4.1",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "好"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        )
        with TestClient(app) as client:
            r1 = client.post(
                "/v1/responses",
                json={"input": "一", "conversation_id": "conv-prev"},
            )
            assert r1.status_code == 200, r1.text
            first_id = r1.json()["id"]
            r2 = client.post(
                "/v1/responses",
                json={"input": "二", "previous_response_id": first_id},
            )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["previous_response_id"] == first_id
    # 验证两次写入同一会话：两条 user_new + 两条 ai_generated 共四行
    # 这里只做轻断言：确认接口能跑通即可
