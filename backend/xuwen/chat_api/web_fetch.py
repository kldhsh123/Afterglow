"""后端可选网页读取上下文。

用户显式发来 URL 时，后端可以读取公开网页的标题和正文摘要，再注入 prompt。
这不是浏览器，也不会执行 JavaScript；只处理普通 http/https 文本页面。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.config import Settings
from xuwen.content_i18n import content_text
from xuwen.core.errors import LLMError
from xuwen.core.metrics import MetricsRecorder
from xuwen.error_i18n import error_text

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>'\"，。！？、；：）)】]+", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w.-])"
    r"("
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|cn|net|org|top|io|dev|app|me|xyz|site|ai|cc|club|info|wiki|tech|ink)"
    r"(?::\d{1,5})?"
    r"(?:/[^\s<>'\"，。！？、；：）)】]*)?"
    r")",
    re.IGNORECASE,
)
_TEXT_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
)
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}
_FETCH_INTENT_TRIGGERS = (
    "网站",
    "网页",
    "链接",
    "打开",
    "访问",
    "看看",
    "看一下",
    "看下",
    "读一下",
    "读读",
    "里面",
    "官网",
    "是什么",
    "什么呀",
    "查一下",
    "搜一下",
)


@dataclass(slots=True, frozen=True)
class WebFetchResult:
    url: str
    title: str
    text: str


class UnsafeURL(ValueError):
    """URL 不适合由后端访问。"""


class WebFetchClient:
    """安全受限的网页读取客户端。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        timeout = max(0.1, settings.web_fetch_timeout_seconds or 8.0)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout,
                connect=min(timeout, 5.0),
                read=timeout,
                write=timeout,
                pool=timeout,
            ),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_many(
        self,
        urls: list[str],
        *,
        trace_id: str = "",
        metrics: MetricsRecorder | None = None,
    ) -> list[WebFetchResult]:
        if not self.settings.web_access_enabled or not self.settings.web_fetch_enabled:
            return []
        unique = _dedupe_urls(urls)[: self.settings.web_fetch_max_urls]
        results: list[WebFetchResult] = []
        for url in unique:
            item = await self.fetch(url, trace_id=trace_id, metrics=metrics)
            if item is not None:
                results.append(item)
        return results

    async def fetch(
        self,
        url: str,
        *,
        trace_id: str = "",
        metrics: MetricsRecorder | None = None,
    ) -> WebFetchResult | None:
        start = time.perf_counter()
        try:
            final_url, content_type, body = await self._download(url)
            title, text = extract_readable_text(
                body,
                content_type=content_type,
                max_chars=self.settings.web_fetch_max_chars,
            )
            if not text:
                raise ValueError("empty_text")
            result = WebFetchResult(url=final_url, title=title or final_url, text=text)
            _record_fetch_metric(
                metrics,
                trace_id=trace_id,
                url=url,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="ok",
                chars=len(text),
            )
            return result
        except Exception as e:
            logger.warning(
                content_text("Webpage fetch failed; skipping URL context: %s"), type(e).__name__
            )
            _record_fetch_metric(
                metrics,
                trace_id=trace_id,
                url=url,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="error",
                error=type(e).__name__,
            )
            return None

    async def _download(self, url: str) -> tuple[str, str, bytes]:
        current = url.strip()
        max_redirects = self.settings.web_fetch_max_redirects
        for _ in range(max_redirects + 1):
            await ensure_safe_url(current)
            async with self._client.stream(
                "GET",
                current,
                headers={
                    "User-Agent": f"{self.settings.app_name}/0.1 URLFetcher",
                    "Accept": "text/html,text/plain,application/xhtml+xml,application/json;q=0.8,*/*;q=0.2",
                },
            ) as resp:
                if 300 <= resp.status_code < 400 and resp.headers.get("location"):
                    current = urljoin(str(resp.url), resp.headers["location"])
                    continue

                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if not _is_text_content_type(content_type):
                    raise ValueError("unsupported_content_type")

                data = bytearray()
                async for chunk in resp.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self.settings.web_fetch_max_bytes:
                        raise ValueError("response_too_large")
                await ensure_safe_url(str(resp.url))
                return str(resp.url), content_type, bytes(data)

        raise ValueError("too_many_redirects")


