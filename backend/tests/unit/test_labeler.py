"""labeler 单测：batch 调用 / JSON 解析 / 兜底 / 枚举校验。"""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
import respx

from xuwen.config import Settings
from xuwen.persona.labeler import (
    DEFAULT_MOOD_VOCAB,
    ChunkLabel,
    Labeler,
    _RetryableLabelError,
)

LABEL_BASE = "https://label.test/v1"


def _settings(**overrides) -> Settings:
    defaults = dict(
        labeling_enabled=True,
        label_api_url=LABEL_BASE,
        label_api_key="sk-test",  # type: ignore[arg-type]
        label_model="glm-4-flash",
        label_batch_size=4,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_resp(labels: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"labels": labels}, ensure_ascii=False),
                    }
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_label_messages_basic():
    async with httpx.AsyncClient() as raw:
        client = Labeler(_settings(), client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=_mock_resp(
                    [
                        {"mood": "调侃", "topic": "玩笑", "importance": 1},
                        {"mood": "安慰", "topic": "鼓励", "importance": 2},
                    ]
                )
            )
            result = await client.label_messages(["在干嘛", "辛苦啦"])
    assert len(result) == 2
    assert result[0].mood == "调侃"
    assert result[1].mood == "安慰"
    assert result[1].importance == 2


@pytest.mark.asyncio
async def test_label_messages_batches_correctly():
    settings = _settings(label_batch_size=2)
    captured: list[int] = []

    import re

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        user_text = body["messages"][-1]["content"]
        # 数 [数字] 形式的消息编号，避开 prompt 模板里 labels[i] 等字面 [
        count = len(re.findall(r"\[\d+\]", user_text))
        captured.append(count)
        return _mock_resp([{"mood": "日常", "topic": "", "importance": 1}] * count)

    async with httpx.AsyncClient() as raw:
        client = Labeler(settings, client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(side_effect=handler)
            await client.label_messages(["a", "b", "c", "d", "e"])
    # 5 条 / batch=2 = 3 次（2+2+1）
    assert sorted(captured) == [1, 2, 2]


@pytest.mark.asyncio
async def test_label_unknown_mood_falls_back():
    """LLM 返回不在枚举里的 mood，应退化为 unknown。"""
    async with httpx.AsyncClient() as raw:
        client = Labeler(_settings(), client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=_mock_resp(
                    [
                        {"mood": "怪东西", "topic": "x", "importance": 1},
                    ]
                )
            )
            result = await client.label_messages(["x"])
    assert result[0].mood == "unknown"


@pytest.mark.asyncio
async def test_label_invalid_json_falls_back():
    async with httpx.AsyncClient() as raw:
        client = Labeler(_settings(), client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "完全不是 JSON 啊"}}]},
                )
            )
            result = await client.label_messages(["x", "y"])
    assert result[0].mood == "unknown"
    assert result[1].mood == "unknown"


@pytest.mark.asyncio
async def test_label_strips_markdown_code_fence():
    """部分小模型会用 ```json ... ``` 包 JSON。"""
    fenced = "```json\n" + json.dumps({"labels": [{"mood": "撒娇", "topic": "想你", "importance": 2}]}) + "\n```"
    async with httpx.AsyncClient() as raw:
        client = Labeler(_settings(), client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": fenced}}]},
                )
            )
            result = await client.label_messages(["想你"])
    assert result[0].mood == "撒娇"


@pytest.mark.asyncio
async def test_label_handles_short_response():
    """LLM 返回的数量少于输入应补 unknown。"""
    async with httpx.AsyncClient() as raw:
        client = Labeler(_settings(), client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=_mock_resp([{"mood": "日常", "topic": "", "importance": 1}])
            )
            result = await client.label_messages(["a", "b", "c"])
    assert len(result) == 3
    assert result[0].mood == "日常"
    assert result[1].mood == "unknown"
    assert result[2].mood == "unknown"


