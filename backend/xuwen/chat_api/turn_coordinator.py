"""按调用方隔离的聊天轮次协调器。

这层刻意只做进程内、best-effort 状态管理，用于支持 IM 里常见的
"AI 还在想/还在打字时，用户又补了一条消息"语义：
同一个 caller 的新输入会取消上一轮 active turn，并把尚未被成功回复的
用户气泡合并进下一轮生成。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field


@dataclass(slots=True)
class PendingInput:
    message_id: str
    text: str
    image_shas: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ActiveTurn:
    generation: int
    cancel_event: asyncio.Event
    message_ids: tuple[str, ...]


@dataclass(slots=True)
class TurnSnapshot:
    caller_id: str
    generation: int
    cancel_event: asyncio.Event
    pending_inputs: list[PendingInput]

    @property
    def message_ids(self) -> tuple[str, ...]:
        return tuple(item.message_id for item in self.pending_inputs)

    def combined_text(self) -> str:
        return "\n\n".join(item.text.strip() for item in self.pending_inputs if item.text.strip())

    def combined_image_shas(self) -> list[str]:
        result: list[str] = []
        for item in self.pending_inputs:
            result.extend(item.image_shas)
        return result

    def combined_image_urls(self) -> list[str]:
        result: list[str] = []
        for item in self.pending_inputs:
            result.extend(item.image_urls)
        return result


class TurnCoordinator:
    """追踪每个 caller 的待回复用户输入和最新 active generation。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queues: dict[str, list[PendingInput]] = {}
        self._active: dict[str, ActiveTurn] = {}
        self._generations: dict[str, int] = {}

    async def begin_turn(
        self,
        *,
        caller_id: str,
        message_id: str | None,
        text: str,
        image_shas: list[str],
        image_urls: list[str],
    ) -> TurnSnapshot:
        async with self._lock:
            old = self._active.get(caller_id)
            if old is not None:
                old.cancel_event.set()

            resolved_message_id = message_id or f"msg-{uuid.uuid4().hex[:16]}"
            queue = self._queues.setdefault(caller_id, [])
            if not any(item.message_id == resolved_message_id for item in queue):
                queue.append(
                    PendingInput(
                        message_id=resolved_message_id,
                        text=text,
                        image_shas=list(image_shas),
                        image_urls=list(image_urls),
                    )
                )

            generation = self._generations.get(caller_id, 0) + 1
            self._generations[caller_id] = generation
            cancel_event = asyncio.Event()
            pending_inputs = list(queue)
            self._active[caller_id] = ActiveTurn(
                generation=generation,
                cancel_event=cancel_event,
                message_ids=tuple(item.message_id for item in pending_inputs),
            )
            return TurnSnapshot(
                caller_id=caller_id,
                generation=generation,
                cancel_event=cancel_event,
                pending_inputs=pending_inputs,
            )

    async def is_current(self, snapshot: TurnSnapshot) -> bool:
        async with self._lock:
            active = self._active.get(snapshot.caller_id)
            return active is not None and active.generation == snapshot.generation

    async def ack(self, snapshot: TurnSnapshot) -> bool:
        async with self._lock:
            active = self._active.get(snapshot.caller_id)
            if active is None or active.generation != snapshot.generation:
                return False

            acked = set(snapshot.message_ids)
            queue = self._queues.get(snapshot.caller_id, [])
            self._queues[snapshot.caller_id] = [
                item for item in queue if item.message_id not in acked
            ]
            if not self._queues[snapshot.caller_id]:
                self._queues.pop(snapshot.caller_id, None)
            self._active.pop(snapshot.caller_id, None)
            return True

    async def cancel(self, snapshot: TurnSnapshot) -> None:
        async with self._lock:
            active = self._active.get(snapshot.caller_id)
            if active is not None and active.generation == snapshot.generation:
                active.cancel_event.set()
                self._active.pop(snapshot.caller_id, None)
