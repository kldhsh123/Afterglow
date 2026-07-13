"""视觉理解客户端：调用 OpenAI 兼容 VLM 把图片转文字描述。

适用场景：主 chat 模型不支持视觉，需要先用专门的 VLM 把图片转成一句描述，
再以纯文本注入到主模型 prompt 里。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

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


class VisionError(XuwenError):
    """VLM 调用失败。"""

    code = "xuwen.vision"
    http_status = 502


class _RetryableVisionError(VisionError):
    pass


class VisionClient:
    """OpenAI 兼容的 VLM 客户端，调用 chat/completions 端点描述图片。"""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.settings = settings
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        )
        # VLM 通常也走 /chat/completions 端点（多模态）
        self._url = _resolve_endpoint(str(settings.vision_api_url), "/chat/completions")
        self._headers = {
            "Authorization": f"Bearer {settings.vision_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def __aenter__(self) -> VisionClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def describe_images(self, data_urls: Sequence[str]) -> list[str]:
        """对每张图片返回一句描述。

        - 串行调用（避免一次性把所有图打到上游配额）
        - 单张失败时返回固定占位 "[图片：识别失败]"，不阻塞整个对话
        """
        out: list[str] = []
        for url in data_urls:
            try:
                desc = await self._describe_one(url)
            except VisionError as e:
                logger.warning("VLM 描述失败：%s", e.message)
                desc = "[图片：识别失败]"
            out.append(desc)
        return out

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _describe_one(self, data_url: str) -> str:
        payload = {
            "model": self.settings.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.settings.vision_describe_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "stream": False,
            "max_tokens": 120,
            "temperature": 0.3,
        }
        if self.settings.llm_reasoning_effort != "null":
            payload["reasoning_effort"] = self.settings.llm_reasoning_effort

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1.0, min=1.0, max=10.0),
            retry=retry_if_exception_type((httpx.HTTPError, _RetryableVisionError)),
            reraise=True,
        ):
            with attempt:
                return await self._call_once(payload)
        raise VisionError("VLM 重试退出（不应到达）")

    async def _call_once(self, payload: dict[str, object]) -> str:
        try:
            resp = await self._client.post(self._url, headers=self._headers, json=payload)
        except httpx.HTTPError as e:
            raise _RetryableVisionError(
                f"VLM 网络错误：{type(e).__name__}"
            ) from e

        if resp.status_code in (429,) or 500 <= resp.status_code < 600:
            logger.warning("VLM 上游 %d: %s", resp.status_code, resp.text[:500])
            raise _RetryableVisionError(
                f"VLM 暂时不可用（HTTP {resp.status_code}）",
                detail={"status": resp.status_code},
            )
        if resp.status_code >= 400:
            logger.error("VLM 上游 %d: %s", resp.status_code, resp.text[:500])
            raise VisionError(
                f"VLM 客户端错误（HTTP {resp.status_code}），请检查 VISION_API_KEY / VISION_MODEL（详情见日志）",
                detail={"status": resp.status_code},
            )

        try:
            data = resp.json()
        except ValueError as e:
            raise VisionError("VLM 返回非 JSON 响应") from e

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise VisionError("VLM 响应缺少 choices[0].message.content") from e

        # 兼容部分 VLM（如 Qwen-VL）会返回 content 是 list[{type:text,text:...}] 的情况
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
            content = "".join(text_parts)

        return str(content or "").strip() or "[图片：无描述]"