@pytest.mark.asyncio
async def test_label_4xx_batch_degrades_to_unknown():
    """单批 4xx 错误 → 该批全部 unknown，不中断后续批次。"""
    settings = _settings(label_batch_size=2)
    seq = iter(
        [
            httpx.Response(401, text="invalid"),
            _mock_resp([{"mood": "日常", "topic": "", "importance": 1}] * 2),
        ]
    )
    async with httpx.AsyncClient() as raw:
        client = Labeler(settings, client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(side_effect=lambda req: next(seq))
            result = await client.label_messages(["a", "b", "c", "d"])
    # 前 2 条降级 unknown，后 2 条成功
    assert result[0].mood == "unknown"
    assert result[1].mood == "unknown"
    assert result[2].mood == "日常"
    assert result[3].mood == "日常"


@pytest.mark.asyncio
async def test_label_retryable_error_bubbles_for_resume():
    """限流 / 网络类失败不应写成 unknown，否则后续无法增量续跑。"""
    async with httpx.AsyncClient() as raw:
        client = Labeler(_settings(), client=raw)

        async def boom(batch: list[str]) -> list[ChunkLabel]:
            raise _RetryableLabelError("rate limited")

        client._label_batch = boom  # type: ignore[method-assign]
        with pytest.raises(_RetryableLabelError):
            await client.label_messages(["a"])


def test_default_mood_vocab_has_expected_values():
    """默认 8 项应该和我们承诺的对得上。"""
    expected = {"安慰", "调侃", "分享", "请求", "吐槽", "认真讨论", "日常", "撒娇"}
    assert set(DEFAULT_MOOD_VOCAB) == expected


@pytest.mark.asyncio
async def test_user_can_extend_mood_vocab():
    """LABEL_MOOD_VOCAB 配置应该被 Labeler 接受。"""
    settings = _settings(label_mood_vocab="安慰,调侃,卖萌,深夜emo")
    async with httpx.AsyncClient() as raw:
        client = Labeler(settings, client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(
                return_value=_mock_resp(
                    [{"mood": "卖萌", "topic": "搞怪", "importance": 1}]
                )
            )
            result = await client.label_messages(["哎呀"])
    assert result[0].mood == "卖萌"


@pytest.mark.asyncio
async def test_label_truncates_long_text():
    """单条超过 label_max_chars_per_message 时应截断后再送 LLM。"""
    settings = _settings(label_max_chars_per_message=10)
    captured: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        captured.append(body["messages"][-1]["content"])
        return _mock_resp([{"mood": "日常", "topic": "", "importance": 1}])

    async with httpx.AsyncClient() as raw:
        client = Labeler(settings, client=raw)
        with respx.mock(base_url=LABEL_BASE) as router:
            router.post("/chat/completions").mock(side_effect=handler)
            long_text = "你" * 100
            await client.label_messages([long_text])
    # prompt 里只应见到截断后的 10 个字符
    assert "你" * 10 in captured[0]
    assert "你" * 11 not in captured[0]


@pytest.mark.asyncio
async def test_label_disabled_short_circuits():
    """labeling_enabled=false 时 labeling.label_all_unlabeled 应直接返回。"""
    from xuwen.persona.labeling import label_all_unlabeled

    settings = _settings(labeling_enabled=False)
    report = await label_all_unlabeled(settings)
    assert report.labeled == 0
    assert report.batches == 0


def test_chunk_label_coercion():
    """importance 非法值应被 clamp 到 0-3。"""
    settings = _settings()
    labeler = Labeler(settings, client=httpx.AsyncClient())
    out = labeler._coerce_label({"mood": "日常", "topic": "聊天", "importance": 99})
    assert out.importance == 3
    out2 = labeler._coerce_label({"mood": "日常", "topic": "聊天", "importance": -5})
    assert out2.importance == 0
    out3 = labeler._coerce_label({"mood": "日常", "topic": "x", "importance": "2"})
    assert out3.importance == 2
    out4 = labeler._coerce_label({"mood": "日常", "topic": "x"})  # 无 importance
    assert out4.importance == 1


def test_chunk_label_immutable():
    label = ChunkLabel(mood="日常", topic="", importance=1)
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        label.mood = "调侃"  # type: ignore[misc]
