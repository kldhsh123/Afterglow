"""未知格式分析回归用例：验证真实模型客户端、完整文件转换和导入边界。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.ingestion.ai_converter import (
    conversion_mapping_path,
    conversion_output_path,
    convert_unknown_file,
    file_has_plugin,
)
from xuwen.ingestion.conversion_rules import (
    MAX_EXPORT_BYTES,
    MAX_SAMPLE_CHARS,
    convert,
    make_sample,
    parse_mapping_response,
    read_export,
)
from xuwen.web_ui.inspect_file import inspect_chat_file

BASE = "https://conversion.test/v1"
SPEC = {"messages_path": "/archive/events", "sender_id": "uid", "text": "body", "timestamp": "when"}


def _settings(**changes) -> Settings:
    values = {"openai_base_url": BASE, "openai_api_key": "test-key", "chat_model": "user-model"}
    return Settings(_env_file=None, **{**values, **changes})


def _source(tmp_path: Path, count: int = 2) -> Path:
    path = tmp_path / "unknown.json"
    records = [
        {"uid": f"sender-{index % 2}", "body": f"message-{index}", "when": 1700000000 + index}
        for index in range(count)
    ]
    path.write_text(json.dumps({"archive": {"events": records}}), encoding="utf-8")
    return path


def _answer(content, protocol="chat_completions", *, incomplete=False):
    if protocol == "responses":
        return {
            "status": "incomplete" if incomplete else "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": content or ""}]}
            ],
        }
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                    "reasoning_content": "只作为思考内容，不能代替最终正文",
                },
                "finish_reason": "length" if incomplete else "stop",
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_converts_full_file_with_user_model_without_executing_generated_code(
    tmp_path, protocol
):
    source = _source(tmp_path, 1005)
    assert not file_has_plugin(source)
    endpoint = "/responses" if protocol == "responses" else "/chat/completions"
    with respx.mock(base_url=BASE) as router:
        route = router.post(endpoint).respond(
            200, json=_answer("```json\n" + json.dumps(SPEC) + "\n```", protocol)
        )
        output = await convert_unknown_file(source, _settings(chat_api_protocol=protocol))
    converted = json.loads(output.read_text())
    assert output == conversion_output_path(source)
    assert len(converted["messages"]) == 1005
    assert converted["messages"][-1]["text"] == "message-1004"
    assert {item["role"] for item in converted["participants"]} == {"unknown"}
    assert converted["conversion_stats"]["skipped_records"] == 0
    assert inspect_chat_file(output).total_messages == 1005
    assert not list(tmp_path.glob("*.py"))
    request = json.loads(route.calls[0].request.content)
    assert request["model"] == "user-model"
    assert (
        "thinking" not in request
        and "reasoning" not in request
        and "reasoning_effort" not in request
    )
    if protocol == "responses":
        assert request["text"]["format"] == {"type": "json_object"}
        assert request["store"] is False
    else:
        assert request["response_format"] == {"type": "json_object"}
    assert len(json.dumps(request, ensure_ascii=False)) < 55_000
    assert (
        json.loads(conversion_mapping_path(source).read_text())["mapping"]["messages_path"]
        == SPEC["messages_path"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
@pytest.mark.parametrize("failure", ["empty", "truncated", "invalid_json", "invalid_mapping"])
async def test_retries_invalid_final_answer_once_with_larger_budget(tmp_path, protocol, failure):
    source = _source(tmp_path)
    first = {
        "empty": _answer(None, protocol),
        "truncated": _answer('{"messages_path":', protocol, incomplete=True),
        "invalid_json": _answer("this is not JSON", protocol),
        "invalid_mapping": _answer(json.dumps({**SPEC, "messages_path": "/wrong"}), protocol),
    }[failure]
    endpoint = "/responses" if protocol == "responses" else "/chat/completions"
    with respx.mock(base_url=BASE) as router:
        route = router.post(endpoint).mock(
            side_effect=[
                httpx.Response(200, json=first),
                httpx.Response(200, json=_answer(json.dumps(SPEC), protocol)),
            ]
        )
        output = await convert_unknown_file(source, _settings(chat_api_protocol=protocol))
    assert len(json.loads(output.read_text())["messages"]) == 2
    assert route.call_count == 2
    key = "max_output_tokens" if protocol == "responses" else "max_tokens"
    assert [json.loads(call.request.content)[key] for call in route.calls] == [4096, 8192]


@pytest.mark.asyncio
async def test_empty_after_retry_has_readable_error_and_does_not_publish_output(
    tmp_path, chinese_language
):
    source = _source(tmp_path)
    with respx.mock(base_url=BASE) as router:
        route = router.post("/chat/completions").respond(200, json=_answer(None))
        with pytest.raises(ParseError, match=r"重试后仍失败.*最终 JSON"):
            await convert_unknown_file(source, _settings())
    assert route.call_count == 2
    assert not conversion_output_path(source).exists()
    assert not conversion_mapping_path(source).exists()


@pytest.mark.asyncio
async def test_reuses_mapping_only_for_unchanged_source_and_timezone(tmp_path):
    source = _source(tmp_path)
    with respx.mock(base_url=BASE) as router:
        route = router.post("/chat/completions").respond(200, json=_answer(json.dumps(SPEC)))
        output = await convert_unknown_file(source, _settings())
        output.write_text("broken output", encoding="utf-8")
        await convert_unknown_file(source, _settings())
        assert route.call_count == 1
        source.write_text(source.read_text().replace("message-1", "changed"), encoding="utf-8")
        await convert_unknown_file(source, _settings())
        await convert_unknown_file(source, _settings(app_timezone="UTC"))
        assert route.call_count == 3
    assert json.loads(output.read_text())["messages"][-1]["text"] == "changed"


def test_samples_first_twenty_lines_and_three_hundred_later_lines():
    text = "\n".join(f"source-line-{index}" for index in range(500))
    sample = make_sample(text)
    assert "[line 1] source-line-0" in sample
    assert "[line 20] source-line-19" in sample
    assert sample.count("[line ") == 320


def test_minified_json_uses_bounded_record_samples():
    records = [
        {"uid": index % 2, "body": f"value-{index}", "time": 1700000000} for index in range(1005)
    ]
    sample = make_sample(json.dumps(records), records)
    assert len(sample) <= MAX_SAMPLE_CHARS
    assert sample.count("[record ") == 300
    assert "[line 1]" in sample


def test_root_array_and_pointer_fields_preserve_invalid_record_counts():
    payload = [
        {"user.name": "A", "time": 1700000000.125, "body": "hello"},
        {"user.name": "B", "time": 1700000001125, "body": "hi"},
        {"user.name": None, "time": 1700000002, "body": "system event"},
        {"user.name": "A", "time": 1700000000.125, "body": "hello"},
    ]
    spec = {"messages_path": "", "sender_name": "/user.name", "timestamp": "time", "text": "body"}
    result = convert(payload, spec, "chat", "UTC")
    assert result["messages"][0]["timestamp_ms"] == 1700000000125
    assert result["conversion_stats"]["skipped_records"] == 1
    assert result["messages"][0]["id"] != result["messages"][-1]["id"]
    assert result["messages"][-1]["seq"] == 3


def test_text_rule_uses_app_timezone_and_reports_unmatched_lines():
    payload = {"_text": "export header\n2026/01/01 12:00 A: hello\n2026/01/01 12:01 B: hi"}
    spec = {
        "format": "text",
        "timestamp_format": "%Y/%m/%d %H:%M",
        "record_pattern": r"^(?P<timestamp>\d{4}/\d{2}/\d{2} \d{2}:\d{2}) (?P<sender_name>[^:]+): (?P<text>[^\n]+)$",
    }
    utc = convert(payload, spec, "chat", "UTC")
    shanghai = convert(payload, spec, "chat", "Asia/Shanghai")
    assert (
        utc["messages"][0]["timestamp_ms"] - shanghai["messages"][0]["timestamp_ms"]
        == 8 * 3600 * 1000
    )
    assert utc["conversion_stats"]["unmatched_lines"] == 1


@pytest.mark.parametrize("answer", ['{"script":"print(1)"}', "```python\nprint(1)\n```", "{}"])
def test_rejects_scripts_and_incomplete_rules(answer):
    with pytest.raises(ParseError):
        parse_mapping_response(answer)


def test_malformed_json_keeps_entire_source_for_text_analysis(tmp_path):
    path = tmp_path / "broken.json"
    original = '{"uid":"A"}\ntrailing chat content'
    path.write_text(original, encoding="utf-8")
    payload, records, text = read_export(path)
    assert payload["_text"] == text == original
    assert payload["_source_warning"]
    assert records == []


@pytest.mark.asyncio
async def test_size_limit_precedes_any_model_request(tmp_path):
    path = tmp_path / "large.json"
    with path.open("wb") as stream:
        stream.truncate(MAX_EXPORT_BYTES + 1)
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(ParseError, match="64 MB"):
            await convert_unknown_file(path, _settings())
    assert len(router.calls) == 0


@pytest.mark.asyncio
async def test_conversion_endpoint_does_not_start_import(tmp_path, monkeypatch):
    from xuwen.web_ui import routes

    source = _source(tmp_path)
    output = conversion_output_path(source)
    payload, _, _ = read_export(source)
    converted = convert(payload, SPEC, "chat", "UTC")
    output.write_text(json.dumps(converted), encoding="utf-8")
    conversion_mapping_path(source).write_text(
        json.dumps({"statistics": converted["conversion_stats"]})
    )
    fake_convert = AsyncMock(return_value=output)
    monkeypatch.setattr(routes, "convert_unknown_file", fake_convert)
    monkeypatch.setattr(routes, "get_manager", lambda: pytest.fail("转换接口不得创建导入任务"))
    result = await routes.convert_import_file(
        routes.ConvertImportPayload(path=str(source)),
        _settings(config_ui_uploads_dir=str(tmp_path)),
    )
    assert result["format"] == "afterglow_v1"
    assert result["total_messages"] == 2
    assert result["conversion_stats"]["skipped_records"] == 0
    assert all(candidate["role_hint"] == "unknown" for candidate in result["candidates"])
