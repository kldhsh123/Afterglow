"""AdaptiveChunkCache 单测：读写往返、坏行容忍、非法值拒绝。"""

from __future__ import annotations

from xuwen.ingestion.chunk_cache import AdaptiveChunkCache


def test_cache_roundtrip_across_instances(tmp_path):
    path = tmp_path / "cache.jsonl"
    cache = AdaptiveChunkCache(path)
    assert cache.get("k1") is None
    cache.put("k1", [[0, 1], [2, 3]])

    reloaded = AdaptiveChunkCache(path)
    assert reloaded.get("k1") == [[0, 1], [2, 3]]
    assert reloaded.hits == 1
    assert reloaded.misses == 0


def test_cache_tolerates_corrupt_and_half_written_lines(tmp_path):
    path = tmp_path / "cache.jsonl"
    path.write_text(
        '{"k": "good", "v": [[0, 2]]}\n'
        "not json at all\n"
        '{"k": "half", "v": [[0,\n',
        encoding="utf-8",
    )
    cache = AdaptiveChunkCache(path)
    assert cache.get("good") == [[0, 2]]
    assert cache.get("half") is None


def test_cache_rejects_invalid_segments(tmp_path):
    path = tmp_path / "cache.jsonl"
    cache = AdaptiveChunkCache(path)
    cache.put("bad-shape", [[0]])
    cache.put("bad-type", [[0, "x"]])  # type: ignore[list-item]
    cache.put("empty", [])
    assert cache.get("bad-shape") is None
    assert cache.get("bad-type") is None
    assert cache.get("empty") is None
    assert not path.exists()  # 无有效写入则不创建文件


def test_cache_last_write_wins(tmp_path):
    path = tmp_path / "cache.jsonl"
    cache = AdaptiveChunkCache(path)
    cache.put("k", [[0, 1]])
    cache.put("k", [[0, 5]])

    reloaded = AdaptiveChunkCache(path)
    assert reloaded.get("k") == [[0, 5]]