def extract_urls(text: str, *, limit: int = 5) -> list[str]:
    """从用户文本中提取 URL，保留原顺序并去重。

    支持两类输入：
    - 完整 URL：`https://example.com/a`
    - 裸域名：`example.com`，自动按 `https://example.com` 读取
    """
    matches: list[tuple[int, int, str]] = []
    full_spans: list[tuple[int, int]] = []
    for m in _URL_RE.finditer(text):
        url = _strip_url_tail(m.group(0))
        if url:
            matches.append((m.start(), m.end(), url))
            full_spans.append((m.start(), m.end()))

    for m in _BARE_DOMAIN_RE.finditer(text):
        if _overlaps_any(m.start(), m.end(), full_spans):
            continue
        domain = _strip_url_tail(m.group(1))
        if domain:
            matches.append((m.start(), m.end(), f"https://{domain}"))

    matches.sort(key=lambda item: item[0])
    return _dedupe_urls([item[2] for item in matches])[:limit]


def extract_direct_urls(text: str, *, limit: int = 5) -> list[str]:
    """只提取显式 http/https URL。"""
    found = [_strip_url_tail(m.group(0)) for m in _URL_RE.finditer(text)]
    return _dedupe_urls([u for u in found if u])[:limit]


def extract_bare_domain_urls(text: str, *, limit: int = 5) -> list[str]:
    """只提取裸域名，并规范化为 https URL。"""
    full_spans = [(m.start(), m.end()) for m in _URL_RE.finditer(text)]
    found: list[str] = []
    for m in _BARE_DOMAIN_RE.finditer(text):
        if _overlaps_any(m.start(), m.end(), full_spans):
            continue
        domain = _strip_url_tail(m.group(1))
        if domain:
            found.append(f"https://{domain}")
    return _dedupe_urls(found)[:limit]


def should_confirm_bare_domain_fetch(text: str) -> bool:
    """本地轻量门控：只有像是在要求访问网站时，才需要小模型确认。"""
    lowered = text.strip().lower()
    if not lowered:
        return False
    return any(trigger in lowered for trigger in _FETCH_INTENT_TRIGGERS)


