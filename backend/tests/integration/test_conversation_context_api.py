"""从真实 HTTP 入口验证摘要接入、缓存复用与最终主模型上下文。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from xuwen.chat_api.app import create_app
from xuwen.config import Settings


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses"])
def test_long_conversation_summary_reaches_main_model_and_is_reused(tmp_path, endpoint):
    settings = Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "db",
        persona_data_dir=tmp_path / "persona",
        openai_base_url="https://model.test/v1",
        openai_api_key="test",
        embedding_api_url="https://embedding.test/v1",
        embedding_api_key="test",
        api_auth_required=False,
        writeback_enabled=False,
        conversation_summary_trigger_messages=6,
        conversation_summary_keep_messages=2,
    )
    main_calls = []
    summary_calls = []

    def model_response(request):
        payload = json.loads(request.read())
        if "Maintain a compact summary" in payload["messages"][0]["content"]:
            summary_calls.append(payload)
            text = "用户先前提到想学游泳；AI仅提出了建议。"
        else:
            main_calls.append(payload)
            text = "可以，慢慢来"
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    def embedding_response(request):
        payload = json.loads(request.read())
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [0.1] * 8} for i in range(len(payload["input"]))
        ]})

    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"原始消息-{i}"}
        for i in range(8)
    ]
    messages = [*history, {"role": "user", "content": "继续聊吧"}]
    with respx.mock(assert_all_called=False) as router:
        router.post("https://model.test/v1/chat/completions").mock(side_effect=model_response)
        router.post("https://embedding.test/v1/embeddings").mock(side_effect=embedding_response)
        with TestClient(create_app(settings)) as client:
            state = client.app.state.xuwen
            state.life.decide_for_turn = AsyncMock(return_value=state.life.snapshot())
            for _ in range(2):
                body = {"conversation_id": "one", "stream": False}
                body["messages" if endpoint == "chat/completions" else "input"] = messages
                response = client.post(f"/v1/{endpoint}", json=body)
                assert response.status_code == 200, response.text

    assert len(summary_calls) == 1
    assert len(main_calls) == 2
    for call in main_calls:
        sent = call["messages"]
        assert "用户先前提到想学游泳" in sent[0]["content"]
        assert "原始消息-0" not in json.dumps(sent, ensure_ascii=False)
        assert sent[1:] == [*history[-2:], messages[-1]]
