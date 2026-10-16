"""后端可选联网检索上下文。

项目处理私人聊天记录，所以联网能力默认关闭；只有用户明确表达搜索意图时，
才会把本轮查询文本发送给配置的搜索服务。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from xuwen.config import Settings
from xuwen.content_i18n import content_text
from xuwen.core.metrics import MetricsRecorder

logger = logging.getLogger(__name__)

_SEARCH_TRIGGERS = (
    "查一下",
    "帮我查",
    "搜索",
    "搜一下",
    "联网",
    "网上",
    "新闻",
    "最新",
    "现在的",
    "官网",
    "价格",
    "天气",
    "汇率",
    "股价",
    "版本",
)


@dataclass(slots=True, frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str


class WebSearchClient:
    """Tavily / SearXNG 搜索客户端。

    默认使用 Tavily，因为它配置简单且有月度免费额度。
    使用方式：WEB_ACCESS_ENABLED=true，并填写 WEB_SEARCH_API_KEY。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        timeout = max(0.1, settings.web_search_timeout_seconds or 8.0)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout,
                connect=min(timeout, 5.0),
                read=timeout,
                write=timeout,
                pool=timeout,
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        *,
        trace_id: str = "",
        metrics: MetricsRecorder | None = None,
    ) -> list[WebSearchResult]:
        if not self.settings.web_access_enabled:
            return []
        query = query.strip()
        if not query or self.settings.web_search_max_results <= 0:
            return []

        start = time.perf_counter()
        try:
            if self.settings.web_search_provider == "searxng":
                results = await self._search_searxng(query)
            else:
                results = await self._search_tavily(query)
            _record_web_metric(
                metrics,
                query=query,
                trace_id=trace_id,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="ok",
                result_count=len(results),
            )
            return results
        except Exception as e:
            logger.warning(
                content_text("Web search failed; continuing without web context: %s"),
                type(e).__name__,
            )
            _record_web_metric(
                metrics,
                query=query,
                trace_id=trace_id,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="error",
                error=type(e).__name__,
            )
            return []

    async def _search_tavily(self, query: str) -> list[WebSearchResult]:
        api_key = self.settings.web_search_api_key.get_secret_value()
        if not api_key:
            logger.warning(
                content_text(
                    "WEB_ACCESS_ENABLED=true, but WEB_SEARCH_API_KEY is not configured; skipping Tavily"
                )
            )
            return []
        base = (self.settings.web_search_base_url or "https://api.tavily.com").rstrip("/")
        resp = await self._client.post(
            f"{base}/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": self.settings.web_search_max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        resp.raise_for_status()
        return _parse_tavily_results(resp.json(), self.settings.web_search_max_results)

    async def _search_searxng(self, query: str) -> list[WebSearchResult]:
        if not self.settings.web_search_base_url:
            return []
        url = self.settings.web_search_base_url.rstrip("/") + "/search"
        headers: dict[str, str] = {}
        api_key = self.settings.web_search_api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = await self._client.get(
            url,
            params={
                "q": query,
                "format": "json",
                "language": self.settings.web_search_language,
            },
            headers=headers,
        )
        resp.raise_for_status()
        return _parse_searxng_results(resp.json(), self.settings.web_search_max_results)


def should_search_web(text: str) -> bool:
    """只有用户明确要求公开实时信息时才触发联网检索。"""
    lowered = text.strip().lower()
    if not lowered:
        return False
    if any(token in lowered for token in _SEARCH_TRIGGERS):
        return True
    return lowered.startswith(("web:", "/web ", "search:", "/search "))


def render_web_context(results: list[WebSearchResult]) -> str:
    if not results:
        return ""
    lines = []
    for i, item in enumerate(results, 1):
        title = _short(item.title, 80)
        snippet = _short(item.snippet, 220)
        url = _short(item.url, 180)
        lines.append(f"[{i}] {title}\nSource: {url}\nSummary: {snippet}")
    return "\n\n".join(lines)


def _parse_searxng_results(data: object, limit: int) -> list[WebSearchResult]:
    if not isinstance(data, dict):
        return []
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return []
    results: list[WebSearchResult] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        snippet = str(raw.get("content") or raw.get("snippet") or "").strip()
        if not title and not snippet:
            continue
        results.append(WebSearchResult(title=title or url, url=url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def _parse_tavily_results(data: object, limit: int) -> list[WebSearchResult]:
    if not isinstance(data, dict):
        return []
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return []
    results: list[WebSearchResult] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        snippet = str(raw.get("content") or raw.get("snippet") or "").strip()
        if not title and not snippet:
            continue
        results.append(WebSearchResult(title=title or url, url=url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def _record_web_metric(
    metrics: MetricsRecorder | None,
    *,
    query: str,
    trace_id: str,
    latency_ms: float,
    status: str,
    result_count: int = 0,
    error: str = "",
) -> None:
    if metrics is None:
        return
    detail = f"trace={trace_id},results={result_count},query_chars={len(query)}"
    metrics.record(
        "web.search",
        latency_ms,
        error=error if status == "error" else None,
        detail=detail,
    )


def _short(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
