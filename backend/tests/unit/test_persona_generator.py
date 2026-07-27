"""多文件 persona 聚合测试。"""

from __future__ import annotations

import json
from pathlib import Path

from xuwen.config import Settings
from xuwen.persona.generator import generate_persona_artifacts, load_persona_dataset


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        self_name="我",
        self_uid="me",
        friend_name="TA",
        friend_uid="friend",
        persona_data_dir=tmp_path / "persona",
    )


def _write_afterglow(path: Path, messages: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "afterglow": {"format": "afterglow-chat", "version": "1.0"},
                "conversation": {"type": "private"},
                "participants": [
                    {"uid": "me", "name": "我", "role": "self"},
                    {"uid": "friend", "name": "TA", "role": "friend"},
                ],
                "messages": messages,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_persona_dataset_merges_same_plugin_files_in_timestamp_order(tmp_path: Path) -> None:
    earlier = tmp_path / "earlier.json"
    later = tmp_path / "later.json"
    _write_afterglow(
        earlier,
        [
            {
                "id": "m1",
                "seq": 1,
                "timestamp_ms": 1_700_000_000_000,
                "sender_uid": "me",
                "sender_name": "我",
                "kind": "text",
                "text": "在干嘛",
            }
        ],
    )
    _write_afterglow(
        later,
        [
            {
                "id": "m2",
                "seq": 2,
                "timestamp_ms": 1_700_000_060_000,
                "sender_uid": "friend",
                "sender_name": "TA",
                "kind": "text",
                "text": "刚吃完饭呢",
            }
        ],
    )

    dataset = load_persona_dataset([later, earlier], _settings(tmp_path))

    assert len(dataset.sessions) == 1
    assert [message.message_id for message in dataset.sessions[0].messages] == ["m1", "m2"]


def test_persona_generation_deduplicates_overlapping_chunks(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    duplicated = {
        "id": "m2",
        "seq": 2,
        "timestamp_ms": 1_700_000_060_000,
        "sender_uid": "friend",
        "sender_name": "TA",
        "kind": "text",
        "text": "刚吃完饭呢",
    }
    _write_afterglow(
        first,
        [
            {
                "id": "m1",
                "seq": 1,
                "timestamp_ms": 1_700_000_000_000,
                "sender_uid": "me",
                "sender_name": "我",
                "kind": "text",
                "text": "在干嘛",
            },
            duplicated,
        ],
    )
    _write_afterglow(
        second,
        [
            duplicated,
            {
                "id": "m3",
                "seq": 3,
                "timestamp_ms": 1_700_000_120_000,
                "sender_uid": "friend",
                "sender_name": "TA",
                "kind": "text",
                "text": "准备休息啦",
            },
        ],
    )

    result = generate_persona_artifacts([first, second], _settings(tmp_path))

    assert result.source_files == 2
    assert result.parsed_messages == 4
    assert result.duplicate_messages == 1
    assert result.unique_messages == 3
    assert result.friend_messages == 2
    assert result.circadian_sample_size == 2
    assert result.report_path.exists()
    assert result.style_profile_path.exists()


def test_persona_dataset_deduplicates_jsonl_fallback_ids_by_content(tmp_path: Path) -> None:
    first = tmp_path / "chunk-001.jsonl"
    second = tmp_path / "chunk-002.jsonl"
    record = {
        "timestamp_ms": 1_700_000_060_000,
        "sender_uid": "friend",
        "sender_name": "TA",
        "kind": "text",
        "text": "重复分片消息",
    }
    encoded = json.dumps(record, ensure_ascii=False)
    first.write_text(encoded, encoding="utf-8")
    second.write_text(encoded, encoding="utf-8")

    dataset = load_persona_dataset([first, second], _settings(tmp_path))

    assert dataset.parsed_messages == 2
    assert dataset.duplicate_messages == 1
    assert len(dataset.messages) == 1


def test_persona_dataset_keeps_different_plugins_in_separate_sessions(tmp_path: Path) -> None:
    afterglow = tmp_path / "afterglow.json"
    qq = tmp_path / "qq.json"
    _write_afterglow(
        afterglow,
        [
            {
                "id": "a1",
                "seq": 1,
                "timestamp_ms": 1_700_000_000_000,
                "sender_uid": "me",
                "sender_name": "我",
                "kind": "text",
                "text": "在干嘛",
            }
        ],
    )
    qq.write_text(
        json.dumps(
            {
                "metadata": {"name": "QQChatExporter"},
                "chatInfo": {"selfUid": "me"},
                "messages": [
                    {
                        "id": "q1",
                        "seq": 1,
                        "timestamp": 1_700_000_060_000,
                        "sender": {"uid": "friend", "name": "TA"},
                        "type": "type_1",
                        "content": {"text": "刚吃完饭呢", "elements": [], "resources": []},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    dataset = load_persona_dataset([afterglow, qq], _settings(tmp_path))

    assert len(dataset.sessions) == 2
