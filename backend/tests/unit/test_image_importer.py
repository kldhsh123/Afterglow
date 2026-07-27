"""历史图片导入测试。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from xuwen.config import Settings
from xuwen.ingestion.image_importer import import_history_images


class FakeStore:
    def __init__(
        self,
        *,
        rows_by_id: dict[str, dict[str, Any]] | None = None,
        rows_by_sha: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.rows = []
        self.rows_by_id = rows_by_id or {}
        self.rows_by_sha = rows_by_sha or {}
        self.soft_deleted: list[str] = []
        self.list_by_ids_calls = 0
        self.list_by_sha_calls = 0

    async def existing_ids(self, _table: str, _ids: list[str]) -> set[str]:
        return set()

    async def list_history_images_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        self.list_by_ids_calls += 1
        return [self.rows_by_id[i] for i in ids if i in self.rows_by_id]

    async def list_history_images_by_sha(self, sha: str, limit: int = 100) -> list[dict[str, Any]]:
        self.list_by_sha_calls += 1
        return list(self.rows_by_sha.get(sha, []))[:limit]

    async def soft_delete_ids(self, _table: str, ids: list[str]) -> int:
        self.soft_deleted.extend(ids)
        return len(ids)

    async def upsert_history_image_chunks(self, chunks, embeddings) -> int:
        self.rows.extend((chunk, embeddings[chunk.chunk_id]) for chunk in chunks)
        return len(chunks)


class FakeEmbedder:
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(i + 1), 0.0, 0.0] for i, _text in enumerate(texts)]

    async def aclose(self) -> None:
        pass


class FakeVision:
    def __init__(self) -> None:
        self.calls = 0

    async def describe_images(self, data_urls: list[str]) -> list[str]:
        self.calls += len(data_urls)
        return ["一张测试图片"] * len(data_urls)

    async def aclose(self) -> None:
        pass


def test_import_history_images_supports_weflow_jsonl_media_directory(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    media_dir = export_dir / "media" / "images"
    media_dir.mkdir(parents=True)
    (media_dir / "photo.jpg").write_bytes(b"weflow-image")
    (export_dir / "chat.jsonl").write_text(
        "\n".join(
            [
                '{"_type":"header","chatlab":{"generator":"WeFlow"},"meta":{"name":"Friend","platform":"wechat","type":"private"}}',
                '{"_type":"member","platformId":"wxid_me","accountName":"Me"}',
                '{"_type":"member","platformId":"wxid_friend","accountName":"Friend"}',
                '{"_type":"message","sender":"wxid_friend","accountName":"Friend","timestamp":1700000000,"type":7,"content":"media/images/photo.jpg","platformMessageId":"m1"}',
            ]
        ),
        encoding="utf-8",
    )

    store = FakeStore()
    vision = FakeVision()
    settings = Settings(
        self_uid="wxid_me",
        friend_uid="wxid_friend",
        self_name="Me",
        friend_name="Friend",
        vision_enabled=True,
        vision_api_url="https://vision.test/v1",
        vision_api_key="sk-test",
        embedding_dim=3,
        image_data_dir=tmp_path / "cache",
    )

    report = asyncio.run(
        import_history_images(
            export_dir,
            settings,
            store=store,  # type: ignore[arg-type]
            embedder=FakeEmbedder(),  # type: ignore[arg-type]
            vision_client=vision,  # type: ignore[arg-type]
        )
    )

    assert report.total_refs == 1
    assert report.matched_files == 1
    assert report.upserted_rows == 1
    assert store.rows[0][0].image_name == "photo.jpg"


def test_import_history_images_dedupes_by_sha(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    images_dir = export_dir / "resources" / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "a.jpg").write_bytes(b"same-image")
    (images_dir / "copy.jpg").write_bytes(b"same-image")
    payload = {
        "afterglow": {"format": "afterglow-chat", "version": "1.0"},
        "conversation": {"type": "private"},
        "participants": [
            {"uid": "me", "name": "Me", "role": "self"},
            {"uid": "friend", "name": "Friend", "role": "friend"},
        ],
        "messages": [
            {
                "id": "m1",
                "seq": 1,
                "timestamp_ms": 1000,
                "sender_uid": "friend",
                "kind": "placeholder",
                "attachments": [{"type": "image", "name": "a.jpg"}],
            },
            {
                "id": "m2",
                "seq": 2,
                "timestamp_ms": 2000,
                "sender_uid": "friend",
                "kind": "placeholder",
                "attachments": [{"type": "image", "name": "copy.jpg"}],
            },
        ],
    }
    (export_dir / "chat.json").write_text(json.dumps(payload), encoding="utf-8")

    store = FakeStore()
    vision = FakeVision()
    settings = Settings(
        self_uid="me",
        friend_uid="friend",
        self_name="Me",
        friend_name="Friend",
        vision_enabled=True,
        vision_api_url="https://vision.test/v1",
        vision_api_key="sk-test",
        embedding_dim=3,
        image_data_dir=tmp_path / "cache",
    )
    report = asyncio.run(
        import_history_images(
            export_dir,
            settings,
            store=store,  # type: ignore[arg-type]
            embedder=FakeEmbedder(),  # type: ignore[arg-type]
            vision_client=vision,  # type: ignore[arg-type]
            plugin_name="afterglow_v1",
        )
    )

    assert report.total_refs == 2
    assert report.unique_images == 1
    assert vision.calls == 1
    assert report.upserted_rows == 2
    assert len(store.rows) == 2
    assert len(list((tmp_path / "cache").iterdir())) == 1


def test_import_history_images_matches_qq_suffix_filename(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    images_dir = export_dir / "resources" / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "abc_A.JPG").write_bytes(b"image-a")
    payload = {
        "metadata": {"name": "QQChatExporter V5"},
        "chatInfo": {"selfUid": "me", "selfName": "Me", "type": "private"},
        "messages": [
            {
                "id": "m1",
                "seq": "1",
                "timestamp": 1000,
                "sender": {"uid": "friend", "name": "Friend"},
                "type": "type_1",
                "content": {
                    "text": "[图片: A.JPG]",
                    "resources": [{"type": "image", "filename": "A.JPG"}],
                    "elements": [],
                },
            }
        ],
    }
    (export_dir / "chat.json").write_text(json.dumps(payload), encoding="utf-8")

    store = FakeStore()
    vision = FakeVision()
    settings = Settings(
        self_uid="me",
        friend_uid="friend",
        self_name="Me",
        friend_name="Friend",
        vision_enabled=True,
        vision_api_url="https://vision.test/v1",
        vision_api_key="sk-test",
        embedding_dim=3,
        image_data_dir=tmp_path / "cache",
    )
    report = asyncio.run(
        import_history_images(
            export_dir,
            settings,
            store=store,  # type: ignore[arg-type]
            embedder=FakeEmbedder(),  # type: ignore[arg-type]
            vision_client=vision,  # type: ignore[arg-type]
            plugin_name="qqexporter_v5",
        )
    )

    assert report.matched_files == 1
    assert report.upserted_rows == 1


def test_import_history_images_prefers_qq_local_path_over_url(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    images_dir = export_dir / "resources" / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "local-image.jpg").write_bytes(b"image-a")
    payload = {
        "metadata": {"name": "QQChatExporter V5"},
        "chatInfo": {"selfUid": "me", "selfName": "Me", "type": "private"},
        "messages": [
            {
                "id": "m1",
                "seq": "1",
                "timestamp": 1000,
                "sender": {"uid": "friend", "name": "Friend"},
                "type": "type_1",
                "content": {
                    "text": "[图片]",
                    "resources": [
                        {
                            "type": "image",
                            "url": "https://cdn.invalid/remote-name.jpg?token=dead",
                            "localPath": "resources/images/local-image.jpg",
                        }
                    ],
                    "elements": [],
                },
            }
        ],
    }
    (export_dir / "chat.json").write_text(json.dumps(payload), encoding="utf-8")

    store = FakeStore()
    vision = FakeVision()
    settings = Settings(
        self_uid="me",
        friend_uid="friend",
        self_name="Me",
        friend_name="Friend",
        vision_enabled=True,
        vision_api_url="https://vision.test/v1",
        vision_api_key="sk-test",
        embedding_dim=3,
        image_data_dir=tmp_path / "cache",
    )

    report = asyncio.run(
        import_history_images(
            export_dir,
            settings,
            store=store,  # type: ignore[arg-type]
            embedder=FakeEmbedder(),  # type: ignore[arg-type]
            vision_client=vision,  # type: ignore[arg-type]
            plugin_name="qqexporter_v5",
        )
    )

    assert report.matched_files == 1
    assert report.missing_files == 0
    assert store.rows[0][0].image_name == "local-image.jpg"


def test_import_history_images_reuses_later_valid_sha_description(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    images_dir = export_dir / "resources" / "images"
    images_dir.mkdir(parents=True)
    raw_image = b"image-a"
    (images_dir / "a.jpg").write_bytes(raw_image)
    sha = hashlib.sha256(raw_image).hexdigest()
    payload = {
        "afterglow": {"format": "afterglow-chat", "version": "1.0"},
        "conversation": {"type": "private"},
        "participants": [
            {"uid": "me", "name": "Me", "role": "self"},
            {"uid": "friend", "name": "Friend", "role": "friend"},
        ],
        "messages": [
            {
                "id": "m1",
                "seq": 1,
                "timestamp_ms": 1000,
                "sender_uid": "friend",
                "kind": "placeholder",
                "attachments": [{"type": "image", "name": "a.jpg"}],
            }
        ],
    }
    (export_dir / "chat.json").write_text(json.dumps(payload), encoding="utf-8")

    store = FakeStore(
        rows_by_sha={
            sha: [
                {"id": "old-failed", "image_sha": sha, "description": "[图片：识别失败]"},
                {"id": "old-valid", "image_sha": sha, "description": "已有有效摘要"},
            ]
        }
    )
    vision = FakeVision()
    settings = Settings(
        self_uid="me",
        friend_uid="friend",
        self_name="Me",
        friend_name="Friend",
        vision_enabled=True,
        vision_api_url="https://vision.test/v1",
        vision_api_key="sk-test",
        embedding_dim=3,
        image_data_dir=tmp_path / "cache",
    )

    report = asyncio.run(
        import_history_images(
            export_dir,
            settings,
            store=store,  # type: ignore[arg-type]
            embedder=FakeEmbedder(),  # type: ignore[arg-type]
            vision_client=vision,  # type: ignore[arg-type]
            plugin_name="afterglow_v1",
        )
    )

    assert vision.calls == 0
    assert report.upserted_rows == 1
    assert store.rows[0][0].description == "已有有效摘要"


def test_import_history_images_batches_existing_row_classification(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    images_dir = export_dir / "resources" / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "valid.jpg").write_bytes(b"valid-image")
    (images_dir / "failed.jpg").write_bytes(b"failed-image")
    valid_sha = hashlib.sha256(b"valid-image").hexdigest()
    failed_sha = hashlib.sha256(b"failed-image").hexdigest()
    valid_chunk_id = f"image-m1-{valid_sha[:16]}"
    failed_chunk_id = f"image-m2-{failed_sha[:16]}"
    payload = {
        "afterglow": {"format": "afterglow-chat", "version": "1.0"},
        "conversation": {"type": "private"},
        "participants": [
            {"uid": "me", "name": "Me", "role": "self"},
            {"uid": "friend", "name": "Friend", "role": "friend"},
        ],
        "messages": [
            {
                "id": "m1",
                "seq": 1,
                "timestamp_ms": 1000,
                "sender_uid": "friend",
                "kind": "placeholder",
                "attachments": [{"type": "image", "name": "valid.jpg"}],
            },
            {
                "id": "m2",
                "seq": 2,
                "timestamp_ms": 2000,
                "sender_uid": "friend",
                "kind": "placeholder",
                "attachments": [{"type": "image", "name": "failed.jpg"}],
            },
        ],
    }
    (export_dir / "chat.json").write_text(json.dumps(payload), encoding="utf-8")

    store = FakeStore(
        rows_by_id={
            valid_chunk_id: {
                "id": valid_chunk_id,
                "image_sha": valid_sha,
                "description": "已有有效摘要",
                "deleted": False,
            },
            failed_chunk_id: {
                "id": failed_chunk_id,
                "image_sha": failed_sha,
                "description": "[图片：识别失败]",
                "deleted": False,
            },
        }
    )
    vision = FakeVision()
    settings = Settings(
        self_uid="me",
        friend_uid="friend",
        self_name="Me",
        friend_name="Friend",
        vision_enabled=True,
        vision_api_url="https://vision.test/v1",
        vision_api_key="sk-test",
        embedding_dim=3,
        image_data_dir=tmp_path / "cache",
    )

    report = asyncio.run(
        import_history_images(
            export_dir,
            settings,
            store=store,  # type: ignore[arg-type]
            embedder=FakeEmbedder(),  # type: ignore[arg-type]
            vision_client=vision,  # type: ignore[arg-type]
            plugin_name="afterglow_v1",
        )
    )

    assert store.list_by_ids_calls == 1
    assert store.soft_deleted == [failed_chunk_id]
    assert vision.calls == 1
    assert report.skipped_existing_rows == 1
    assert report.upserted_rows == 1
    assert store.rows[0][0].chunk_id == failed_chunk_id