async def resolve_fetch_urls(
    text: str,
    *,
    llm: LLMClient,
    model: str,
    limit: int,
    trace_id: str = "",
    metrics: MetricsRecorder | None = None,
) -> list[str]:
    """解析本轮需要读取的 URL。

    策略：
    - 显式 http/https URL：直接读取，不额外调用小模型。
    - 裸域名：只有本地意图门控命中时，才调用小模型确认并规范化。
    - 普通聊天：不调小模型，不联网读取。
    """
    direct = extract_direct_urls(text, limit=limit)
    if direct:
        _record_intent_metric(
            metrics,
            trace_id=trace_id,
            status="skipped",
            reason="direct_url",
            candidate_count=len(direct),
        )
        return direct[:limit]

    candidates = extract_bare_domain_urls(text, limit=limit)
    if not candidates:
        _record_intent_metric(
            metrics,
            trace_id=trace_id,
            status="skipped",
            reason="no_candidate_domain",
            candidate_count=0,
        )
        return []
    if not should_confirm_bare_domain_fetch(text):
        _record_intent_metric(
            metrics,
            trace_id=trace_id,
            status="skipped",
            reason="local_intent_not_matched",
            candidate_count=len(candidates),
        )
        return []

    start = time.perf_counter()
    try:
        raw = await llm.complete_chat(
            _build_url_intent_messages(text, candidates),
            GenerationParams(temperature=0.0, max_tokens=220),
            model=model,
            trace_id=trace_id,
            stage="web.intent",
            metrics=metrics,
        )
        urls = _parse_intent_response(raw, candidates, limit=limit)
        _record_intent_metric(
            metrics,
            trace_id=trace_id,
            status="ok",
            reason="model_confirmed" if urls else "model_rejected",
            candidate_count=len(candidates),
            result_count=len(urls),
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        return urls
    except (LLMError, ValueError, json.JSONDecodeError) as e:
        logger.warning(
            content_text("URL intent detection failed; skipping bare-domain fetch: %s"),
            type(e).__name__,
        )
        _record_intent_metric(
            metrics,
            trace_id=trace_id,
            status="error",
            reason=type(e).__name__,
            candidate_count=len(candidates),
            latency_ms=(time.perf_counter() - start) * 1000,
            error=type(e).__name__,
        )
        return []


async def ensure_safe_url(url: str) -> None:
    """拒绝本机、内网、链路本地等不适合后端访问的地址。"""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURL(error_text("Only http/https URLs are allowed"))
    host = parsed.hostname
    if not host:
        raise UnsafeURL(error_text("The URL is missing a host"))
    if host.lower().strip(".") in _BLOCKED_HOSTS:
        raise UnsafeURL(error_text("Localhost addresses are not allowed"))

    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None:
        _assert_public_ip(parsed_ip)
        return

    infos = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
    if not infos:
        raise UnsafeURL(error_text("Cannot resolve the domain name"))
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = ipaddress.ip_address(str(sockaddr[0]))
        _assert_public_ip(ip)


def extract_readable_text(
    body: bytes,
    *,
    content_type: str,
    max_chars: int,
) -> tuple[str, str]:
    """从 HTML / 纯文本响应中抽取适合塞进 prompt 的正文。"""
    text = _decode_body(body, content_type)
    if _is_html_content_type(content_type):
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
            tag.decompose()
        title = " ".join((soup.title.string or "").split()) if soup.title else ""
        chunks = [chunk.strip() for chunk in soup.get_text("\n").splitlines() if chunk.strip()]
        readable = "\n".join(chunks)
        return title, _short(readable, max_chars)
    return "", _short(" ".join(text.split()), max_chars)


def render_url_context(results: list[WebFetchResult]) -> str:
    if not results:
        return ""
    lines = []
    for i, item in enumerate(results, 1):
        lines.append(
            f"[{i}] {_short(item.title, 100)}\n"
            f"URL: {_short(item.url, 180)}\n"
            f"Content excerpt: {_short(item.text, 1200)}"
        )
    return "\n\n".join(lines)


def _record_fetch_metric(
    metrics: MetricsRecorder | None,
    *,
    trace_id: str,
    url: str,
    latency_ms: float,
    status: str,
    chars: int = 0,
    error: str = "",
) -> None:
    if metrics is None:
        return
    detail = f"trace={trace_id},chars={chars},url_chars={len(url)}"
    metrics.record(
        "web.fetch",
        latency_ms,
        error=error if status == "error" else None,
        detail=detail,
    )


def _record_intent_metric(
    metrics: MetricsRecorder | None,
    *,
    trace_id: str,
    status: str,
    reason: str,
    candidate_count: int,
    result_count: int = 0,
    latency_ms: float = 0.0,
    error: str = "",
) -> None:
    if metrics is None:
        return
    metrics.record(
        "web.intent",
        latency_ms,
        error=error if status == "error" else None,
        detail=(
            f"trace={trace_id},reason={reason},candidates={candidate_count},results={result_count}"
        ),
    )


def _build_url_intent_messages(text: str, candidates: list[str]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Classify web-reading intent in any language. Return JSON only. "
                "Decide whether the user asks to read, open or inspect candidate websites. "
                "Use should_fetch=false for casual mentions, domain purchases, email, code or no request to access content. "
                'Return: {"should_fetch": true|false, "urls": ["https://..."]}。'
                "Select urls only from the candidate URLs; never invent URLs."
            ),
        },
        {
            "role": "user",
            "content": (
                "User message:\n"
                f"{text}\n\n"
                "Candidate URLs:\n" + "\n".join(f"- {u}" for u in candidates)
            ),
        },
    ]


def _parse_intent_response(raw: str, candidates: list[str], *, limit: int) -> list[str]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("intent_response_not_object")
    if data.get("should_fetch") is not True:
        return []
    candidate_set = set(candidates)
    raw_urls = data.get("urls")
    if not isinstance(raw_urls, list):
        return []
    urls: list[str] = []
    for item in raw_urls:
        if not isinstance(item, str):
            continue
        normalized = _normalize_model_url(item)
        if normalized in candidate_set:
            urls.append(normalized)
    return _dedupe_urls(urls)[:limit]


def _normalize_model_url(url: str) -> str:
    cleaned = _strip_url_tail(url.strip())
    if not cleaned:
        return ""
    if not cleaned.lower().startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    parsed = urlparse(cleaned)
    if parsed.path == "/" and not parsed.params and not parsed.query and not parsed.fragment:
        cleaned = cleaned.rstrip("/")
    return cleaned


def _assert_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UnsafeURL(error_text("Private or special-use addresses are not allowed"))


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        normalized = url.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _strip_url_tail(url: str) -> str:
    return url.rstrip("，。！？、,.!?;；：:")


def _overlaps_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _is_text_content_type(content_type: str) -> bool:
    lowered = content_type.split(";", 1)[0].strip().lower()
    return lowered in _TEXT_CONTENT_TYPES or lowered.startswith("text/")


def _is_html_content_type(content_type: str) -> bool:
    lowered = content_type.split(";", 1)[0].strip().lower()
    return lowered in {"text/html", "application/xhtml+xml"}


def _decode_body(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip("\"'")
            break
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _short(text: str, limit: int) -> str:
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
