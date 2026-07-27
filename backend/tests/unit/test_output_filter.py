"""assistant 输出过滤测试。"""

from __future__ import annotations

from xuwen.chat_api.output_filter import AssistantOutputFilter, sanitize_assistant_text


def test_sanitize_assistant_text_removes_history_placeholders():
    assert sanitize_assistant_text("[图片]: 在干嘛") == "在干嘛"
    assert sanitize_assistant_text("[图片]在干嘛") == "在干嘛"
    assert sanitize_assistant_text("[[呜呜呜]] 想你") == "想你"
    assert sanitize_assistant_text("[语音]") == "嗯"


def test_sanitize_assistant_text_removes_qq_native_face():
    """旧库未清洗的 `[/汪汪]` 被检索后可能让模型直接复读，输出层必须兜底。"""
    assert sanitize_assistant_text("好的[/汪汪]") == "好的"
    assert sanitize_assistant_text("[/呲牙] 晚安") == "晚安"
    assert sanitize_assistant_text("嗯嗯[/破涕为笑]今天好累[/汪汪]") == "嗯嗯今天好累"
    # 纯 [/xxx] 的输出应被替换为兜底文本，不让前端看到诡异字符串
    assert sanitize_assistant_text("[/汪汪]") == "嗯"


def test_sanitize_assistant_text_keeps_sticker_tokens():
    assert sanitize_assistant_text("摸摸 [sticker:摸摸头]") == "摸摸 [sticker:摸摸头]"


def test_sanitize_assistant_text_removes_trailing_partial_sticker():
    assert sanitize_assistant_text("[sticker:e7e") == "嗯"
    assert sanitize_assistant_text("等我一下 [sticker:e7e") == "等我一下"
    assert sanitize_assistant_text("摸摸 [sticker:摸摸头]") == "摸摸 [sticker:摸摸头]"


def test_stream_filter_handles_split_placeholder():
    f = AssistantOutputFilter()
    out = [
        f.feed("[图"),
        f.feed("片]: 在"),
        f.feed("干嘛"),
        f.flush(),
    ]
    assert "".join(out) == "在干嘛"


def test_stream_filter_does_not_emit_partial_sticker_token():
    f = AssistantOutputFilter()
    out = [
        f.feed("摸摸 [sticker:"),
        f.feed("very-long-sticker-name"),
        f.feed("]"),
        f.flush(),
    ]
    assert "".join(out) == "摸摸 [sticker:very-long-sticker-name]"


def test_sanitize_assistant_text_strips_life_update_block():
    """主模型输出 <life-update>{...}</life-update> 标记块必须从对外回复中剥离。"""
    assert sanitize_assistant_text(
        "好的我去吃饭了 <life-update>{\"current_activity\": \"吃饭\"}</life-update>"
    ) == "好的我去吃饭了"
    # 多行也要剥
    assert sanitize_assistant_text(
        "去散步啦\n<life-update>\n{\"current_activity\": \"散步\"}\n</life-update>"
    ) == "去散步啦"


def test_stream_filter_does_not_emit_life_update_until_closed():
    """流式过程中 life-update 块未闭合前不能切到中间发出去。"""
    f = AssistantOutputFilter()
    out = [
        f.feed("我现在去吃饭啦 <life-update>"),
        f.feed("{\"current_activity\": \"吃饭\", \"recent_meal\":\""),
        f.feed("拉面\"}</life-update>"),
        f.flush(),
    ]
    final = "".join(out)
    # 用户看到的最终内容里不应出现 life-update 块或其字段
    assert "<life-update>" not in final
    assert "current_activity" not in final
    assert "拉面" not in final
    assert "我现在去吃饭啦" in final
    # raw_text 应保留完整原始流，供后端解析 apply
    raw = f.raw_text()
    assert "<life-update>" in raw
    assert "</life-update>" in raw
    assert "拉面" in raw


def test_sanitize_strips_unknown_sticker_tokens():
    """模型自创不在库里的 sticker 名字，必须剥离，避免前端渲染失败。"""
    valid = frozenset({"摸摸头", "ok"})
    assert sanitize_assistant_text(
        "好的 [sticker:摸摸头] 再来 [sticker:不存在] [sticker:ok]",
        valid_sticker_names=valid,
    ) == "好的 [sticker:摸摸头] 再来 [sticker:ok]"
    # 自创名字（多/少一个字）也要剥
    assert sanitize_assistant_text(
        "[sticker:摸摸头头]",
        valid_sticker_names=valid,
    ) == "嗯"
    # 不传 valid_sticker_names 时不做校验（兼容旧行为）
    assert sanitize_assistant_text(
        "[sticker:任意名字]",
    ) == "[sticker:任意名字]"


def test_sanitize_with_empty_sticker_set_strips_all_stickers():
    """空集 = 当前没有可用 sticker，应剥离所有 sticker token。"""
    assert sanitize_assistant_text(
        "[sticker:某个]",
        valid_sticker_names=frozenset(),
    ) == "嗯"


def test_stream_filter_rejects_unknown_sticker():
    """流式版本也要剥离不在库里的 sticker。"""
    f = AssistantOutputFilter(valid_sticker_names=["摸摸头"])
    out = [
        f.feed("看这个 [sticker:不存在] 再 [sticker:摸摸头]"),
        f.feed(" 完成 "),
        f.feed("再说一遍"),
        f.flush(),
    ]
    final = "".join(out)
    assert "[sticker:不存在]" not in final
    assert "[sticker:摸摸头]" in final
