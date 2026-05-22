"""chat / responses 路由共享的 helper。

只放真正会跨路由复用的小函数；大段业务逻辑（图片处理、检索、决策、prompt 构建）
仍保留在各路由内部，重复代码可控。下次重构再统一。
"""

from __future__ import annotations

import logging
import re

from xuwen.chat_api.schemas import PolicyHint
from xuwen.chat_api.sticker_store import StickerStore
from xuwen.companion.life import LifeStateManager
from xuwen.companion.response_policy import ResponseDecision
from xuwen.config import Settings

logger = logging.getLogger(__name__)

# 主模型在回复中可输出 <life-update>{...}</life-update> 标记块，
# 后端解析后直接 patch life，并从对外回复里剥离这个块。
# 用 DOTALL 让 . 匹配换行；非贪婪取最短内容。
_LIFE_MARKER_RE = re.compile(
    r"<life-update>\s*(.*?)\s*</life-update>",
    re.DOTALL | re.IGNORECASE,
)


def build_policy_hint(decision: ResponseDecision) -> PolicyHint:
    """把 ResponseDecision 映射成 OpenAI 响应里附带的 PolicyHint。"""
    return PolicyHint(
        should_reply=decision.should_reply,
        reply_mode=decision.reply_mode,
        user_state=decision.user_state,
        risk_level=decision.risk_level,
        reason=decision.derived_reason(),
    )


def extract_and_apply_life_marker(
    assistant_text: str,
    life: LifeStateManager,
    *,
    enabled: bool,
) -> str:
    """从 assistant_text 中提取 life-update 标记块，应用到 life state，返回剥离后的文本。

    - enabled=True：解析 + 应用 + 剥离
    - enabled=False：仅剥离（兜底，避免前端看到内部协议）

    任何异常都吞掉，不影响主回复链路。
    """
    if not assistant_text:
        return assistant_text
    matches = _LIFE_MARKER_RE.findall(assistant_text)
    if not matches:
        return assistant_text
    if enabled:
        for raw in matches:
            try:
                life.apply_marker_patch(raw, trigger="marker")
            except Exception:
                logger.warning("life marker 应用失败，已忽略", exc_info=True)
    # 剥离标记块（即使禁用也要剥，防止前端看到内部协议）
    return _LIFE_MARKER_RE.sub("", assistant_text).strip()


def available_sticker_names(settings: Settings) -> frozenset[str]:
    """收集 AI 当前实际能用的 sticker 名字集合。

    输出层会用这个集合校验 `[sticker:xxx]`：xxx 不在集合内就剥离，
    避免模型自创不存在的 sticker 让前端渲染失败。

    任何异常都返回空集（不做校验，等同当前行为，避免误伤）。
    """
    try:
        store = StickerStore(settings)
        return frozenset(s.name for s in store.available_for_ai())
    except Exception:
        logger.warning("收集可用 sticker 名字失败，跳过 sticker 校验", exc_info=True)
        return frozenset()


# 当模型只发了不存在的 sticker、被输出过滤拦截后，sanitize 会兜底成 "嗯"。
# 但 "嗯" 跟 sticker 的语气完全不匹配（撒娇/玩梗时尤其奇怪）。
# 这里按 reply_mode 给一个更贴合本轮模式的短句。
_STICKER_REJECT_FALLBACK_BY_MODE: dict[str, str] = {
    "clingy": "嘿嘿",
    "intimate": "嘿嘿",
    "playful": "哈哈",
    "tease": "哈哈",
    "joking": "草",
    "chaotic": "……",
    "serious": "嗯嗯",
    "calm": "嗯嗯",
    "topic_shift": "嗯",
    "image": "唔",
    "sticker": "唔",
    "silence": "",
}


def fallback_for_rejected_sticker(reply_mode: str) -> str:
    """模型只输出了不存在的 sticker，被剥离后该用什么短句替代干瘪的"嗯"。

    参数是 reply_mode 字符串，方便流式分支用 PolicyHint.reply_mode 直接调用。
    """
    return _STICKER_REJECT_FALLBACK_BY_MODE.get(reply_mode, "……")


def looks_like_sticker_only_intent(raw_text: str) -> bool:
    """判断模型原始输出是否"基本只是想发 sticker"。

    判断规则：原文含 [sticker:，把所有 sticker 占位剥掉后剩下的内容非常少
    （少于 2 个有效字符），认为模型本意就是用 sticker 表达。
    """
    if "[sticker:" not in raw_text:
        return False
    # 同 output_filter._FULL_STICKER_TOKEN_RE，去掉所有 sticker token
    no_stickers = re.sub(r"\[sticker(?::|=)[^\]\s]+\]", "", raw_text)
    # 同时去掉未闭合的尾巴和常见标点
    no_stickers = re.sub(r"\[sticker(?::|=)[^\]\s]*$", "", no_stickers)
    no_stickers = re.sub(r"[\s,，.。:：;；、~～!！?？…·\-—]+", "", no_stickers)
    return len(no_stickers) < 2


def build_sticker_retry_hint(
    raw_text: str,
    available_names: frozenset[str],
) -> str:
    """构造一条 system 消息提示，告诉主模型刚才输出的 sticker 不在库里，请重新组织。

    用作 retry 时追加到 messages 末尾。available_names 为空时明确告诉模型
    "当前完全没有可用 sticker"，避免它继续乱猜。
    """
    rejected = sorted({
        m.group(1)
        for m in re.finditer(r"\[sticker(?::|=)([^\]\s]+)\]", raw_text)
        if m.group(1) not in available_names
    })
    rejected_text = "、".join(f"[sticker:{n}]" for n in rejected) or "（未识别）"
    if available_names:
        allowed = "、".join(f"[sticker:{n}]" for n in sorted(available_names))
        return (
            f"你刚才输出了系统里**不存在**的 sticker：{rejected_text}。"
            f"当前可用的 sticker 仅有：{allowed}。"
            "请用文字（或上面列出的名字一字不差地使用）重新回复用户的上一条消息；"
            "**不要**再输出自创的 sticker 名字。"
        )
    return (
        f"你刚才输出了系统里**不存在**的 sticker：{rejected_text}。"
        "当前完全没有可用 sticker。请改用文字（或 emoji）重新回复用户的上一条消息；"
        "**不要**再输出 `[sticker:...]` 格式。"
    )
