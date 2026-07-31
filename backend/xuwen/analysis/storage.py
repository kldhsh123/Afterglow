"""关系分析产物的原子、隔离存储。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from xuwen.analysis.models import BlockAnalysis

ANALYSIS_CACHE_VERSION = 5
EXPERIMENTAL_CACHE_VERSION = 3
LIFE_CACHE_VERSION = 1


class AnalysisStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.blocks_dir = self.root / "blocks"
        self.experimental_dir = self.root / "experimental"
        self.experimental_blocks_dir = self.experimental_dir / "blocks"

    def prepare(self, *, experimental: bool) -> None:
        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        if experimental:
            self.experimental_blocks_dir.mkdir(parents=True, exist_ok=True)

    def block_path(self, block_id: str) -> Path:
        return self.blocks_dir / f"{block_id}.json"

    def load_block(self, block_id: str, *, require_experimental: bool = False) -> BlockAnalysis | None:
        path = self.block_path(block_id)
        if not path.exists():
            return None
        try:
            result = BlockAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if result.schema_version != ANALYSIS_CACHE_VERSION:
            return None
        if require_experimental:
            experimental = self.load_experimental_block(block_id)
            if experimental is None:
                return None
            result.experimental_requested = True
            result.experimental_schema_version = experimental.experimental_schema_version
            result.experimental_signals = experimental.experimental_signals
        return result

    def load_experimental_block(self, block_id: str) -> BlockAnalysis | None:
        path = self.experimental_blocks_dir / f"{block_id}.json"
        if not path.exists():
            return None
        try:
            result = BlockAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if result.experimental_schema_version != EXPERIMENTAL_CACHE_VERSION:
            return None
        return result

    def save_block(self, result: BlockAnalysis) -> Path:
        normal = result.model_copy(
            update={
                "experimental_requested": False,
                "experimental_schema_version": 0,
                "experimental_signals": [],
            },
            deep=True,
        )
        path = self.write_json(self.block_path(result.block_id), normal)
        if result.experimental_requested:
            experimental = result.model_copy(
                update={
                    "events": [],
                    "personality_observations": [],
                    "relationship_signals": [],
                    "life_habits": [],
                    "life_schema_version": 0,
                    "experimental_schema_version": EXPERIMENTAL_CACHE_VERSION,
                },
                deep=True,
            )
            self.write_json(
                self.experimental_blocks_dir / f"{result.block_id}.json",
                experimental,
            )
        return path

    def write_json(self, path: Path, value: BaseModel | dict[str, Any] | list[Any]) -> Path:
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self._atomic_write(path, text)
        return path

    def write_text(self, path: Path, text: str) -> Path:
        self._atomic_write(path, text.rstrip() + "\n")
        return path

    def read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(raw_temp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
