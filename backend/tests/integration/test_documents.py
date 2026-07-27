"""/v1/documents/* 端点测试。"""

from __future__ import annotations

import io

import pytest
import respx
from fastapi.testclient import TestClient

from xuwen.chat_api.app import create_app
from xuwen.config import Settings


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        persona_data_dir=tmp_path / "persona",
        image_data_dir=tmp_path / "images",
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        api_auth_required=False,
        enable_pii_redaction=False,
    )


def test_documents_formats_endpoint(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.get("/v1/documents/formats")
    assert r.status_code == 200
    exts = r.json()["extensions"]
    assert "txt" in exts and "pdf" in exts and "docx" in exts


def test_documents_extract_txt(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.post(
            "/v1/documents/extract",
            files={"file": ("note.txt", io.BytesIO("你好测试".encode()), "text/plain")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "note.txt"
    assert "你好测试" in body["text"]
    assert body["estimated_tokens"] > 0


def test_documents_extract_unsupported_format(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.post(
            "/v1/documents/extract",
            files={"file": ("x.bin", io.BytesIO(b"binary"), "application/octet-stream")},
        )
    assert r.status_code == 400
    assert "不支持" in r.json()["detail"]


def test_documents_extract_empty_rejected(settings: Settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.post(
            "/v1/documents/extract",
            files={"file": ("e.txt", io.BytesIO(b""), "text/plain")},
        )
    assert r.status_code == 400
