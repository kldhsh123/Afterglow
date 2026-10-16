"""连通性测试：向用户填写的 base_url + key 发一次最短请求，返回友好结果。

设计：
- 不依赖正在运行的 settings；直接接收用户提交的字段值
- 错误统一翻译为中文 + 给出下一步建议
- 超时 8 秒，避免前端卡死
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from xuwen.error_i18n import error_text


@dataclass
class TestResult:
    ok: bool
    message: str
    detail: str = ""  # 给开发者看的原始错误（前端可折叠显示）
    extra: dict[str, Any] | None = None


_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)


def _explain_http_error(status: int, body_text: str) -> str:
    """把 HTTP 错误翻译成小白能看懂的中文。"""
    snippet = body_text[:200].replace("\n", " ")
    if status == 401:
        return error_text(
            "The API key is invalid or expired. Check that you copied the complete key from your provider and that it has not been compromised."
        )
    if status == 403:
        return error_text(
            "The provider rejected the request. Check your account balance, API access and IP permissions."
        )
    if status == 404:
        return error_text(
            "The endpoint was not found. Check the API URL; leave off endpoint suffixes such as /chat/completions."
        )
    if status == 429:
        return error_text(
            "The provider rate limited the request. Try again later or reduce concurrency."
        )
    if 500 <= status < 600:
        return error_text("Provider server error (HTTP {p0}). Try again later.", p0=status)
    return f"HTTP {status}：{snippet}"


def _describe_exc(exc: Exception) -> str:
    """提取异常的非空原因文本，保证返回值非空。

    httpx 网络异常（ConnectTimeout/ReadTimeout/ConnectError 等）的 str(exc)
    经常是空字符串，直接拼接会得到"原因为空"。这里依次回退到底层
    cause/context、repr、类名，确保任何异常都能给出可读文本。
    """
    raw = str(exc).strip()
    if raw:
        return raw
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        c = str(cause).strip()
        return f"{type(cause).__name__}: {c}" if c else type(cause).__name__
    return repr(exc).strip() or type(exc).__name__


def _explain_network_error(exc: Exception) -> str:
    """网络层错误（DNS、连接、超时、代理）的中文解释。

    按 httpx 异常【类型】判定，而非依赖 str(exc)——后者对 httpx 网络异常
    常为空，旧的子串匹配会全部落空并退到空 fallback（"网络错误："）。
    所有分支都保证返回非空、可读的中文。
    """
    name = type(exc).__name__
    text = str(exc).lower()

    # 1) 按 httpx 异常类型判定（这些异常的 str(exc) 多为空，不能依赖文本）
    if isinstance(exc, httpx.TimeoutException):
        return error_text(
            "The connection timed out. Check your network, proxy requirements and API URL."
        )
    if isinstance(exc, httpx.ProxyError):
        return error_text(
            "The proxy connection failed. Check HTTP_PROXY/HTTPS_PROXY or retry with the proxy disabled."
        )
    if isinstance(exc, httpx.ConnectError):
        # ConnectError 会聚合 DNS / 拒绝连接 / SSL 握手等底层原因，能拿到文本就细化
        if "ssl" in text or "certificate" in text:
            return error_text(
                "SSL/TLS error. For a local service (http://127.0.0.1), check whether the URL should use http instead of https."
            )
        if (
            "name or service not known" in text
            or "nodename nor servname" in text
            or "temporary failure in name resolution" in text
            or "getaddrinfo" in text
        ):
            return error_text(
                "DNS lookup failed. Check the API hostname and your internet connection."
            )
        if "connection refused" in text or "[errno 111]" in text:
            return error_text(
                "The service refused the connection. For a local service such as Ollama, check that it is running."
            )
        return error_text(
            "Cannot connect to the service ({p0}). Check the API URL, network and proxy settings.",
            p0=name,
        )
    if isinstance(exc, httpx.RemoteProtocolError):
        return error_text(
            "The server closed the connection unexpectedly. Retry or check the API URL and protocol."
        )

    # 2) 文本兜底：兼容非 httpx 异常，按原有关键词给中文
    if "timeout" in text or "timed out" in text:
        return error_text(
            "The connection timed out. Check your network, proxy requirements and API URL."
        )
    if "ssl" in text:
        return error_text(
            "SSL/TLS error. For a local service (http://127.0.0.1), check whether the URL should use http instead of https."
        )
    if "name or service not known" in text or "nodename nor servname" in text:
        return error_text("DNS lookup failed. Check the API hostname and your internet connection.")
    if "connection refused" in text:
        return error_text(
            "The service refused the connection. For a local service such as Ollama, check that it is running."
        )

    # 3) 最终兜底——永不为空，带上异常类名与可得原因
    if isinstance(exc, httpx.TransportError):
        return error_text("Network transport error ({p0}): {p1}", p0=name, p1=_describe_exc(exc))
    return error_text("Network error ({p0}): {p1}", p0=name, p1=_describe_exc(exc))


async def test_openai_chat(
    base_url: str,
    api_key: str,
    model: str,
    protocol: Literal["chat_completions", "responses"] = "chat_completions",
) -> TestResult:
    """按所选协议对主模型发一次最短请求。"""
    if not base_url:
        return TestResult(ok=False, message=error_text("Enter an API URL"))
    if not api_key:
        return TestResult(ok=False, message=error_text("Enter an API key"))
    if not model:
        return TestResult(ok=False, message=error_text("Enter a model name"))

    root = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    if protocol == "responses":
        url = root + "/responses"
        payload = {
            "model": model,
            "input": [{"role": "user", "content": "ping"}],
            "max_output_tokens": 1,
            "stream": False,
            "store": False,
        }
    else:
        url = root + "/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception as e:
        return TestResult(
            ok=False,
            message=_explain_network_error(e),
            detail=f"{type(e).__name__}: {_describe_exc(e)}",
        )
    if resp.status_code >= 400:
        return TestResult(
            ok=False,
            message=_explain_http_error(resp.status_code, resp.text),
            detail=resp.text[:1000],
        )
    try:
        data = resp.json()
    except ValueError:
        return TestResult(
            ok=False,
            message=error_text(
                "The service returned an invalid chat response. Check the API URL and protocol."
            ),
            detail=resp.text[:1000],
        )
    if protocol == "responses":
        valid_response = isinstance(data, dict) and isinstance(data.get("output"), list)
    else:
        valid_response = isinstance(data, dict) and isinstance(data.get("choices"), list)
    if not valid_response:
        return TestResult(
            ok=False,
            message=error_text(
                "The service returned an invalid chat response. Check the API URL and protocol."
            ),
            detail=resp.text[:1000],
        )
    return TestResult(
        ok=True,
        message=error_text("Connected successfully. Model {p0} is available.", p0=model),
        extra={"status": resp.status_code},
    )


async def test_embedding(
    base_url: str,
    api_key: str,
    model: str,
    input_mode: str = "array",
    send_dimensions: bool = True,
    dim: int | None = None,
) -> TestResult:
    """对 OpenAI 兼容 /embeddings 发一条最短请求。

    会校验返回向量的维度是否与用户填写的 EMBEDDING_DIM 一致；不一致给出明确指引。
    """
    if not base_url:
        return TestResult(ok=False, message=error_text("Enter an API URL"))
    if not api_key:
        return TestResult(ok=False, message=error_text("Enter an API key"))
    if not model:
        return TestResult(ok=False, message=error_text("Enter a model name"))

    url = base_url.rstrip("/") + "/embeddings"
    payload: dict[str, Any] = {
        "model": model,
        "input": error_text("ping") if input_mode == "single" else [error_text("ping")],
    }
    if send_dimensions and dim:
        payload["dimensions"] = dim
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception as e:
        return TestResult(
            ok=False,
            message=_explain_network_error(e),
            detail=f"{type(e).__name__}: {_describe_exc(e)}",
        )
    if resp.status_code >= 400:
        msg = _explain_http_error(resp.status_code, resp.text)
        # 针对"格式不匹配"的额外友好提示
        if resp.status_code == 400 and "schema" in resp.text.lower():
            msg += error_text(
                " (Hint: try setting EMBEDDING_INPUT_MODE to single or disabling encoding_format.)"
            )
        return TestResult(ok=False, message=msg, detail=resp.text[:1000])

    try:
        data = resp.json()
        emb = data.get("data", [{}])[0].get("embedding", [])
        got_dim = len(emb)
    except Exception:
        return TestResult(
            ok=False,
            message=error_text("The service returned an invalid embedding response."),
            detail=resp.text[:500],
        )
    if dim and got_dim and got_dim != dim:
        return TestResult(
            ok=False,
            message=(
                error_text(
                    "Dimension mismatch: you entered {p0}, but the provider returned {p1}. Set vector dimensions to {p2} or choose a model supporting {p3} dimensions.",
                    p0=dim,
                    p1=got_dim,
                    p2=got_dim,
                    p3=dim,
                )
            ),
            extra={"actual_dim": got_dim, "expected_dim": dim},
        )
    return TestResult(
        ok=True,
        message=error_text("Connected successfully. Vector dimensions: {p0}.", p0=got_dim),
        extra={"actual_dim": got_dim},
    )
