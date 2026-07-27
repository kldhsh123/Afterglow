"""metrics + /debug/* 单测。"""

from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient

from xuwen.chat_api.app import create_app
from xuwen.config import Settings
from xuwen.core.metrics import MetricsRecorder


def test_metrics_recorder_basic():
    r = MetricsRecorder(capacity=10)
    r.record("llm", 100.0)
    r.record("llm", 200.0)
    r.record("llm", 300.0, error="LLMError")
    s = r.stats("llm")
    assert s.count == 3
    assert s.error_count == 1
    assert s.avg_latency_ms == pytest.approx(200.0)
    assert s.error_rate == pytest.approx(1 / 3, rel=1e-3)


def test_metrics_recorder_p50_p95():
    r = MetricsRecorder(capacity=200)
    for i in range(1, 101):
        r.record("call", float(i))
    s = r.stats("call")
    assert s.p50_latency_ms == 51.0  # 排序后第 50 个（0-indexed 50）
    assert 90 <= s.p95_latency_ms <= 100


def test_metrics_recorder_circular_buffer():
    r = MetricsRecorder(capacity=3)
    for i in range(10):
        r.record("k", float(i))
    s = r.stats("k")
    # 只保留最后 3 条
    assert s.count == 3
    assert {rec.latency_ms for rec in s.last_records} == {7.0, 8.0, 9.0}


def test_metrics_recorder_reset():
    r = MetricsRecorder()
    r.record("a", 10)
    r.record("b", 20)
    assert r.kinds() == ["a", "b"]
    r.reset("a")
    assert r.kinds() == ["b"]
    r.reset()
    assert r.kinds() == []


@pytest.fixture()
def settings_with_debug(tmp_path) -> Settings:
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
        debug_endpoints_enabled=True,
        enable_pii_redaction=False,
    )


def test_debug_stats_endpoint(settings_with_debug: Settings):
    app = create_app(settings_with_debug)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.get("/debug/stats")
    assert r.status_code == 200
    body = r.json()
    assert "memory" in body
    assert "writeback" in body
    assert "calls" in body
    assert body["memory"]["friend_messages"] == 0


def test_debug_config_endpoint_does_not_leak_secrets(settings_with_debug: Settings):
    app = create_app(settings_with_debug)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.get("/debug/config")
    assert r.status_code == 200
    body = r.json()
    # 应有"是否配置"标志而非具体 key
    assert "api_keys_configured" in body
    assert body["api_keys_configured"]["openai"] is True
    # 文本里不应出现 "sk-test"
    import json

    text = json.dumps(body)
    assert "sk-test" not in text


def test_debug_metrics_reset(settings_with_debug: Settings):
    app = create_app(settings_with_debug)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.post("/debug/metrics/reset")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_debug_endpoints_disabled(tmp_path):
    settings = Settings(
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
        debug_endpoints_enabled=False,
    )
    app = create_app(settings)
    with respx.mock(assert_all_called=False), TestClient(app) as client:
        r = client.get("/debug/stats")
    assert r.status_code == 404
