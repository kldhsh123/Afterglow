"""网页读取上下文辅助测试。"""

from __future__ import annotations

import pytest

from xuwen.chat_api.web_fetch import (
    UnsafeURL,
    WebFetchResult,
    ensure_safe_url,
    extract_bare_domain_urls,
    extract_direct_urls,
    extract_readable_text,
    extract_urls,
    render_url_context,
    resolve_fetch_urls,
    should_confirm_bare_domain_fetch,
)


def test_extract_urls_dedupes_and_trims_punctuation():
    urls = extract_urls(
        "看看 https://example.com/a，另一个 https://example.com/b. 重复 https://example.com/a",
    )
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_extract_urls_supports_bare_domains():
    urls = extract_urls("kldhsh.top这个网站是什么呀，还有 docs.example.com/a")
    assert urls == ["https://kldhsh.top", "https://docs.example.com/a"]


def test_extract_urls_does_not_duplicate_domain_inside_full_url():
    urls = extract_urls("看看 https://kldhsh.top/a")
    assert urls == ["https://kldhsh.top/a"]


def test_extract_urls_ignores_email_domain():
    urls = extract_urls("我的邮箱是 test@example.com，不是网站")
    assert urls == []


def test_extract_direct_urls_does_not_include_bare_domain():
    assert extract_direct_urls("看看 kldhsh.top 和 https://example.com") == [
        "https://example.com",
    ]
    assert extract_bare_domain_urls("看看 kldhsh.top 和 https://example.com") == [
        "https://kldhsh.top",
    ]


def test_should_confirm_bare_domain_fetch_requires_local_intent():
    assert should_confirm_bare_domain_fetch("kldhsh.top这个网站是什么呀")
    assert should_confirm_bare_domain_fetch("看看 kldhsh.top")
    assert not should_confirm_bare_domain_fetch("我买了 kldhsh.top 这个域名")


@pytest.mark.asyncio
async def test_resolve_fetch_urls_direct_url_skips_small_model():
    llm = _FakeIntentLLM('{"should_fetch": false, "urls": []}')
    urls = await resolve_fetch_urls(
        "看看 https://example.com/a",
        llm=llm,  # type: ignore[arg-type]
        model="small",
        limit=2,
    )
    assert urls == ["https://example.com/a"]
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_resolve_fetch_urls_bare_domain_uses_small_model_when_intent_matches():
    llm = _FakeIntentLLM('{"should_fetch": true, "urls": ["https://kldhsh.top"]}')
    urls = await resolve_fetch_urls(
        "kldhsh.top这个网站是什么呀",
        llm=llm,  # type: ignore[arg-type]
        model="small",
        limit=2,
    )
    assert urls == ["https://kldhsh.top"]
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_resolve_fetch_urls_bare_domain_without_intent_skips_small_model():
    llm = _FakeIntentLLM('{"should_fetch": true, "urls": ["https://kldhsh.top"]}')
    urls = await resolve_fetch_urls(
        "我买了 kldhsh.top 这个域名",
        llm=llm,  # type: ignore[arg-type]
        model="small",
        limit=2,
    )
    assert urls == []
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_ensure_safe_url_rejects_localhost_and_private_ip():
    with pytest.raises(UnsafeURL):
        await ensure_safe_url("http://localhost:8000/debug/stats")
    with pytest.raises(UnsafeURL):
        await ensure_safe_url("http://127.0.0.1:8000/")
    with pytest.raises(UnsafeURL):
        await ensure_safe_url("http://192.168.1.2/")


def test_extract_readable_text_from_html():
    title, text = extract_readable_text(
        """
        <html>
          <head><title> 示例页面 </title><script>bad()</script></head>
          <body><h1>标题</h1><p>第一段正文。</p><style>.x{}</style></body>
        </html>
        """.encode(),
        content_type="text/html; charset=utf-8",
        max_chars=200,
    )
    assert title == "示例页面"
    assert "标题" in text
    assert "第一段正文" in text
    assert "bad()" not in text


def test_render_url_context_formats_results():
    text = render_url_context(
        [
            WebFetchResult(
                url="https://example.com/a",
                title="示例页面",
                text="这是网页正文。",
            )
        ]
    )
    assert "【网页读取结果】" not in text
    assert "示例页面" in text
    assert "URL：https://example.com/a" in text
    assert "正文摘录：这是网页正文。" in text


class _FakeIntentLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def complete_chat(self, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.response
