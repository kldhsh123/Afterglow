"""Assistant 输出过滤。

历史聊天里的 `[图片]` / `[[表情]]` / `[/汪汪]` 只是导入占位符或 QQ 自带文字表情，
模型不能真的发送这些内容。这里在 LLM 输出层做最后一道防线；
真正可渲染的表情包使用 `[sticker:名字]`——但**只允许已注册的 sticker 名字**，
模型自创的（库里不存在的）也会在这里被剥离，避免前端渲染失败 / 显示乱码。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_MEDIA_PLACEHOLDER_RE = re.compile(
    r"\[(?:图片|语音|视频|文件|表情|动画表情|撤回|系统消息)(?:[:：][^\]]*)?\]\s*[:：]?\s*"
)
_QQ_FACE_RE = re.compile(r"\[\[[^\]]+\]\]\s*[:：]?\s*")
# QQ 自带文字表情：`[/汪汪]` `[/呲牙]` 等。即便 LanceDB 里有历史污染未清洗的记录，
# 这里也要兜底，避免主模型把它当语气信号原样输出。
_QQ_NATIVE_FACE_RE = re.compile(r"\[/[^\]\n]{1,16}\]\s*[:：]?\s*")
_REPLY_MEDIA_RE = re.compile(
    r"\[回复[^\n\]]*(?:图片|语音|视频|文件|表情|动画表情)[^\n\]]*\]\s*[:：]?\s*"
)
# 主模型在回复中输出的 <life-update>{...}</life-update> 标记块——路由层会
# 解析并 patch life，但对外回复必须剥离干净不让用户看到内部协议。
_LIFE_UPDATE_RE = re.compile(
    r"\s*<life-update>.*?</life-update>\s*",
    re.DOTALL | re.IGNORECASE,
)
_LIFE_UPDATE_OPEN_TAG = "<life-update>"
_LIFE_UPDATE_CLOSE_TAG = "</life-update>"
# 主模型在回复中输出的 <schedule-hint>...</schedule-hint> 自然语言意图块——
# 路由层会调用 schedule_extractor 小模型解析为 ScheduleTask，对外回复必须剥离。
# Feature #9。
_SCHEDULE_HINT_RE = re.compile(
    r"\s*<schedule-hint>.*?</schedule-hint>\s*",
    re.DOTALL | re.IGNORECASE,
)
_SCHEDULE_HINT_OPEN_TAG = "<schedule-hint>"
_SCHEDULE_HINT_CLOSE_TAG = "</schedule-hint>"
# 完整 sticker token：用来匹配模型已经输出完整的 [sticker:xxx]，便于校验
_FULL_STICKER_TOKEN_RE = re.compile(r"\[sticker(?::|=)([^\]\s]+)\]")
_TRAILING_PARTIAL_STICKER_RE = re.compile(r"\s*\[sticker(?::|=)[^\]\s]*$")
_LEADING_PUNCT_RE = re.compile(r"^[\s,，.。:：;；、~～]+")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_STREAM_TAIL_CHARS = 16


def _filter_unknown_stickers(text: str, valid_names: frozenset[str]) -> str:
    """剥离 `[sticker:xxx]` 中 xxx 不在 valid_names 里的 token。

    valid_names 为空集时**不做任何过滤**（用于测试或调试场景）。
    这是为了避免一个空集 = 全删的误伤；如果你确实想全删，路由层不要传 valid_names。
    """
    if not valid_names:
        return text

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in valid_names:
            return match.group(0)
        return ""

    return _FULL_STICKER_TOKEN_RE.sub(_replace, text)


def sanitize_assistant_text(
    text: str,
    *,
    fallback_empty: bool = True,
    valid_sticker_names: frozenset[str] | None = None,
) -> str:
    """移除模型复制出来的历史占位符 + 校验 sticker 是否注册。

    - 保留 `[sticker:xxx]` 当 xxx 在 valid_sticker_names 中；否则剥离
    - valid_sticker_names=None 时不做 sticker 校验（保留所有 `[sticker:xxx]`）
    """
    if not text:
        return text

    out = _REPLY_MEDIA_RE.sub("", text)
    out = _LIFE_UPDATE_RE.sub("", out)
    out = _SCHEDULE_HINT_RE.sub("", out)
    out = _MEDIA_PLACEHOLDER_RE.sub("", out)
    out = _QQ_FACE_RE.sub("", out)
    out = _QQ_NATIVE_FACE_RE.sub("", out)
    out = _TRAILING_PARTIAL_STICKER_RE.sub("", out)
    if valid_sticker_names is not None:
        out = _filter_unknown_stickers(out, valid_sticker_names)
    out = _MULTI_SPACE_RE.sub(" ", out)
    if fallback_empty:
        out = "\n".join(_LEADING_PUNCT_RE.sub("", line).rstrip() for line in out.splitlines())
        out = out.strip()
    else:
        out = "\n".join(_LEADING_PUNCT_RE.sub("", line) for line in out.splitlines())
    if fallback_empty and text.strip() and not out:
        return "嗯"
    return out


class AssistantOutputFilter:
    """流式输出过滤器。

    为避免把拆开的 `[图片]` 半截先发给前端，保留一小段尾巴，等下个 chunk
    到来后再统一过滤。最终 flush 时会处理剩余内容。

    同时维护一份累积的 raw 文本（`raw_text()`），供流结束后路由层做
    life-update 标记块解析等不影响实时输出的后处理。

    构造时传入 `valid_sticker_names` 后，所有 `[sticker:xxx]` 都会被校验：
    xxx 不在集合内的会被剥离，避免前端渲染不存在的表情包。
    """

    def __init__(
        self,
        *,
        valid_sticker_names: Iterable[str] | None = None,
    ) -> None:
        self._buffer = ""
        self._all_raw = ""
        self._valid_sticker_names: frozenset[str] | None = (
            frozenset(valid_sticker_names) if valid_sticker_names is not None else None
        )

    def raw_text(self) -> str:
        """返回累积的完整原始文本（含还没 flush 的尾巴）。"""
        return self._all_raw

    def feed(self, piece: str) -> str:
        if not piece:
            return ""
        self._buffer += piece
        self._all_raw += piece
        if len(self._buffer) <= _STREAM_TAIL_CHARS:
            return ""

        cut = len(self._buffer) - _STREAM_TAIL_CHARS
        # 用 lower-case 副本做位置查找，与 _LIFE_UPDATE_RE / _SCHEDULE_HINT_RE 的
        # IGNORECASE 行为对齐——否则 <SCHEDULE-HINT> 这种大小写变体能绕过流式守卫
        # 把半截标签透传给前端（Finding 2）。两个标签都是纯 ASCII，lower 不会移位。
        buffer_lower = self._buffer.lower()
        # 不要在 bracket token 中间切开，尤其是较长的 [sticker:xxx]。
        last_bracket = self._buffer.rfind("[", 0, cut)
        if last_bracket >= 0:
            close_bracket = self._buffer.find("]", last_bracket)
            if close_bracket == -1 or close_bracket >= cut:
                cut = last_bracket
        if last_bracket >= 0 and cut - last_bracket < _STREAM_TAIL_CHARS:
            cut = last_bracket
        # 同样保护 <...> 形式的内部协议标签（<life-update>、<schedule-hint>）。
        # 与上面的 `[` 守卫对称：当 < 在 cut 前但匹配的 > 未在 cut 前出现时，
        # 把 cut 回退到 < 处，防止前端看到半截开标签（如 "<life-upda"）。
        last_lt = self._buffer.rfind("<", 0, cut)
        if last_lt >= 0:
            close_gt = self._buffer.find(">", last_lt)
            if close_gt == -1 or close_gt >= cut:
                cut = last_lt
        # 不要在 <life-update>...</life-update> 块中间切开，否则用户会看到半截内部协议
        last_tag_open = buffer_lower.rfind(_LIFE_UPDATE_OPEN_TAG, 0, cut)
        if last_tag_open >= 0:
            tag_close_idx = buffer_lower.find(_LIFE_UPDATE_CLOSE_TAG, last_tag_open)
            if tag_close_idx == -1 or (
                tag_close_idx + len(_LIFE_UPDATE_CLOSE_TAG) > cut
            ):
                cut = last_tag_open
        # 同样保护 <schedule-hint>...</schedule-hint>（同样走 lower-case 比对）
        last_hint_open = buffer_lower.rfind(_SCHEDULE_HINT_OPEN_TAG, 0, cut)
        if last_hint_open >= 0:
            hint_close_idx = buffer_lower.find(_SCHEDULE_HINT_CLOSE_TAG, last_hint_open)
            if hint_close_idx == -1 or (
                hint_close_idx + len(_SCHEDULE_HINT_CLOSE_TAG) > cut
            ):
                cut = last_hint_open
        if cut <= 0:
            return ""

        raw = self._buffer[:cut]
        self._buffer = self._buffer[cut:]
        return sanitize_assistant_text(
            raw,
            fallback_empty=False,
            valid_sticker_names=self._valid_sticker_names,
        )

    def flush(self) -> str:
        raw = self._buffer
        self._buffer = ""
        return sanitize_assistant_text(
            raw,
            fallback_empty=False,
            valid_sticker_names=self._valid_sticker_names,
        )
