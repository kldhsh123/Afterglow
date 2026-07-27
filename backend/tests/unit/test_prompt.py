"""prompt 模板渲染测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from xuwen.config import Settings
from xuwen.core.errors import ConfigError
from xuwen.core.models import RetrievalResult, ScoredChunk
from xuwen.persona.prompt import ChatMessage, build_chat_messages, dump_prompt


def _settings(**overrides):
    defaults = dict(
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        relationship_type="friend",
        persona_template="xuwen",
        chat_model="gpt-4o-mini",
        openai_api_key="sk-test",  # type: ignore[arg-type]
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _empty_result():
    return RetrievalResult(
        friend_examples=[],
        dialogue_windows=[],
        recent_live=[],
        response_pairs=[],
        fused=[],
    )


def _chunk(chunk_id, text, ts=0, kind="friend", source="history"):
    return ScoredChunk(
        chunk_id=chunk_id,
        kind=kind,
        text=text,
        score=1.0,
        rank=1,
        timestamp_ms=ts,
        source=source,
    )


def test_build_messages_renders_friend_and_self_names():
    settings = _settings()
    messages = build_chat_messages(
        settings=settings,
        persona_card="# TA 画像\n爱笑爱闹。",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="在干嘛",
    )
    assert messages[-1] == {"role": "user", "content": "在干嘛"}
    system = messages[0]["content"]
    assert "TA" in system
    assert "Me" in system
    # xuwen 模板特征：明确的扮演 + relationship_description
    assert "朋友" in system
    assert "【运行时上下文】" in system
    assert "真实当前时间" in system
    assert "当前时区：Asia/Shanghai" in system


def test_build_messages_uses_relationship_description():
    settings = _settings(relationship_type="lover")
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="想你了",
    )
    assert "恋人" in messages[0]["content"]


def test_build_messages_custom_relationship_requires_description():
    """RELATIONSHIP_TYPE=custom 但没填 RELATIONSHIP_DESCRIPTION 应在启动期就报错。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(relationship_type="custom", relationship_description="")


def test_build_messages_custom_relationship_with_description_works():
    settings = _settings(
        relationship_type="custom",
        relationship_description="高中同桌",
        persona_template="friend",
    )
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="hi",
    )
    assert "高中同桌" in messages[0]["content"]


def test_build_messages_includes_friend_examples():
    settings = _settings()
    retrieved = RetrievalResult(
        friend_examples=[_chunk("c1", "嗯嗯，慢慢说", ts=1700000000000)],
        dialogue_windows=[],
        recent_live=[],
        response_pairs=[],
        fused=[],
    )
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=retrieved,
        recent=[],
        current_user_message="今天有点累",
    )
    assert "嗯嗯，慢慢说" in messages[0]["content"]


def test_build_messages_includes_web_context_when_provided():
    settings = _settings()
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="查一下今天新闻",
        web_context="[1] 示例新闻\n来源：https://example.test\n摘要：一条摘要",
    )
    system = messages[0]["content"]
    assert "【联网检索结果】" in system
    assert "示例新闻" in system
    assert "必须优先基于【联网检索结果】概括回答" in system
    assert "不要用“我没看新闻”“刚醒”“不知道”“你看到什么了吗”等生活状态来回避" in system


def test_build_messages_without_web_context_forbids_fake_search():
    settings = _settings()
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="今天有什么新闻嘛",
    )
    system = messages[0]["content"]
    assert "未提供联网检索结果" in system
    assert "不要假装已经查询互联网" in system
    assert "这边暂时没查到" in system


def test_build_messages_includes_url_context_when_provided():
    settings = _settings()
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="看看 https://example.com",
        url_context="[1] 示例页面\nURL：https://example.com\n正文摘录：网页正文",
    )
    system = messages[0]["content"]
    assert "【网页读取结果】" in system
    assert "示例页面" in system
    assert "回答必须基于【网页读取结果】" in system
    assert "不要说自己打不开链接" in system


def test_build_messages_without_url_context_forbids_fake_fetch():
    settings = _settings()
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="看看 https://example.com",
    )
    system = messages[0]["content"]
    assert "未提供网页读取结果" in system
    assert "不要假装已经打开或读过链接" in system


