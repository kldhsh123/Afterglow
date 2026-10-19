"""Backend startup language is independent of the browser locale."""

import pytest

from xuwen.config import Settings
from xuwen.web_ui.app import _log_startup_banner
from xuwen.web_ui.first_run import FirstRunStatus


@pytest.mark.parametrize(
    ("language", "expected"),
    [(None, "First-run setup detected"), ("en", "First-run setup detected"), ("zh-CN", "检测到首次配置")],
)
def test_startup_banner_language(monkeypatch, capsys, language, expected):
    monkeypatch.delenv("XUWEN_LANGUAGE", raising=False)
    if language is not None:
        monkeypatch.setenv("XUWEN_LANGUAGE", language)
    _log_startup_banner(
        Settings(_env_file=None),
        "test-token",
        "generated",
        FirstRunStatus(is_first_run=True, missing_keys=["SELF_NAME"]),
        "http://127.0.0.1:8000",
    )
    output = capsys.readouterr().out
    assert expected in output
    assert "test-token" in output
    assert "http://127.0.0.1:8000/config/" in output
