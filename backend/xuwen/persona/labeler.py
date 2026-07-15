"""语义标签器：用小模型（默认 GLM-4-Flash）给 chunk 打 mood / topic / importance。

设计要点：
- **batch 调用**：一次 prompt 塞 N 条消息，让 LLM 返回 JSON 数组
- **结构化输出**：用 JSON schema 锁 mood 枚举（容忍小模型自由发挥）
- **兜底**：单条解析失败 → unknown，不阻塞下游
- **离线增量**：上层负责筛"还没标过的"，本模块只管纯打标
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from xuwen.config import Settings
from xuwen.core.errors import XuwenError
from xuwen.ingestion.embedder import _resolve_endpoint

logger = logging.getLogger(__name__)

# 默认 mood 枚举：8 个常见聊天意图
DEFAULT_MOOD_VOCAB: list[str] = [
    "安慰", "调侃", "分享", "请求", "吐槽", "认真讨论", "日常", "撒娇",
]


class LabelError(XuwenError):
    code = "xuwen.label"
    http_status = 502


class _RetryableLabelError(LabelError):
    pass


@dataclass(slots=True, frozen=True)
class ChunkLabel:
    """单个 chunk 的标签结果。"""

    mood: str       # 枚举中的某项，或 "unknown"
    topic: str      # 自由短词（≤8 字），允许空
    importance: int # 0-3：0=无关紧要, 1=普通, 2=值得记, 3=高光时刻


def _mood_vocab(settings: Settings) -> list[str]:
    """用户配置优先；空则用默认。"""
    raw = settings.label_mood_vocab.strip()
    if not raw:
        return list(DEFAULT_MOOD_VOCAB)
    items = [w.strip() for w in raw.split(",")]
    return [w for w in items if w] or list(DEFAULT_MOOD_VOCAB)


class Labeler:
    """小模型打标客户端。"""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.settings = settings
        self._owned_client = client is None
        effective_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.label_timeout_seconds
        )
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(effective_timeout, connect=10.0),
        )
        self._url = _resolve_endpoint(str(settings.label_api_url), "/chat/completions")
        self._headers = {
            "Authorization": f"Bearer {settings.label_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        self._mood_vocab = _mood_vocab(settings)

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def __aenter__(self) -> Labeler:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def label_messages(self, texts: Sequence[str]) -> list[ChunkLabel]:
        """打一批消息。

        - 自动按 settings.label_batch_size 切片
        - 单批失败 → 该批所有消息退化为 unknown 标签（不抛异常）
        - 限流 / 网络 / 5xx 重试耗尽 → 向上抛出，让上层保留未打标状态以便续跑
        - 返回与输入等长、顺序一致的标签列表
        """
        if not texts:
            return []
        batch_size = max(1, self.settings.label_batch_size)
        out: list[ChunkLabel] = []
        max_chars = self.settings.label_max_chars_per_message
        for i in range(0, len(texts), batch_size):
            batch = list(texts[i : i + batch_size])
            # 单条截断防爆 token
            truncated = [t[:max_chars] for t in batch]
            try:
                batch_labels = await self._label_batch(truncated)
            except _RetryableLabelError:
                raise
            except LabelError as e:
                logger.warning(
                    "打标批次失败：%s（%d 条降级为 unknown）",
                    e.message,
                    len(batch),
                )
                batch_labels = [_unknown() for _ in batch]
            out.extend(batch_labels)
        return out

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _label_batch(self, batch: list[str]) -> list[ChunkLabel]:
        """单次 LLM 调用，期望返回 JSON 数组。"""
        prompt = self._build_prompt(batch)
        payload = {
            "model": self.settings.label_model,
            "messages": [
                {"role": "system", "content": "你是一个严格输出 JSON 的标签助手。"},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 0.0,
            "max_tokens": 600,
            "response_format": {"type": "json_object"},
        }
        if self.settings.llm_reasoning_effort != "null":
            payload["reasoning_effort"] = self.settings.llm_reasoning_effort

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1.0, min=1.0, max=10.0),
            retry=retry_if_exception_type((httpx.HTTPError, _RetryableLabelError)),
            reraise=True,
        ):
            with attempt:
                raw = await self._call_once(payload)
        labels = self._parse_response(raw, expected=len(batch))
        return labels

    def _build_prompt(self, batch: list[str]) -> str:
        vocab = "、".join(f'"{w}"' for w in self._mood_vocab)
        lines = [
            f"给下面 {len(batch)} 条朋友的聊天消息分别打标签。",
            "",
            "返回严格的 JSON 对象，根字段 `labels` 是数组，与输入顺序一一对应。",
            "每条 `labels[i]` 必须含 3 个字段：",
            f'- mood：从这些里选一个：{vocab}；如果都不匹配返回 "unknown"',
            "- topic：用 2-6 个汉字概括话题；想不到就返回空字符串",
            "- importance：0-3 的整数（0=无关紧要 / 1=普通 / 2=值得记忆 / 3=高光时刻）",
            "",
            "不要解释、不要 markdown，只返回 JSON。",
            "",
            "消息列表：",
        ]
        for i, text in enumerate(batch, 1):
            cleaned = text.replace("\n", " ").strip()
            lines.append(f"[{i}] {cleaned}")
        return "\n".join(lines)

    async def _call_once(self, payload: dict[str, object]) -> str:
        try:
            resp = await self._client.post(self._url, headers=self._headers, json=payload)
        except httpx.HTTPError as e:
            raise _RetryableLabelError(
                f"打标网络错误：{type(e).__name__}",
            ) from e

        if resp.status_code in (429,) or 500 <= resp.status_code < 600:
            logger.warning("打标上游 %d: %s", resp.status_code, resp.text[:300])
            raise _RetryableLabelError(
                f"打标 API 暂不可用（HTTP {resp.status_code}）",
            )
        if resp.status_code >= 400:
            logger.error("打标上游 %d: %s", resp.status_code, resp.text[:300])
            raise LabelError(
                f"打标 API 客户端错误（HTTP {resp.status_code}），请检查 LABEL_API_KEY / LABEL_MODEL",
            )
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as e:
            raise LabelError("打标 API 响应格式异常") from e
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content or "")

    def _parse_response(self, raw: str, *, expected: int) -> list[ChunkLabel]:
        """容错地解析 LLM 返回。"""
        # 有些小模型可能在 JSON 外包 markdown 代码块
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # 去掉首尾代码栅栏
            cleaned = cleaned.strip("`").lstrip("json").strip()

        try:
            obj = json.loads(cleaned)
        except json.JSONDecodeError:
            # 找首个 [ 和最后 ]，提取数组（兜底）
            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start >= 0 and end > start:
                try:
                    obj = {"labels": json.loads(cleaned[start : end + 1])}
                except json.JSONDecodeError:
                    return [_unknown() for _ in range(expected)]
            else:
                return [_unknown() for _ in range(expected)]

        items: list[object] = []
        if isinstance(obj, dict):
            value = obj.get("labels")
            if isinstance(value, list):
                items = list(value)
            elif isinstance(obj.get("data"), list):
                items = list(obj["data"])  # 兼容 OpenAI 风格
        elif isinstance(obj, list):
            items = list(obj)

        labels: list[ChunkLabel] = []
        for entry in items[:expected]:
            labels.append(self._coerce_label(entry))
        # 数量不足时补 unknown
        while len(labels) < expected:
            labels.append(_unknown())
        return labels

    def _coerce_label(self, entry: object) -> ChunkLabel:
        if not isinstance(entry, dict):
            return _unknown()
        mood_raw = str(entry.get("mood") or "").strip()
        mood = mood_raw if mood_raw in self._mood_vocab else "unknown"
        topic = str(entry.get("topic") or "").strip()[:8]
        importance_raw = entry.get("importance")
        importance = 1
        if isinstance(importance_raw, (int, float)):
            importance = max(0, min(3, int(importance_raw)))
        elif isinstance(importance_raw, str):
            try:
                importance = max(0, min(3, int(importance_raw)))
            except ValueError:
                pass
        return ChunkLabel(mood=mood, topic=topic, importance=importance)


def _unknown() -> ChunkLabel:
    return ChunkLabel(mood="unknown", topic="", importance=1)
