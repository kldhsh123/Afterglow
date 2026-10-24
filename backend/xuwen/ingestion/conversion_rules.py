"""声明式聊天转换，参考 Afterglow-Desktop 的 import_conversion 实现。

模型仅返回字段路径或文本匹配规则，本地代码负责完整文件转换及输出格式。
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import subprocess
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from xuwen.core.errors import ParseError
from xuwen.error_i18n import error_text

MAX_SAMPLE_CHARS = 48_000
MAX_MESSAGES = 2_000_000
MAX_EXPORT_BYTES = 64 * 1024 * 1024
FIELDS = {"sender_id", "sender_name", "text", "timestamp", "kind"}


def parse_mapping_response(content: Any) -> dict[str, str]:
    """只解析最终 JSON 正文，不接收思考内容或可执行代码。"""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        raise ParseError(
            error_text(
                "The model returned no final JSON answer; reasoning may have consumed the output budget"
            )
        )
    content = content.lstrip("\ufeff").strip()
    # 部分兼容接口会忽略 response_format 并添加代码围栏。
    # 仅兼容 JSON 围栏，字段路径仍然需要严格校验。
    fenced = re.fullmatch(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content, flags=re.IGNORECASE)
    if fenced:
        content = fenced.group(1).strip()
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        raise ParseError(
            error_text("The model did not return a valid JSON conversion mapping")
        ) from None
    return validate_spec(value)


class _PlainHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "div", "p", "tr", "li"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"div", "p", "tr", "li"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _get(value: Any, path: str) -> Any:
    if path == "" or path == "$":
        return value
    current = value
    parts = (
        [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
        if path.startswith("/")
        else path.removeprefix("$.").split(".")
    )
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)] if int(part) < len(current) else None
        else:
            return None
    return current


def read_export(path: Path) -> tuple[Any, list[Any], str]:
    """读取完整源文件，返回原始数据、抽样记录和文本。"""
    suffix = path.suffix.lower()
    if suffix not in {".json", ".jsonl", ".ndjson", ".txt", ".md", ".html", ".htm"}:
        raise ParseError(error_text("This file type does not support AI format analysis"))
    if path.stat().st_size > MAX_EXPORT_BYTES:
        raise ParseError(error_text("Unknown-format AI conversion is limited to 64 MB per file"))
    with path.open("rb") as stream:
        raw = stream.read(MAX_EXPORT_BYTES + 1)
    if len(raw) > MAX_EXPORT_BYTES:
        raise ParseError(error_text("Unknown-format AI conversion is limited to 64 MB per file"))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseError(error_text("AI format analysis requires a UTF-8 chat file")) from None
    if suffix in {".txt", ".md", ".html", ".htm"}:
        if suffix in {".html", ".htm"}:
            parser = _PlainHTML()
            parser.feed(text)
            text = "".join(parser.parts)
        return {"_text": text}, [], text
    if suffix in {".jsonl", ".ndjson"}:
        records = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                return _unstructured_json(text, number, exc.colno, exc.msg)
            records.append(value)
        return records, records, text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        # .json 后缀不代表内容合法；保留完整原文，不能静默丢弃尾部数据。
        return _unstructured_json(text, exc.lineno, exc.colno, exc.msg)
    return payload, _find_record_list(payload), text


def _unstructured_json(
    text: str,
    line: int,
    column: int,
    reason: str,
) -> tuple[dict[str, Any], list[Any], str]:
    explanation = error_text(
        "Trailing content after the first JSON value" if reason == "Extra data" else "Invalid JSON"
    )
    warning = error_text(
        "Source JSON line {line}, column {column}: {reason}. The entire original text was analyzed; check message counts and identities before import.",
        line=line,
        column=column,
        reason=explanation,
    )
    return {"_text": text, "_source_warning": warning}, [], text


def _find_record_list(value: Any, depth: int = 0) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and depth <= 6:
        preferred = ("messages", "records", "items", "chats", "data", "history")
        for key in preferred:
            candidate = value.get(key)
            if (
                isinstance(candidate, list)
                and candidate
                and all(isinstance(i, dict) for i in candidate[:20])
            ):
                return candidate
        candidates = [
            v
            for v in value.values()
            if isinstance(v, list) and v and all(isinstance(i, dict) for i in v[:20])
        ]
        if len(candidates) == 1:
            return candidates[0]
        for child in value.values():
            if isinstance(child, dict):
                records = _find_record_list(child, depth + 1)
                if records:
                    return records
    return []


def describe_structure(payload: Any) -> str:
    """提供准确的容器路径，不在结构描述中包含消息正文。"""
    lines: list[str] = []

    def visit(value: Any, path: str, depth: int = 0) -> None:
        if depth > 6 or len(lines) >= 100:
            return
        if isinstance(value, dict):
            lines.append(
                f"{path or '/'}: object keys={json.dumps(list(value)[:40], ensure_ascii=False)}"
            )
            for key, child in list(value.items())[:40]:
                if isinstance(child, (dict, list)):
                    visit(child, path + "/" + key.replace("~", "~0").replace("/", "~1"), depth + 1)
        elif isinstance(value, list):
            lines.append(f"{path or '/'}: array, {len(value)} entries")
            for child in value[:1]:
                if isinstance(child, (dict, list)):
                    visit(child, path + "/0", depth + 1)

    visit(payload, "")
    return "\n".join(lines)


def _preview(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[nested data omitted]"
    if isinstance(value, str):
        return value[:1000] + (" … [truncated]" if len(value) > 1000 else "")
    if isinstance(value, dict):
        return {str(k): _preview(v, depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [_preview(v, depth + 1) for v in value[:5]]
    return value


def make_sample(text: str, records: list[Any] | None = None) -> str:
    # 保留前 20 行，并对后续行做最多 300 行的蓄水池抽样，避免复制完整行列表。
    lines, later = [], []
    cursor, number = 0, 0
    rng = random.SystemRandom()
    while cursor < len(text):
        end = text.find("\n", cursor)
        if end < 0:
            end = len(text)
        line = text[cursor:end].rstrip("\r")
        if number < 20:
            lines.append(line)
        elif not records:
            if len(later) < 300:
                later.append((number, line[:1000]))
            else:
                index = rng.randrange(number - 19)
                if index < 300:
                    later[index] = (number, line[:1000])
        cursor = end + 1
        number += 1
        if records and number == 20:
            break
    # 头部和随机记录分别限制长度，避免单行 JSON 耗尽抽样额度。
    first = [line[:600] + (" … [truncated]" if len(line) > 600 else "") for line in lines[:20]]
    if records:
        indices = sorted(random.SystemRandom().sample(range(len(records)), min(300, len(records))))
        sampled = [
            f"[record {i + 1}] {json.dumps(_preview(records[i]), ensure_ascii=False, separators=(',', ':'))}"
            for i in indices
        ]
    elif later:
        sampled = [f"[line {i + 1}] {line}" for i, line in sorted(later)]
    else:
        sampled = []
    header = "\n".join(f"[line {i + 1}] {line}" for i, line in enumerate(first))
    parts = [header, "--- random sample ---"]
    remaining = MAX_SAMPLE_CHARS - len("\n".join(parts)) - 100
    omitted = 0
    for item in sampled:
        if len(item) + 1 <= remaining:
            parts.append(item)
            remaining -= len(item) + 1
        else:
            omitted += 1
    if omitted:
        parts.append(f"[{omitted} sampled entries omitted to fit the sample size limit]")
    return "\n".join(parts)


def _valid_path(path: Any, limit: int) -> bool:
    if not isinstance(path, str) or len(path) > limit or any(ord(c) < 32 for c in path):
        return False
    if path.startswith("/"):
        return not re.search(r"~(?![01])", path)
    if path in {"", "$"}:
        return True
    # 含空格、点号或标点的键使用 JSON Pointer。
    return bool(re.fullmatch(r"(?:\$\.)?[\w-]+(?:\.[\w-]+)*", path))


def validate_spec(spec: Any) -> dict[str, str]:
    if not isinstance(spec, dict) or set(spec) - {
        "format",
        "messages_path",
        *FIELDS,
        "timestamp_format",
        "record_pattern",
    }:
        raise ParseError(
            error_text("The model must return a JSON conversion mapping, not executable code")
        )
    timestamp_format = spec.get("timestamp_format", "")
    if not isinstance(timestamp_format, str) or len(timestamp_format) > 100:
        raise ParseError(error_text("The conversion timestamp format is invalid"))
    if spec.get("format") == "text":
        pattern = spec.get("record_pattern")
        if (
            not isinstance(pattern, str)
            or not 1 <= len(pattern) <= 2000
            or not pattern.startswith("^")
        ):
            raise ParseError(
                error_text("The text conversion rule must be an anchored message pattern")
            )
        try:
            compiled = re.compile(pattern, re.MULTILINE)
        except re.error:
            raise ParseError(error_text("The text conversion pattern is invalid")) from None
        groups = compiled.groupindex
        if not {"text", "timestamp"}.issubset(groups) or not {
            "sender_id",
            "sender_name",
        }.intersection(groups):
            raise ParseError(
                error_text("A conversion rule requires sender, timestamp and text fields")
            )
        return {
            "format": "text",
            "record_pattern": pattern,
            "timestamp_format": timestamp_format,
            "messages_path": "",
            **{field: field if field in groups else "" for field in FIELDS},
        }
    if spec.get("format", "json") != "json":
        raise ParseError(
            error_text("The model must return a JSON conversion mapping, not executable code")
        )
    messages_path = spec.get("messages_path", "")
    if not _valid_path(messages_path, 240):
        raise ParseError(
            error_text("The conversion mapping contains an invalid message array path")
        )
    result = {"messages_path": messages_path}
    for field in FIELDS:
        path = spec.get(field, "")
        if not _valid_path(path, 160):
            raise ParseError(
                error_text(
                    "The conversion mapping contains an invalid field path: {field}", field=field
                )
            )
        result[field] = path
    if (
        not result["text"]
        or not (result["sender_id"] or result["sender_name"])
        or not result["timestamp"]
    ):
        raise ParseError(error_text("A conversion rule requires sender, timestamp and text fields"))
    return {**result, "timestamp_format": timestamp_format}


def _timestamp_ms(value: Any, timezone: ZoneInfo, timestamp_format: str = "") -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            if math.isfinite(value):
                return int(value if abs(value) >= 100_000_000_000 else value * 1000)
        except OverflowError:
            pass
        return None
    if isinstance(value, str):
        raw = value.strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
            try:
                return _timestamp_ms(float(raw), timezone)
            except (ValueError, OverflowError):
                return None
        try:
            parsed = (
                datetime.strptime(raw, timestamp_format)
                if timestamp_format
                else datetime.fromisoformat(raw.replace("Z", "+00:00"))
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone)
            return int(parsed.timestamp() * 1000)
        except (ValueError, OverflowError, OSError):
            return None
    return None


# 只运行这段固定程序，模型生成的正则仅作为 JSON 数据传入。
# 使用独立进程，确保耗时回溯在不同系统上都能超时终止。
_TEXT_MATCHER = """
import json, re, sys
data = json.load(sys.stdin)
records, unmatched, cursor = [], 0, 0
for match in re.finditer(data['pattern'], data['text'], re.MULTILINE):
    if len(records) >= data['limit']:
        raise ValueError('Too many messages')
    unmatched += sum(bool(line.strip()) for line in data['text'][cursor:match.start()].splitlines())
    records.append(match.groupdict())
    cursor = match.end()
