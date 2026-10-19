"""会话压缩：原文保留、增量复用、分支隔离和失败时不丢上下文。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from xuwen.chat_api.conversation_context import prepare_conversation_context
from xuwen.config import Settings
from xuwen.core.errors import LLMError
from xuwen.persona.prompt import ChatMessage


def _settings(tmp_path, **overrides):
    return Settings(
        persona_data_dir=tmp_path,
        conversation_summary_trigger_messages=6,
        conversation_summary_keep_messages=2,
        **overrides,
    )


def _history(count=8):
    return [ChatMessage("user" if i % 2 == 0 else "assistant", f"message-{i}") for i in range(count)]


async def test_short_conversation_does_not_call_model_or_write_cache(tmp_path):
    llm = AsyncMock()
    history = _history(4)
    result = await prepare_conversation_context(
        history, settings=_settings(tmp_path), llm=llm, conversation_id="one"
    )
    assert result.recent == history
    assert result.summary == ""
    llm.complete_chat.assert_not_called()
    assert not (tmp_path / "conversation_summaries").exists()


async def test_summary_keeps_recent_originals_and_reuses_persistent_prefix(tmp_path):
    llm = AsyncMock()
    llm.complete_chat.return_value = "用户说的事实；AI 提出的建议。"
    history = _history()
    settings = _settings(tmp_path)
    first = await prepare_conversation_context(
        history, settings=settings, llm=llm, conversation_id="../conversation"
    )
    assert first.recent == history[-2:]
    assert first.summary == llm.complete_chat.return_value
    assert len(history) == 8
    files = list((tmp_path / "conversation_summaries").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["covered_messages"] == 6

    second = await prepare_conversation_context(
        _history(10), settings=settings, llm=llm, conversation_id="../conversation"
    )
    assert second.recent == _history(10)[6:]
    assert second.summary == first.summary
    assert llm.complete_chat.await_count == 1


async def test_incremental_summary_only_sends_newly_covered_messages(tmp_path):
    llm = AsyncMock()
    llm.complete_chat.return_value = "previous summary"
    settings = _settings(tmp_path)
    await prepare_conversation_context(
        _history(), settings=settings, llm=llm, conversation_id="one"
    )
    result = await prepare_conversation_context(
        _history(14), settings=settings, llm=llm, conversation_id="one"
    )
    payload = json.loads(llm.complete_chat.call_args.args[0][1]["content"])
    assert payload["previous_summary"] == "previous summary"
    assert [m["content"] for m in payload["messages"]] == [f"message-{i}" for i in range(6, 12)]
    assert result.recent == _history(14)[12:]


@pytest.mark.parametrize("change_scope", [False, True])
async def test_edited_prefix_or_different_conversation_does_not_reuse_summary(tmp_path, change_scope):
    llm = AsyncMock()
    llm.complete_chat.return_value = "old summary"
    settings = _settings(tmp_path)
    await prepare_conversation_context(
        _history(), settings=settings, llm=llm, conversation_id="one"
    )
    history = _history()
    if not change_scope:
        history[0] = ChatMessage("user", "edited message")
    await prepare_conversation_context(
        history, settings=settings, llm=llm, conversation_id="two" if change_scope else "one"
    )
    payload = json.loads(llm.complete_chat.call_args.args[0][1]["content"])
    assert payload["previous_summary"] == ""
    assert len(payload["messages"]) == 6


async def test_character_trigger_splits_large_messages_without_losing_text(tmp_path):
    llm = AsyncMock()
    llm.complete_chat.return_value = "summary"
    history = [ChatMessage("user", "x" * 700), ChatMessage("assistant", "response")]
    settings = _settings(
        tmp_path,
        conversation_summary_trigger_chars=200,
        conversation_summary_recent_chars=100,
        conversation_summary_batch_chars=256,
    )
    result = await prepare_conversation_context(
        history, settings=settings, llm=llm, conversation_id="one"
    )
    sent = [
        json.loads(call.args[0][1]["content"])["messages"]
        for call in llm.complete_chat.call_args_list
    ]
    assert all(sum(len(m["content"]) for m in batch) <= 256 for batch in sent)
    assert "".join(m["content"] for batch in sent for m in batch) == "x" * 700 + "response"
    assert result.recent == []


async def test_instructions_survive_compaction_and_are_not_sent_to_summarizer(tmp_path):
    llm = AsyncMock()
    llm.complete_chat.return_value = "summary"
    instruction = ChatMessage("system", "Reply briefly")
    result = await prepare_conversation_context(
        [instruction, *_history()], settings=_settings(tmp_path), llm=llm, conversation_id="one"
    )
    assert result.recent == [instruction, *_history()[-2:]]
    assert "Reply briefly" not in llm.complete_chat.call_args.args[0][1]["content"]


@pytest.mark.parametrize("failure", [LLMError("unavailable"), "", "timeout"])
async def test_summary_failure_stops_request_and_preserves_last_good_cache(tmp_path, failure):
    llm = AsyncMock()
    llm.complete_chat.return_value = "valid summary"
    settings = _settings(tmp_path, conversation_summary_timeout_seconds=0.01)
    await prepare_conversation_context(
        _history(), settings=settings, llm=llm, conversation_id="one"
    )
    cache = next((tmp_path / "conversation_summaries").glob("*.json"))
    before = cache.read_text()
    if failure == "timeout":
        async def delayed(*args, **kwargs):
            await asyncio.sleep(1)
            return "late summary"
        llm.complete_chat.side_effect = delayed
    elif isinstance(failure, Exception):
        llm.complete_chat.side_effect = failure
    else:
        llm.complete_chat.return_value = failure
    with pytest.raises(LLMError):
        await prepare_conversation_context(
            _history(14), settings=settings, llm=llm, conversation_id="one"
        )
    assert cache.read_text() == before


async def test_disable_preserves_all_original_history(tmp_path):
    llm = AsyncMock()
    history = _history(50)
    result = await prepare_conversation_context(
        history,
        settings=_settings(tmp_path, conversation_summary_enabled=False),
        llm=llm,
        conversation_id="one",
    )
    assert result.recent == history
    llm.complete_chat.assert_not_called()
