"""跨平台正文、稳定去重、图片目录和不完整导入的回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xuwen.config import Settings
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.chunker import build_friend_chunks
from xuwen.ingestion.parser import parse_messages
from xuwen.ingestion.plugins import TEXT_CONTENT_KEY, TEXT_SUFFIX_KEY
from xuwen.ingestion.splitter import split_sessions

TEXT = "Read [guide](https://example.test), values [1, 2], year [2026]."


def payload(platform, messages):
    if platform == "discord":
        return {
            "channel": {"id": "channel", "type": "DirectTextChat"},
            "guild": None,
            "exportedAt": "2026-10-19T00:00:00Z",
            "messages": [
                {
                    "id": str(mid),
                    "timestamp": f"2026-10-13T12:25:{mid:02d}Z",
                    "author": {"id": sender, "name": sender},
                    "type": "Default",
                    "content": text,
                }
                for mid, sender, text in messages
            ],
        }
    if platform == "telegram":
        html = '<div class="page_header"><div class="text">SyntheticChat</div></div><div class="history">'
        for mid, sender, text in messages:
            html += (
                f'<div class="message default" id="message{mid}"><div class="body">'
                f'<div class="date" title="13.10.2026 12:25:{mid:02d} UTC+00:00"></div>'
                f'<div class="from_name">{sender}</div><div class="text">{text}</div></div></div>'
            )
        return {TEXT_SUFFIX_KEY: ".html", TEXT_CONTENT_KEY: html + "</div>"}
    return {
        TEXT_SUFFIX_KEY: ".txt",
        TEXT_CONTENT_KEY: "\n".join(
            f"[10/13/26, 12:25:{mid:02d}] {sender}: {text}" for mid, sender, text in messages
        ),
    }


def settings(platform, **kwargs):
    prefix = "" if platform == "discord" else platform + ":"
    return Settings(
        _env_file=None,
        self_name="Me",
        friend_name="Friend",
        self_uid=prefix + "Me",
        friend_uid=prefix + "Friend",
        **kwargs,
    )


@pytest.mark.parametrize("platform", ["discord", "telegram", "whatsapp"])
def test_preserve_regular_brackets(platform):
    s = settings(platform)
    parsed = parse_messages(payload(platform, [(1, "Friend", TEXT)]), s)
    assert Cleaner(s).clean_many(parsed)[0].text == TEXT


@pytest.mark.parametrize("platform", ["discord", "telegram", "whatsapp"])
def test_native_message_chunk_identity_survives_export_range(platform):
    s = settings(platform)

    def chunks(messages):
        parsed = parse_messages(payload(platform, messages), s)
        return build_friend_chunks(split_sessions(Cleaner(s).clean_many(parsed), s), s)

    full = chunks([(1, "Me", "question"), (2, "Friend", "answer")])
    partial = chunks([(2, "Friend", "answer")])
    assert full[0].message_id == partial[0].message_id
    assert full[0].chunk_id == partial[0].chunk_id


@pytest.mark.parametrize("platform", ["discord", "telegram", "whatsapp"])
def test_response_pair_identity_survives_export_range(platform):
    from xuwen.ingestion.chunker import build_response_pair_chunks

    s = settings(platform)

    def pairs(messages):
        parsed = parse_messages(payload(platform, messages), s)
        sessions = split_sessions(Cleaner(s).clean_many(parsed), s)
        return build_response_pair_chunks(sessions, s)

    full = pairs([(1, "Friend", "earlier"), (2, "Me", "question"), (3, "Friend", "answer")])
    partial = pairs([(2, "Me", "question"), (3, "Friend", "answer")])
    assert full[0].chunk_id == partial[0].chunk_id


@pytest.mark.parametrize("platform", ["discord", "telegram", "whatsapp"])
def test_persona_dataset_preserves_regular_brackets(platform, tmp_path):
    import json
    from xuwen.persona.generator import load_persona_dataset

    data = payload(platform, [(1, "Friend", TEXT)])
    suffix = data.get(TEXT_SUFFIX_KEY, ".json")
    path = tmp_path / ("chat" + suffix)
    path.write_text(data.get(TEXT_CONTENT_KEY) or json.dumps(data), encoding="utf-8")
    dataset = load_persona_dataset([path], settings(platform))
    assert dataset.messages[0].text == TEXT


def test_missing_photo_cannot_borrow_from_sibling_export(tmp_path):
    from xuwen.ingestion.image_importer import _find_image_file, _index_image_files

    source = tmp_path / "chat1"
    source.mkdir()
    other = tmp_path / "chat2/photos"
    other.mkdir(parents=True)
    (other / "photo_1.jpg").write_bytes(b"wrong chat")
    assert (
        _find_image_file(
            tmp_path,
            _index_image_files(tmp_path),
            "photos/photo_1.jpg",
            source_dir=source,
            allow_global_fallback=False,
        )
        is None
    )


def test_ambiguous_photo_filename_is_not_silently_selected(tmp_path):
    from xuwen.ingestion.image_importer import _find_image_file, _index_image_files

    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "photo.jpg").write_bytes(name.encode())
    assert _find_image_file(tmp_path, _index_image_files(tmp_path), "photo.jpg") is None


@pytest.mark.asyncio
async def test_images_resolve_relative_to_own_chat_export(tmp_path):
    from xuwen.ingestion.image_importer import import_history_images

    for i in (1, 2):
        folder = tmp_path / f"chat{i}"
        (folder / "photos").mkdir(parents=True)
        (folder / "photos/photo_1.jpg").write_bytes(f"image-{i}".encode())
        data = payload("telegram", [(i, "Friend", "")])[TEXT_CONTENT_KEY]
        data = data.replace("SyntheticChat", f"Chat{i}").replace(
            '<div class="text"></div>',
            '<div class="media_wrap"><a class="photo_wrap" href="photos/photo_1.jpg"></a></div>',
        )
        (folder / "messages.html").write_text(data, encoding="utf-8")
    s = settings(
        "telegram",
        vision_enabled=True,
        vision_api_url="https://vision.test/v1",
        vision_api_key="synthetic",
        embedding_dim=3,
        image_data_dir=tmp_path / "cache",
    )
    store = SimpleNamespace(
        check_vector_dims=lambda _: None,
        list_history_images_by_ids=AsyncMock(return_value=[]),
        list_history_images_by_sha=AsyncMock(return_value=[]),
        upsert_history_image_chunks=AsyncMock(side_effect=lambda chunks, vectors: len(chunks)),
    )
    report = await import_history_images(
        tmp_path,
        s,
        store=store,
        embedder=SimpleNamespace(
            embed_texts=AsyncMock(side_effect=lambda texts: [[1.0, 0.0, 0.0]] * len(texts))
        ),
        vision_client=SimpleNamespace(
            describe_images=AsyncMock(
                side_effect=lambda urls: ["synthetic description"] * len(urls)
            )
        ),
    )
    assert report.total_refs == 2
    assert report.unique_images == 2


@pytest.mark.asyncio
async def test_partial_embedding_failure_is_visible_in_task(tmp_path, monkeypatch):
    from xuwen.core.models import ImportReport
    from xuwen.web_ui import import_tasks

    manager = import_tasks.ImportTaskManager()
    task = manager.create([tmp_path / "chat.json"], ["synthetic"])
    report = ImportReport(
        total_raw_messages=2,
        parsed_messages=2,
        skipped_messages=0,
        sessions=1,
        friend_chunks=2,
        window_chunks=1,
        response_pairs=1,
        embedded_friend=1,
        embedded_window=1,
        embedded_response_pairs=1,
        upserted_friend=1,
        upserted_window=1,
        upserted_response_pairs=1,
        duration_seconds=0.1,
        failed_friend=1,
        notes=["Skipped 1 chunks after embedding failures; retry import."],
    )
    monkeypatch.setattr(import_tasks, "get_manager", lambda: manager)
    monkeypatch.setattr(import_tasks, "_process_one_file", AsyncMock(return_value=report))
    monkeypatch.setattr(import_tasks, "_run_persona_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr(
        import_tasks, "_rebuild_proactive_profile_from_store", AsyncMock(return_value="ok")
    )
    await import_tasks.run_import_task(
        task.task_id, Settings(_env_file=None, lance_index_min_rows=0)
    )
    assert task.error, "A partial import must expose a user-visible warning"
    assert task.report["failed_chunks"] == 1


@pytest.mark.asyncio
async def test_resume_skips_are_not_reported_as_failures(tmp_path, monkeypatch):
    from xuwen.core.models import ImportReport
    from xuwen.web_ui import import_tasks

    manager = import_tasks.ImportTaskManager()
    task = manager.create([tmp_path / "chat.json"], ["synthetic"])
    report = ImportReport(
        total_raw_messages=2,
        parsed_messages=2,
        skipped_messages=0,
        sessions=1,
        friend_chunks=2,
        window_chunks=1,
        response_pairs=1,
        embedded_friend=0,
        embedded_window=0,
        embedded_response_pairs=0,
        upserted_friend=0,
        upserted_window=0,
        upserted_response_pairs=0,
        duration_seconds=0.1,
        notes=["Resume skipped stored chunks"],
    )
    monkeypatch.setattr(import_tasks, "get_manager", lambda: manager)
    monkeypatch.setattr(import_tasks, "_process_one_file", AsyncMock(return_value=report))
    monkeypatch.setattr(import_tasks, "_run_persona_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr(
        import_tasks, "_rebuild_proactive_profile_from_store", AsyncMock(return_value="ok")
    )
    await import_tasks.run_import_task(
        task.task_id, Settings(_env_file=None, lance_index_min_rows=0)
    )
    assert task.status == "done"
    assert task.error == ""
    assert task.report["failed_chunks"] == 0

