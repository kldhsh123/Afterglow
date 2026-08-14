"""adaptive 模型切分结果的持久缓存。

JSONL 追加写：每行 `{"k": <sha256>, "v": [[start_idx, end_idx], ...]}`。
- 加载时同 key 后行覆盖先行，坏行（中断导致的半行写入）直接跳过；
- put 立即追加落盘，导入中途中断已完成的批次也不丢；
- key 由调用方生成（批内容 + 影响提示词的参数），换模型/参数自然失效；
- 读写失败只降级为"本次无缓存"，绝不影响导入主链路。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TypeGuard

logger = logging.getLogger(__name__)


class AdaptiveChunkCache:
    """批次级切分结果的 JSONL K/V 缓存。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.hits = 0
        self.misses = 0
        self._entries: dict[str, list[list[int]]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("切分缓存读取失败，按空缓存继续：%s", self.path, exc_info=True)
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            key = item.get("k")
            value = item.get("v")
            if isinstance(key, str) and _valid_segments(value):
                self._entries[key] = value

    def get(self, key: str) -> list[list[int]] | None:
        found = self._entries.get(key)
        if found is None:
            self.misses += 1
            return None
        self.hits += 1
        return found

    def put(self, key: str, segments: list[list[int]]) -> None:
        if not _valid_segments(segments):
            return
        self._entries[key] = segments
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"k": key, "v": segments}, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("切分缓存写入失败（本批结果仅本次运行有效）：%s", self.path, exc_info=True)


def _valid_segments(value: object) -> TypeGuard[list[list[int]]]:
    if not isinstance(value, list) or not value:
        return False
    return all(
        isinstance(seg, list)
        and len(seg) == 2
        and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in seg)
        for seg in value
    )
