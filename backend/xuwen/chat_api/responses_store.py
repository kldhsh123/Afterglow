"""Responses API 服务端缓存。

OpenAI Responses API 的 `previous_response_id` 让客户端只需带上 id 就能在服务端
找回之前的对话上下文。Afterglow 用 LanceDB + conversation_id 管理记忆，
所以这层缓存只需要把 response_id 映射回 conversation_id。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock


@dataclass(slots=True, frozen=True)
class ResponseRecord:
    """记录一次 Responses 调用的关键溯源信息。"""

    response_id: str
    conversation_id: str | None
    user_text: str
    assistant_text: str
    created_at: int
    model: str


class ResponsesStore:
    """线程安全的 LRU 缓存。"""

    def __init__(self, capacity: int = 512) -> None:
        if capacity <= 0:
            raise ValueError("ResponsesStore capacity 必须为正整数")
        self._capacity = capacity
        self._items: OrderedDict[str, ResponseRecord] = OrderedDict()
        self._lock = Lock()

    def put(self, record: ResponseRecord) -> None:
        with self._lock:
            if record.response_id in self._items:
                self._items.move_to_end(record.response_id)
            self._items[record.response_id] = record
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def get(self, response_id: str) -> ResponseRecord | None:
        with self._lock:
            record = self._items.get(response_id)
            if record is None:
                return None
            self._items.move_to_end(response_id)
            return record

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
