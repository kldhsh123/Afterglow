"""联网检索上下文辅助测试。"""

from __future__ import annotations

from xuwen.chat_api.web_search import (
    WebSearchResult,
    _parse_tavily_results,
    render_web_context,
    should_search_web,
)


def test_should_search_web_requires_explicit_intent():
    assert should_search_web("帮我查一下最新新闻")
    assert should_search_web("web: Python release")
    assert not should_search_web("你今天在干嘛")
    assert not should_search_web("想你了")


def test_render_web_context_formats_results():
    text = render_web_context(
        [
            WebSearchResult(
                title="示例结果",
                url="https://example.test/news",
                snippet="这是当前公开网页摘要",
            )
        ]
    )
    assert "示例结果" in text
    assert "来源：https://example.test/news" in text
    assert "摘要：这是当前公开网页摘要" in text


def test_parse_tavily_results():
    results = _parse_tavily_results(
        {
            "results": [
                {
                    "title": "Tavily 结果",
                    "url": "https://example.test/tavily",
                    "content": "来自 Tavily 的摘要",
                }
            ]
        },
        limit=5,
    )
    assert results == [
        WebSearchResult(
            title="Tavily 结果",
            url="https://example.test/tavily",
            snippet="来自 Tavily 的摘要",
        )
    ]
