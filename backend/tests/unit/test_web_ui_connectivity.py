"""连通性测试的错误翻译回归测试。

重点防回归：网络异常时错误原因不能为空（issue #10）。
httpx 的网络异常（ConnectTimeout/ReadTimeout/ConnectError 等）str(exc)
经常是空字符串，旧实现的子串匹配会全部落空并退到 f"网络错误：{exc}"，
产生"网络错误："这种冒号后为空的消息。
"""

from __future__ import annotations

import httpx
import pytest

from xuwen.web_ui.connectivity import _describe_exc, _explain_network_error

# 这些异常的 str() 在实践中常为空——正是 issue #10 的触发场景
_EMPTY_STR_NETWORK_ERRORS = [
    httpx.ConnectTimeout(""),
    httpx.ReadTimeout(""),
    httpx.WriteTimeout(""),
    httpx.PoolTimeout(""),
    httpx.ConnectError(""),
    httpx.ReadError(""),
    httpx.RemoteProtocolError(""),
    httpx.ProxyError(""),
]


@pytest.mark.parametrize("exc", _EMPTY_STR_NETWORK_ERRORS, ids=lambda e: type(e).__name__)
def test_explain_network_error_never_empty(exc: Exception) -> None:
    """任何网络异常的中文解释都必须非空，且不能以悬空的冒号结尾。"""
    msg = _explain_network_error(exc)
    assert msg.strip(), f"{type(exc).__name__} 的解释为空"
    assert not msg.rstrip().endswith(("：", ":")), f"{type(exc).__name__} 解释以悬空冒号结尾：{msg!r}"


def test_timeout_classified_by_type() -> None:
    """超时类异常即便 str() 为空也应被识别为'连接超时'（旧实现的类名判断失效）。"""
    for exc in (httpx.ConnectTimeout(""), httpx.ReadTimeout(""), httpx.PoolTimeout("")):
        assert "超时" in _explain_network_error(exc)


def test_proxy_error_classified() -> None:
    assert "代理" in _explain_network_error(httpx.ProxyError(""))


def test_connect_error_subcauses() -> None:
    """ConnectError 聚合了 DNS / 拒绝连接 / SSL，能拿到文本时应给出针对性提示。"""
    assert "域名解析" in _explain_network_error(
        httpx.ConnectError("[Errno -2] Name or service not known")
    )
    assert "拒绝连接" in _explain_network_error(
        httpx.ConnectError("[Errno 111] Connection refused")
    )
    assert "SSL" in _explain_network_error(httpx.ConnectError("certificate verify failed"))


def test_connect_error_empty_falls_back_with_type_name() -> None:
    """无文本可用的 ConnectError 也要给出非空提示并带上异常类名。"""
    msg = _explain_network_error(httpx.ConnectError(""))
    assert msg.strip()
    assert "ConnectError" in msg


def test_non_httpx_exception_fallback() -> None:
    """非 httpx 异常走最终兜底，仍需非空并带类名。"""
    empty = _explain_network_error(RuntimeError(""))
    assert empty.strip()
    assert "RuntimeError" in empty
    assert "boom" in _explain_network_error(ValueError("boom"))


def test_describe_exc_never_empty() -> None:
    """_describe_exc 对空字符串异常也必须返回非空（回退到类名）。"""
    assert _describe_exc(httpx.ConnectError("")).strip()
    assert _describe_exc(RuntimeError("")).strip()
    assert _describe_exc(ValueError("具体原因")) == "具体原因"


def test_describe_exc_uses_cause_when_str_empty() -> None:
    """str(exc) 为空时应回退到底层 cause 的信息。"""
    try:
        try:
            raise OSError("底层网络原因")
        except OSError as root:
            raise httpx.ConnectError("") from root
    except httpx.ConnectError as exc:
        assert "底层网络原因" in _describe_exc(exc)
