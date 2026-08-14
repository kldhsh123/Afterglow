"""导入链路 embedding 容错单测：跳过失败批次、连续失败熔断。

直接测 `_embed_and_upsert_track`，embedder / store 用最小 fake。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from xuwen.config import Settings
from xuwen.core.errors import IngestionError
from xuwen.ingestion.embedder import TolerantEmbedResult
from xuwen.ingestion.importer import _embed_and_upsert_track, import_history


@dataclass(slots=True)
class _Chunk:
    chunk_id: str
    text: str


class _FakeStore:
    async def existing_ids(self, table: str, ids: list[str]) -> set[str]:
        return set()

    async def upsert_friend_chunks(self, batch, embeddings) -> int:
        return len(batch)

    async def upsert_window_chunks(self, batch, embeddings) -> int:
        return len(batch)

    async def upsert_response_pair_chunks(self, batch, embeddings) -> int:
        return len(batch)


class _ScriptedEmbedder:
    """按脚本逐次返回 TolerantEmbedResult 的 fake。

    脚本元素："ok" 全成功（1 个请求）/ "fail" 全灭（1 个请求）/
    "partial:<n>" 2 个请求中 1 个失败，前 n 条置 None。
    """

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    async def embed_texts_tolerant(self, texts):
        action = self.script[self.calls]
        self.calls += 1
        n = len(texts)
        if action == "ok":
            return TolerantEmbedResult(
                vectors=[[1.0, 2.0]] * n, failed_batches=0, total_batches=1
            )
        if action == "fail":
            return TolerantEmbedResult(
                vectors=[None] * n,
                failed_batches=1,
                total_batches=1,
                last_error=RuntimeError("boom"),
            )
        n_failed = int(action.split(":")[1])
        return TolerantEmbedResult(
            vectors=[None] * n_failed + [[1.0, 2.0]] * (n - n_failed),
            failed_batches=1,
            total_batches=2,
            last_error=RuntimeError("boom"),
        )


class _UpsertRecorder:
    def __init__(self) -> None:
        self.chunk_ids: list[str] = []

    async def __call__(self, batch, embeddings) -> int:
        self.chunk_ids.extend(c.chunk_id for c in batch)
        return len(batch)


def _chunks(n: int) -> list[_Chunk]:
    return [_Chunk(chunk_id=f"c{i}", text=f"t{i}") for i in range(n)]


def _import_settings(**updates) -> Settings:
    settings = Settings(
        self_name="Me",
        self_uid="uid-self-001",
        friend_name="TestFriend",
        friend_uid="uid-friend-001",
        relationship_type="friend",
        embedding_max_consecutive_failures=3,
    )
    return settings.model_copy(update=updates)


def _sample_path() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "sample_chat.json"


@pytest.mark.asyncio
async def test_partial_failure_skips_and_continues():
    embedder = _ScriptedEmbedder(["partial:1", "ok"])
    upserts = _UpsertRecorder()

    stats = await _embed_and_upsert_track(
        embedder=embedder,  # type: ignore[arg-type]
        store=_FakeStore(),  # type: ignore[arg-type]
        chunks=_chunks(4),  # type: ignore[arg-type]
        text_of=lambda c: c.text,
        table="t",
        upsert_fn=upserts,
        batch_size=2,
        max_consecutive_failures=3,
    )

    assert embedder.calls == 2
    assert stats["failed"] == 1
    assert stats["embedded"] == 3
    assert stats["upserted"] == 3
    # 失败的 c0 未入库，其余全部落库
    assert sorted(upserts.chunk_ids) == ["c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_consecutive_full_failures_abort():
    embedder = _ScriptedEmbedder(["ok", "fail", "fail", "fail"])
    upserts = _UpsertRecorder()

    with pytest.raises(IngestionError):
        await _embed_and_upsert_track(
            embedder=embedder,  # type: ignore[arg-type]
            store=_FakeStore(),  # type: ignore[arg-type]
            chunks=_chunks(8),  # type: ignore[arg-type]
            text_of=lambda c: c.text,
            table="t",
            upsert_fn=upserts,
            batch_size=2,
            max_consecutive_failures=3,
        )

    # 熔断前已成功的批次必须已经落库（重跑时可跳过）
    assert sorted(upserts.chunk_ids) == ["c0", "c1"]


@pytest.mark.asyncio
async def test_full_failure_below_threshold_then_success_resets():
    # 全灭 1 个请求（如尾批）→ 跳过继续；下批成功清零计数；
    # 之后再连续全灭 2 次仍未达阈值，导入正常完成。
    embedder = _ScriptedEmbedder(["fail", "ok", "fail", "fail"])
    upserts = _UpsertRecorder()

    stats = await _embed_and_upsert_track(
        embedder=embedder,  # type: ignore[arg-type]
        store=_FakeStore(),  # type: ignore[arg-type]
        chunks=_chunks(8),  # type: ignore[arg-type]
        text_of=lambda c: c.text,
        table="t",
        upsert_fn=upserts,
        batch_size=2,
        max_consecutive_failures=3,
    )

    assert embedder.calls == 4
    assert stats["failed"] == 6
    assert stats["embedded"] == 2
    assert stats["upserted"] == 2
    assert sorted(upserts.chunk_ids) == ["c2", "c3"]


@pytest.mark.asyncio
async def test_failure_threshold_is_configurable():
    # 阈值 2：两次全灭即熔断（默认 3 时同样脚本可以跑完，见上一个用例前半段）
    embedder = _ScriptedEmbedder(["fail", "fail"])
    upserts = _UpsertRecorder()

    with pytest.raises(IngestionError):
        await _embed_and_upsert_track(
            embedder=embedder,  # type: ignore[arg-type]
            store=_FakeStore(),  # type: ignore[arg-type]
            chunks=_chunks(4),  # type: ignore[arg-type]
            text_of=lambda c: c.text,
            table="t",
            upsert_fn=upserts,
            batch_size=2,
            max_consecutive_failures=2,
        )

    assert embedder.calls == 2
    assert upserts.chunk_ids == []


@pytest.mark.asyncio
async def test_import_circuit_breaker_counts_failures_across_tracks():
    embedder = _ScriptedEmbedder(["fail", "fail", "fail"])

    with pytest.raises(IngestionError):
        await import_history(
            _sample_path(),
            _import_settings(),
            store=_FakeStore(),  # type: ignore[arg-type]
            embedder=embedder,  # type: ignore[arg-type]
            update_circadian=False,
            update_proactive=False,
        )

    assert embedder.calls == 3


class _CancellationProbeEmbedder:
    instance: _CancellationProbeEmbedder | None = None

    def __init__(self, settings: Settings) -> None:
        type(self).instance = self
        self.started = 0
        self.cancelled = 0
        self.cleaned = 0
        self.closed_after_cleanup = False
        self._all_started = asyncio.Event()
        self._block = asyncio.Event()

    async def embed_texts_tolerant(self, texts) -> TolerantEmbedResult:
        self.started += 1
        if self.started == 3:
            self._all_started.set()
        if self.started == 1:
            await self._all_started.wait()
            return TolerantEmbedResult(
                vectors=[None] * len(texts),
                failed_batches=1,
                total_batches=1,
                last_error=RuntimeError("boom"),
            )
        try:
            await self._block.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            await asyncio.sleep(0)
            self.cleaned += 1
            raise
        raise AssertionError("blocked embedding call unexpectedly resumed")

    async def aclose(self) -> None:
        self.closed_after_cleanup = self.cleaned == 2


@pytest.mark.asyncio
async def test_import_cancels_and_waits_for_siblings_before_closing_embedder(monkeypatch):
    monkeypatch.setattr(
        "xuwen.ingestion.importer.EmbeddingClient",
        _CancellationProbeEmbedder,
    )

    with pytest.raises(IngestionError):
        await import_history(
            _sample_path(),
            _import_settings(embedding_max_consecutive_failures=1),
            store=_FakeStore(),  # type: ignore[arg-type]
            update_circadian=False,
            update_proactive=False,
        )

    embedder = _CancellationProbeEmbedder.instance
    assert embedder is not None
    assert embedder.cancelled == 2
    assert embedder.cleaned == 2
    assert embedder.closed_after_cleanup is True