def test_build_messages_renders_response_pair_evidence():
    settings = _settings()
    pair = _chunk("p1", "Me: 你在干嘛\nTA: 刚吃完饭", kind="response_pair")
    pair.metadata.update({"text": "你在干嘛", "friend_reply": "刚吃完饭"})
    retrieved = RetrievalResult(
        friend_examples=[pair],
        dialogue_windows=[],
        recent_live=[],
        response_pairs=[pair],
        fused=[pair],
    )
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=retrieved,
        recent=[],
        current_user_message="你在干什么",
    )
    system = messages[0]["content"]
    assert "当 Me 说：你在干嘛" in system
    assert "TA 当时回复：刚吃完饭" in system


def test_build_messages_includes_recent_history():
    settings = _settings()
    recent = [
        ChatMessage(role="user", content="上次说的事"),
        ChatMessage(role="assistant", content="嗯，记得"),
    ]
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=recent,
        current_user_message="想再聊聊",
    )
    # system 应渲染最近对话
    assert "上次说的事" in messages[0]["content"]
    assert "嗯，记得" in messages[0]["content"]
    # 同时也作为独立 messages 跟在 system 后面
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "上次说的事"
    assert messages[2]["role"] == "assistant"


def test_build_messages_includes_no_new_emoji_guard():
    settings = _settings(persona_template="lover", relationship_type="lover")
    retrieved = RetrievalResult(
        friend_examples=[
            _chunk(
                "c1",
                "想你了\n抱住\n[[爱心]]",
                ts=1700000000000,
            )
        ],
        dialogue_windows=[],
        recent_live=[],
        response_pairs=[],
        fused=[],
    )
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=retrieved,
        recent=[],
        current_user_message="想你了",
    )
    system = messages[0]["content"]
    assert "不要新增历史片段里没有出现过的 Unicode emoji" in system
    assert "不要把 QQ / 聊天导出的占位符转换成 emoji" in system
    assert "不要把你之前生成过的回复当作 emoji / 颜文字使用依据" in system
    assert "不要输出 `[图片]`、`[语音]`、`[视频]`" in system
    assert "不要主动加入“想你”“有没有想我”“抱抱”“亲亲”“爱你”等亲密内容" in system
    assert "不要因为用户问“在干嘛”“在吗”等寒暄" in system
    assert "[[爱心]]" in system


def test_build_messages_empty_user_raises():
    settings = _settings()
    with pytest.raises(ConfigError):
        build_chat_messages(
            settings=settings,
            persona_card="",
            retrieved=_empty_result(),
            recent=[],
            current_user_message="   ",
        )


def test_all_builtin_templates_render():
    """五个内置模板都应能被加载并渲染。"""
    for name in ["xuwen", "friend", "lover", "family", "colleague"]:
        settings = _settings(persona_template=name)
        messages = build_chat_messages(
            settings=settings,
            persona_card="",
            retrieved=_empty_result(),
            recent=[],
            current_user_message="hi",
        )
        assert messages[0]["content"].strip()


def test_unknown_template_raises():
    settings = _settings(persona_template="nonexistent")
    with pytest.raises(ConfigError):
        build_chat_messages(
            settings=settings,
            persona_card="",
            retrieved=_empty_result(),
            recent=[],
            current_user_message="hi",
        )


def test_custom_template_dir(tmp_path: Path):
    """用户可通过 PROMPT_TEMPLATE_DIR 覆盖内置模板。"""
    custom = tmp_path / "my.md.j2"
    custom.write_text(
        "自定义模板。friend={{ friend_name }}, self={{ self_name }}.",
        encoding="utf-8",
    )
    settings = _settings(
        prompt_template_dir=tmp_path,
        persona_template="my",
    )
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="hi",
    )
    assert "自定义模板" in messages[0]["content"]
    assert "friend=TA" in messages[0]["content"]


def test_dump_prompt_returns_valid_json():
    settings = _settings()
    messages = build_chat_messages(
        settings=settings,
        persona_card="",
        retrieved=_empty_result(),
        recent=[],
        current_user_message="hi",
    )
    import json

    parsed = json.loads(dump_prompt(messages))
    assert isinstance(parsed, list)
