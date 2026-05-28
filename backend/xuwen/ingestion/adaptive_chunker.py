"""Adaptive dialogue windowing for imported chat history.

The adaptive path keeps raw messages immutable. The optional model only returns
segment boundaries; Python code still renders and stores the original text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.config import Settings
from xuwen.core.models import MessageKind, MessageWindow, NormalizedMessage, Session

logger = logging.getLogger(__name__)

_TOPIC_SHIFT_MARKERS = (
    "对了", "话说", "突然想起", "还有个事", "另一个事", "说起来", "换个话题",
    "顺便", "不过", "但是", "然后呢",
)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True, frozen=True)
class _Turn:
    start_seq: int
    end_seq: int
    role: str
    messages: list[NormalizedMessage]

    @property
    def char_count(self) -> int:
        return sum(len(m.text.strip()) for m in self.messages)


@dataclass(slots=True, frozen=True)
class _TurnSegment:
    start_idx: int
    end_idx: int


async def build_adaptive_windows(
    sessions: list[Session],
    settings: Settings,
    *,
    llm: LLMClient | None = None,
    model: str = "",
    progress_cb: Callable[[int, int], None] | None = None,
    max_concurrency: int = 1,
) -> list[MessageWindow]:
    """Build dialogue windows from topic-sized adaptive segments.

    progress_cb(done, total)：每完成一个 session 的切分后回调一次。
        done 单调递增；total = 实际被处理的 session 数（已剔除空会话）。
    max_concurrency：会话级并发上限（>=1）。模型切分时每个会话内部仍按
        ADAPTIVE_CHUNK_MAX_MESSAGES_PER_CALL 串行调用小模型，但不同会话之间
        通过 Semaphore 同时进行；用上游 API 的并发额度大幅压缩总耗时。
    输出窗口的顺序按 processable session 原序排列（gather 保序）。
    """
    processable = [
        s for s in sessions
        if [m for m in s.messages if m.kind != MessageKind.SYSTEM]
    ]
    total = len(processable)
    if total == 0:
        return []

    sem = asyncio.Semaphore(max(1, max_concurrency))
    done_counter = 0
    done_lock = asyncio.Lock()

    async def _process_one(session: Session) -> list[MessageWindow]:
        nonlocal done_counter
        usable = [m for m in session.messages if m.kind != MessageKind.SYSTEM]
        turns = _build_turns(usable)
        out: list[MessageWindow] = []
        if turns:
            async with sem:
                segments = await _segments_for_session(
                    turns,
                    settings,
                    llm=llm,
                    model=model,
                )
            for seg in segments:
                start_idx = max(0, seg.start_idx - settings.adaptive_chunk_overlap_turns)
                end_idx = min(len(turns) - 1, seg.end_idx)
                messages = _flatten_turns(turns[start_idx : end_idx + 1])
                if messages:
                    out.append(_make_adaptive_window(session.session_id, messages))
        # 进度回调：单调递增，并发完成顺序不同时 done 仍能正确递增
        async with done_lock:
            done_counter += 1
            if progress_cb is not None:
                try:
                    progress_cb(done_counter, total)
                except Exception:
                    pass
        return out

    # asyncio.gather 保留输入顺序，确保 windows 按 session 原序拼接
    per_session_windows = await asyncio.gather(
        *[_process_one(s) for s in processable]
    )
    return [w for ws in per_session_windows for w in ws]


async def _segments_for_session(
    turns: list[_Turn],
    settings: Settings,
    *,
    llm: LLMClient | None,
    model: str,
) -> list[_TurnSegment]:
    if (
        settings.adaptive_chunk_model_enabled
        and llm is not None
        and model
    ):
        try:
            segments = await _model_segments_batched(turns, settings, llm=llm, model=model)
            if segments:
                return segments
        except Exception:
            logger.warning("adaptive chunk model failed; using heuristic windows", exc_info=True)
    return _heuristic_segments(turns, settings)


async def _model_segments_batched(
    turns: list[_Turn],
    settings: Settings,
    *,
    llm: LLMClient,
    model: str,
) -> list[_TurnSegment]:
    """Ask the model for boundaries in bounded message batches.

    Long sessions are common in exported chat logs. Keeping model calls bounded
    avoids overlong prompts while still using semantic boundaries for the full
    session instead of falling back entirely to heuristics.
    """
    segments: list[_TurnSegment] = []
    for offset, batch in _turn_batches_by_message_count(
        turns,
        settings.adaptive_chunk_max_messages_per_call,
    ):
        try:
            batch_segments = await _model_segments(batch, settings, llm=llm, model=model)
        except Exception:
            logger.warning(
                "adaptive chunk model batch failed; using heuristic batch windows",
                exc_info=True,
            )
            batch_segments = []
        if not batch_segments:
            batch_segments = _heuristic_segments(batch, settings)
        segments.extend(
            _TurnSegment(offset + seg.start_idx, offset + seg.end_idx)
            for seg in batch_segments
        )
    return _normalize_segments(segments, len(turns))


async def _model_segments(
    turns: list[_Turn],
    settings: Settings,
    *,
    llm: LLMClient,
    model: str,
) -> list[_TurnSegment]:
    prompt = _build_boundary_prompt(turns, settings)
    raw = await llm.complete_chat(
        [
            {
                "role": "system",
                "content": (
                    "你是聊天记录切分助手。你只输出 JSON，不要 markdown。"
                    "你只能返回边界，不能改写、总结或补充原文。"
                    "目标是把连续聊天切成主题完整、适合 RAG 检索的片段。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        GenerationParams(
            temperature=settings.adaptive_chunk_temperature,
            max_tokens=settings.adaptive_chunk_max_tokens,
        ),
        model=model,
        stage="ingestion.adaptive_chunk",
    )
    parsed = _parse_model_segments(raw, turns)
    if not parsed:
        return []
    return _normalize_segments(parsed, len(turns))


def _build_boundary_prompt(turns: list[_Turn], settings: Settings) -> str:
    lines = [
        "请根据下面的聊天 turn 返回切分边界。",
        f"目标长度约 {settings.adaptive_chunk_target_chars} 字，最大约 {settings.adaptive_chunk_max_chars} 字。",
        "尽量让一个片段包含完整小话题/一问一答，不要把强相关上下文切开。",
        "如果话题明显变化，可以提前切。",
        "",
        "【聊天 turn】",
    ]
    for idx, turn in enumerate(turns):
        role = "用户" if turn.role == "self" else "TA" if turn.role == "friend" else turn.role
        text = " / ".join(m.text.strip() for m in turn.messages if m.text.strip())
        lines.append(f"{idx}. seq={turn.start_seq}-{turn.end_seq} {role}: {_short(text, 180)}")
    lines.extend(
        [
            "",
            "输出 JSON 格式：",
            '{"segments":[{"start_turn":0,"end_turn":3,"topic":"短标签","mood":"短标签"}]}',
            "start_turn/end_turn 使用上面 turn 编号，闭区间；segments 按顺序覆盖主要内容。",
        ]
    )
    return "\n".join(lines)


def _parse_model_segments(raw: str, turns: list[_Turn]) -> list[_TurnSegment]:
    match = _JSON_BLOCK_RE.search(raw.strip())
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    raw_segments = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(raw_segments, list):
        return []
    out: list[_TurnSegment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        start = _coerce_int(item.get("start_turn"))
        end = _coerce_int(item.get("end_turn"))
        if start is None or end is None:
            start_seq = _coerce_int(item.get("start_seq"))
            end_seq = _coerce_int(item.get("end_seq"))
            if start_seq is None or end_seq is None:
                continue
            start = _turn_index_for_seq(turns, start_seq)
            end = _turn_index_for_seq(turns, end_seq)
        if start is None or end is None:
            continue
        out.append(_TurnSegment(start, end))
    return out


def _normalize_segments(segments: list[_TurnSegment], n_turns: int) -> list[_TurnSegment]:
    normalized: list[_TurnSegment] = []
    cursor = 0
    for seg in sorted(segments, key=lambda s: (s.start_idx, s.end_idx)):
        start = max(0, min(seg.start_idx, n_turns - 1))
        end = max(start, min(seg.end_idx, n_turns - 1))
        if start > cursor:
            normalized.append(_TurnSegment(cursor, start - 1))
        if end < cursor:
            continue
        start = max(start, cursor)
        normalized.append(_TurnSegment(start, end))
        cursor = end + 1
        if cursor >= n_turns:
            break
    if cursor < n_turns:
        normalized.append(_TurnSegment(cursor, n_turns - 1))
    return normalized


def _turn_batches_by_message_count(
    turns: list[_Turn],
    max_messages: int,
) -> list[tuple[int, list[_Turn]]]:
    limit = max(1, max_messages)
    batches: list[tuple[int, list[_Turn]]] = []
    start = 0
    count = 0
    for idx, turn in enumerate(turns):
        turn_messages = max(1, len(turn.messages))
        if idx > start and count + turn_messages > limit:
            batches.append((start, turns[start:idx]))
            start = idx
            count = 0
        count += turn_messages
    if start < len(turns):
        batches.append((start, turns[start:]))
    return batches


def _heuristic_segments(turns: list[_Turn], settings: Settings) -> list[_TurnSegment]:
    if not turns:
        return []
    target = max(1, settings.adaptive_chunk_target_chars)
    max_chars = max(target, settings.adaptive_chunk_max_chars)
    min_turns = max(1, settings.adaptive_chunk_min_turns)
    segments: list[_TurnSegment] = []
    start = 0
    chars = 0
    for idx, turn in enumerate(turns):
        if idx > start and _should_soft_break(turns[idx - 1], turn, chars, idx - start, settings):
            segments.append(_TurnSegment(start, idx - 1))
            start = idx
            chars = 0
        chars += max(1, turn.char_count)
        turn_count = idx - start + 1
        if chars >= max_chars or (chars >= target and turn_count >= min_turns):
            segments.append(_TurnSegment(start, idx))
            start = idx + 1
            chars = 0
    if start < len(turns):
        segments.append(_TurnSegment(start, len(turns) - 1))
    return segments


def _should_soft_break(
    prev: _Turn,
    cur: _Turn,
    current_chars: int,
    current_turns: int,
    settings: Settings,
) -> bool:
    if current_turns < max(1, settings.adaptive_chunk_min_turns):
        return False
    gap_ms = cur.messages[0].timestamp_ms - prev.messages[-1].timestamp_ms
    if gap_ms >= settings.adaptive_chunk_soft_gap_minutes * 60_000:
        return True
    cur_text = " ".join(m.text for m in cur.messages)
    if current_chars >= settings.adaptive_chunk_target_chars // 2:
        return any(marker in cur_text for marker in _TOPIC_SHIFT_MARKERS)
    return False


def _build_turns(messages: list[NormalizedMessage]) -> list[_Turn]:
    turns: list[_Turn] = []
    current: list[NormalizedMessage] = []
    current_role = ""
    for msg in messages:
        role = msg.sender_role
        if current and role != current_role:
            turns.append(_make_turn(current_role, current))
            current = []
        current.append(msg)
        current_role = role
    if current:
        turns.append(_make_turn(current_role, current))
    return turns


def _make_turn(role: str, messages: list[NormalizedMessage]) -> _Turn:
    return _Turn(
        start_seq=messages[0].seq,
        end_seq=messages[-1].seq,
        role=role,
        messages=list(messages),
    )


def _flatten_turns(turns: list[_Turn]) -> list[NormalizedMessage]:
    return [m for turn in turns for m in turn.messages]


def _make_adaptive_window(session_id: str, messages: list[NormalizedMessage]) -> MessageWindow:
    start_seq = messages[0].seq
    end_seq = messages[-1].seq
    return MessageWindow(
        window_id=f"awin-{session_id[5:13]}-{start_seq}-{end_seq}",
        session_id=session_id,
        messages=list(messages),
        start_seq=start_seq,
        end_seq=end_seq,
        start_time_ms=messages[0].timestamp_ms,
        end_time_ms=messages[-1].timestamp_ms,
        has_media=any(m.has_media for m in messages),
    )


def _turn_index_for_seq(turns: list[_Turn], seq: int) -> int | None:
    for idx, turn in enumerate(turns):
        if turn.start_seq <= seq <= turn.end_seq:
            return idx
    return None


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _short(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."