unmatched += sum(bool(line.strip()) for line in data['text'][cursor:].splitlines())
json.dump({'records': records, 'unmatched_lines': unmatched}, sys.stdout, ensure_ascii=False)
"""


def _text_records(text: str, pattern: str) -> tuple[list[Any], int]:
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _TEXT_MATCHER],
            input=json.dumps({"text": text, "pattern": pattern, "limit": MAX_MESSAGES}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=True,
        )
        parsed = json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise ParseError(
            error_text("The text conversion pattern timed out; analyze the format again")
        ) from exc
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise ParseError(
            error_text("The text conversion pattern failed; analyze the format again")
        ) from exc
    return parsed["records"], parsed["unmatched_lines"]


def convert(
    payload: Any,
    spec: dict[str, str],
    title: str,
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    spec = validate_spec(spec)
    unmatched_lines = 0
    if spec.get("format") == "text":
        text = payload.get("_text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise ParseError(error_text("A text conversion rule requires a text export"))
        source_records, unmatched_lines = _text_records(text, spec["record_pattern"])
    else:
        # 空路径只表示根数组，不能悄悄改用自动发现的其他列表。
        source_records = _get(payload, spec["messages_path"])
    if not isinstance(source_records, list) or not source_records:
        raise ParseError(error_text("The conversion mapping did not select a valid message array"))
    if len(source_records) > MAX_MESSAGES:
        raise ParseError(error_text("The export contains too many messages"))
    messages = []
    zone = ZoneInfo(timezone)
    participants: dict[str, str] = {}
    for seq, raw in enumerate(source_records):
        if not isinstance(raw, dict):
            continue
        sender_name = _get(raw, spec["sender_name"]) if spec["sender_name"] else None
        sender_id = _get(raw, spec["sender_id"]) if spec["sender_id"] else None
        identity = sender_id if sender_id is not None and sender_id != "" else sender_name
        uid = (
            str(identity).strip()
            if isinstance(identity, (str, int)) and not isinstance(identity, bool)
            else ""
        )
        text = _get(raw, spec["text"])
        timestamp = _timestamp_ms(
            _get(raw, spec["timestamp"]), zone, spec.get("timestamp_format", "")
        )
        if (
            not uid
            or len(uid) > 240
            or any(c in uid for c in "\r\n\0,")
            or not isinstance(text, str)
            or timestamp is None
        ):
            continue
        name = str(sender_name or uid).strip()[:240] or uid
        participants.setdefault(uid, name)
        # 内容标识用于重叠导出去重，同一文件中的重复消息再添加序号以保持独立。
        content = json.dumps([uid, timestamp, text], ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(content.encode()).hexdigest()[:32]
        kind = _get(raw, spec["kind"]) if spec["kind"] else "text"
        messages.append(
            {
                "id": f"ai-{digest}",
                "seq": seq,
                "timestamp_ms": timestamp,
                "sender_uid": uid,
                "sender_name": name,
                "kind": str(kind or "text"),
                "raw_type": str(kind or "text"),
                "text": text,
            }
        )
    if not messages or len(participants) < 2:
        raise ParseError(
            error_text("Conversion did not find valid messages from at least two participants")
        )
    counts: dict[str, int] = {}
    for message in messages:
        identity = message["id"]
        duplicate = counts.get(identity, 0)
        counts[identity] = duplicate + 1
        if duplicate:
            message["id"] += f"-{duplicate}"
    return {
        "afterglow": {"format": "afterglow-chat", "version": "1.0"},
        "conversation": {"id": title, "type": "private"},
        "participants": [
            {"uid": uid, "name": name, "role": "unknown"} for uid, name in participants.items()
        ],
        "messages": messages,
        "conversion_stats": {
            "source_records": len(source_records),
            "converted_messages": len(messages),
            "skipped_records": len(source_records) - len(messages),
            "unmatched_lines": unmatched_lines,
            **(
                {"source_warning": payload["_source_warning"]}
                if isinstance(payload, dict) and payload.get("_source_warning")
                else {}
            ),
        },
    }
