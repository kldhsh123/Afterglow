"""plugin 系统单测：注册、自动匹配、强制指定、未知格式拒绝。"""

from __future__ import annotations

import pytest

from xuwen.core.errors import ParseError
from xuwen.core.models import NormalizedMessage
from xuwen.ingestion.parser import detect_plugin, parse_messages
from xuwen.ingestion.plugins import (
    JSONL_RECORDS_KEY,
    find_plugin,
    jsonl_records,
    list_plugins,
    register_plugin,
    select_plugin,
)
from xuwen.ingestion.plugins.qqexporter_v5 import QQExporterV5Plugin
from xuwen.ingestion.plugins.wechat_weflow import WeChatWeFlowPlugin


def test_jsonl_records_rejects_empty_or_malformed_internal_payload() -> None:
    assert jsonl_records({}) is None
    assert jsonl_records({JSONL_RECORDS_KEY: []}) is None
    assert jsonl_records({JSONL_RECORDS_KEY: [{"_type": "header"}, None]}) is None
    assert jsonl_records({JSONL_RECORDS_KEY: [{"_type": "header"}]}) == [
        {"_type": "header"}
    ]


def test_qq_plugin_registered_by_default():
    """import parser 时应该自动注册 QQ plugin。"""
    plugins = list_plugins()
    assert any(p.name == "qqexporter_v5" for p in plugins)


def test_qq_plugin_matches_metadata():
    p = QQExporterV5Plugin()
    assert p.match(
        {"metadata": {"name": "QQChatExporter V5 / https://github.com/..."}}
    )
    assert p.match({"chatInfo": {"selfUid": "u_xxx"}})
    assert not p.match({"messages": []})  # 没有特征字段
    assert not p.match({"metadata": {"name": "WeChat Backup"}})


def test_weflow_plugin_matches_chatlab_jsonl_payload():
    plugin = WeChatWeFlowPlugin()
    assert plugin.match(
        {
            "chatlab": {"generator": "WeFlow", "version": "0.0.2"},
            "meta": {"platform": "wechat", "type": "private"},
            "messages": [],
        }
    )


def test_detect_plugin_returns_matched(sample_payload):
    detected = detect_plugin(sample_payload)
    assert detected is not None
    assert detected.name == "qqexporter_v5"


def test_select_plugin_explicit_overrides(sample_payload):
    """显式指定 plugin 不再做 match 校验。"""
    plugin = select_plugin(sample_payload, preferred="qqexporter_v5")
    assert plugin.name == "qqexporter_v5"


def test_select_plugin_unknown_name_raises():
    with pytest.raises(ParseError) as exc:
        select_plugin({}, preferred="not_a_real_plugin")
    assert "未知" in exc.value.message or "未知" in str(exc.value)


def test_select_plugin_no_match_raises():
    with pytest.raises(ParseError):
        select_plugin({"random": "data"})


def test_parse_messages_via_plugin(sample_payload, settings_for_sample):
    msgs = parse_messages(sample_payload, settings_for_sample)
    assert len(msgs) > 0
    assert isinstance(msgs[0], NormalizedMessage)


def test_register_replaces_duplicate_name():
    """重名 plugin 注册应替换旧的，不允许同名共存。"""

    class FakePlugin:
        name = "qqexporter_v5"  # 故意同名
        display_name = "Fake QQ"

        def match(self, payload):
            return False

        def parse(self, payload, settings):
            return []

    fake = FakePlugin()
    register_plugin(fake)
    # 同名应被替换
    found = find_plugin("qqexporter_v5")
    assert found is fake

    # 恢复
    register_plugin(QQExporterV5Plugin())
    found2 = find_plugin("qqexporter_v5")
    assert isinstance(found2, QQExporterV5Plugin)


def test_parse_messages_with_plugin_name(sample_payload, settings_for_sample):
    """显式指定 plugin name 应能跳过 match。"""
    msgs = parse_messages(
        sample_payload, settings_for_sample, plugin_name="qqexporter_v5"
    )
    assert len(msgs) > 0


def test_plugin_protocol_runtime_check():
    """QQExporterV5Plugin 应符合 ImportPlugin 协议。"""
    from xuwen.ingestion.plugins import ImportPlugin

    p = QQExporterV5Plugin()
    assert isinstance(p, ImportPlugin)
