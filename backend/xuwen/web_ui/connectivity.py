"""连通性测试：向用户填写的 base_url + key 发一次最短请求，返回友好结果。

设计：
- 不依赖正在运行的 settings；直接接收用户提交的字段值
- 错误统一翻译为中文 + 给出下一步建议
- 超时 8 秒，避免前端卡死
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


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
        return "密钥无效或已过期。请确认从服务商网站复制的密钥完整、未泄漏。"
    if status == 403:
        return "服务商拒绝了请求。常见原因：账户余额不足、未启用 API、IP 未授权。"
    if status == 404:
        return "接口路径找不到。请检查接口地址是否正确（不要包含 /chat/completions 这种后缀）。"
    if status == 429:
        return "服务商限流了。请稍后再试，或降低并发设置。"
    if 500 <= status < 600:
        return f"服务商内部错误（HTTP {status}）。建议稍后重试。"
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
        return "连接超时。请检查网络、是否需要代理、接口地址是否正确。"
    if isinstance(exc, httpx.ProxyError):
        return "代理连接失败。请检查代理设置（HTTP_PROXY/HTTPS_PROXY）或关闭代理后重试。"
    if isinstance(exc, httpx.ConnectError):
        # ConnectError 会聚合 DNS / 拒绝连接 / SSL 握手等底层原因，能拿到文本就细化
        if "ssl" in text or "certificate" in text:
            return "SSL/TLS 错误。如果使用本地服务（http://127.0.0.1）请确认协议是 http 而非 https。"
        if (
            "name or service not known" in text
            or "nodename nor servname" in text
            or "temporary failure in name resolution" in text
            or "getaddrinfo" in text
        ):
            return "域名解析失败。请确认接口地址拼写正确且能访问外网。"
        if "connection refused" in text or "[errno 111]" in text:
            return "服务拒绝连接。如果是本地服务（如 Ollama），请确认服务已启动。"
        return f"无法连接到服务（{name}）。请检查接口地址、网络或代理设置。"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "服务端异常断开连接。请稍后重试，或确认接口地址与协议是否正确。"

    # 2) 文本兜底：兼容非 httpx 异常，按原有关键词给中文
    if "timeout" in text or "timed out" in text:
        return "连接超时。请检查网络、是否需要代理、接口地址是否正确。"
    if "ssl" in text:
        return "SSL/TLS 错误。如果使用本地服务（http://127.0.0.1）请确认协议是 http 而非 https。"
    if "name or service not known" in text or "nodename nor servname" in text:
        return "域名解析失败。请确认接口地址拼写正确且能访问外网。"
    if "connection refused" in text:
        return "服务拒绝连接。如果是本地服务（如 Ollama），请确认服务已启动。"

    # 3) 最终兜底——永不为空，带上异常类名与可得原因
    if isinstance(exc, httpx.TransportError):
        return f"网络传输错误（{name}）：{_describe_exc(exc)}"
    return f"网络错误（{name}）：{_describe_exc(exc)}"


async def test_openai_chat(
    base_url: str,
    api_key: str,
    model: str,
) -> TestResult:
    """对 OpenAI 兼容 /chat/completions 发一次最短请求。"""
    if not base_url:
        return TestResult(ok=False, message="未填写接口地址")
    if not api_key:
        return TestResult(ok=False, message="未填写密钥")
    if not model:
        return TestResult(ok=False, message="未填写模型名")

    url = base_url.rstrip("/") + "/chat/completions"
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
    return TestResult(
        ok=True,
        message=f"连接成功，模型 {model} 可用",
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
        return TestResult(ok=False, message="未填写接口地址")
    if not api_key:
        return TestResult(ok=False, message="未填写密钥")
    if not model:
        return TestResult(ok=False, message="未填写模型名")

    url = base_url.rstrip("/") + "/embeddings"
    payload: dict[str, Any] = {
        "model": model,
        "input": "测试" if input_mode == "single" else ["测试"],
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
            msg += "（提示：可尝试把'高级 → EMBEDDING_INPUT_MODE'改为 single，或关闭 encoding_format）"
        return TestResult(ok=False, message=msg, detail=resp.text[:1000])

    try:
        data = resp.json()
        emb = data.get("data", [{}])[0].get("embedding", [])
        got_dim = len(emb)
    except Exception:
        return TestResult(
            ok=False,
            message="返回内容不是合法的 embedding 响应",
            detail=resp.text[:500],
        )
    if dim and got_dim and got_dim != dim:
        return TestResult(
            ok=False,
            message=(
                f"维度不匹配：你填的是 {dim}，但服务商返回 {got_dim}。"
                f"请把'向量维度'改为 {got_dim}，或换一个支持 {dim} 维的模型。"
            ),
            extra={"actual_dim": got_dim, "expected_dim": dim},
        )
    return TestResult(
        ok=True,
        message=f"连接成功，向量维度 {got_dim}",
        extra={"actual_dim": got_dim},
    )
