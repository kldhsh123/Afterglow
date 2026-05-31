"""chat / responses 路由共享的 helper。

只放真正会跨路由复用的小函数；大段业务逻辑（图片处理、检索、决策、prompt 构建）
仍保留在各路由内部，重复代码可控。下次重构再统一。
"""

from __future__ import annotations

import asyncio
import logging
import re

from xuwen.chat_api.schemas import PolicyHint
from xuwen.chat_api.sticker_store import StickerStore
from xuwen.companion.life import LifeSnapshot, LifeStateManager
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

# Feature #9：主模型在回复中可输出 <schedule-hint>明天早上7点叫我起床</schedule-hint>
# 表达定时任务意图。流结束后由 schedule_extractor 小模型解析为结构化 ScheduleTask。
_SCHEDULE_HINT_RE = re.compile(
    r"<schedule-hint>\s*(.*?)\s*</schedule-hint>",
    re.DOTALL | re.IGNORECASE,
)


def extract_schedule_hints(assistant_text: str, *, max_hints: int = 5) -> list[str]:
    """从 assistant_text 中抽取 <schedule-hint> 块内的自然语言时间意图。

    返回去除首尾空白、去重、最多 max_hints 条的列表；没有命中时返回空列表。
    本函数纯文本处理，不调用任何 LLM；调用 schedule_extractor 之前的轻量预筛。
    """
    if not assistant_text:
        return []
    hints: list[str] = []
    seen: set[str] = set()
    for raw in _SCHEDULE_HINT_RE.findall(assistant_text):
        hint = raw.strip()
        if not hint or hint in seen:
            continue
        seen.add(hint)
        hints.append(hint)
        if len(hints) >= max_hints:
            break
    return hints


def build_policy_hint(
    decision: ResponseDecision,
    *,
    reply_delay_seconds: int = 0,
    reply_delay_reason: str = "",
) -> PolicyHint:
    """把 ResponseDecision 映射成 OpenAI 响应里附带的 PolicyHint。"""
    return PolicyHint(
        should_reply=decision.should_reply,
        reply_mode=decision.reply_mode,
        user_state=decision.user_state,
        risk_level=decision.risk_level,
        reason=decision.derived_reason(),
        reply_delay_seconds=max(0, reply_delay_seconds),
        reply_delay_reason=reply_delay_reason if reply_delay_seconds > 0 else "",
    )


def effective_reply_delay_seconds(
    *,
    life: LifeSnapshot,
    decision: ResponseDecision,
    settings: Settings,
) -> int:
    """计算本轮建议给客户端执行的延迟秒数。

    沉默场景延迟=0；其它情况取 life / decision 两者较大值。
    availability=sleeping 时若模型/规则层都没给延迟，启用兜底（避免 sleeping 秒回）。
    """
    if not decision.should_reply:
        return 0
    raw = max(life.reply_delay_seconds, decision.reply_delay_seconds)
    # sleeping 兜底：模型不一定每次都听 prompt 给 5-45 秒，这里强制最小值。
    if life.availability == "sleeping":
        floor = max(0, settings.life_sleeping_min_reply_delay_seconds)
        if floor > 0:
            raw = max(raw, floor)
    return max(0, min(raw, settings.life_max_reply_delay_seconds))


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

    历史保留的同步版本：仍在测试代码和不需要异步的场景用。
    主链路推荐用 extract_life_marker_async 把 apply 部分 fire-and-forget。
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


def extract_life_marker_async(
    assistant_text: str,
    life: LifeStateManager,
    *,
    enabled: bool,
    apply_lock: asyncio.Lock,
    pending_tasks: set[asyncio.Task[None]] | None = None,
) -> str:
    """异步版：同步剥离标记块给用户，apply 走 fire-and-forget 不阻塞主链路。

    - 剥离 (_LIFE_MARKER_RE.sub) 仍同步，几 µs 级
    - apply_marker_patch 内部是同步 disk write，用 asyncio.to_thread 扔线程池
    - 多个并发 task 通过 apply_lock 序列化，避免 life state 文件竞态
    - pending_tasks 用于 lifespan 关闭时 await，避免孤儿 task

    任何异常都吞掉，不影响主回复链路。
    """
    if not assistant_text:
        return assistant_text
    matches = _LIFE_MARKER_RE.findall(assistant_text)
    if not matches:
        return assistant_text

    if enabled:
        async def _apply_async() -> None:
            try:
                async with apply_lock:
                    for raw in matches:
                        try:
                            await asyncio.to_thread(
                                life.apply_marker_patch, raw, trigger="marker"
                            )
                        except Exception:
                            logger.warning(
                                "life marker async apply 单条失败", exc_info=True
                            )
            except Exception:
                logger.warning("life marker async apply 总失败", exc_info=True)

        task = asyncio.create_task(_apply_async())
        if pending_tasks is not None:
            pending_tasks.add(task)
            # done_callback 在任务完成（含异常/取消）时自动从集合移除，避免长期累积。
            # 这是配合"强引用 set"必须做的清理；否则 set 会一直增长。
            task.add_done_callback(pending_tasks.discard)

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


def is_ai_silence_signal(
    assistant_text: str,
    *,
    sentinel: str,
    decision: ResponseDecision,
) -> bool:
    """判断主模型是否选择了"沉默"出口。

    判定规则：
    - sentinel 非空；
    - sanitize 后的整段文本 strip() 等于 sentinel（不允许任何额外字符）；
    - 决策不处于 unsafe（unsafe 场景下 AI 必须回复，sentinel 直接忽略当文本处理）；
    - 决策本来 should_reply=True 且 reply_mode!="silence"（规则层已强制沉默
      的场景走自己的短路，不进入这里）。

    严格匹配是为了避免模型把 sentinel 当成正文一部分顺手输出导致误吞回复。
    """
    if not sentinel:
        return False
    if not assistant_text:
        return False
    if assistant_text.strip() != sentinel:
        return False
    if decision.user_state == "unsafe":
        return False
    if not decision.should_reply or decision.reply_mode == "silence":
        return False
    return True


def effective_silence_sentinel(settings: Settings) -> str:
    """返回当前生效的沉默 sentinel；AI 自主沉默被开关关闭时返回空串。

    传空串给 render_prompt_block → 不注入沉默指令；
    传空串给 is_ai_silence_signal → 第一道守卫直接返回 False。
    一个开关同时关掉 prompt 入口和 pipeline 兜底，避免散点漏改。
    """
    if not settings.ai_silence_enabled:
        return ""
    return settings.silence_response_sentinel


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
