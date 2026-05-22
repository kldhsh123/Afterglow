"""版本更新检查。

后端启动时一次性查询 GitHub Releases API，把最新发布版本缓存到内存供 /info 接口
返回。只请求公开 URL，不传任何身份 / 配置 / API key；用户在 `.env` 一键关闭即可。

不阻塞 lifespan 启动：UpdateChecker.start() 会 fire-and-forget 一个 background task。
后续要重新检查由前端"立即检查"按钮（POST /info/check-update）触发，避免后端无谓地
反复打 GitHub。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass

import httpx

from xuwen.config import Settings

logger = logging.getLogger(__name__)

_USER_AGENT = "Afterglow/update-check"
_RELEASE_NOTES_LIMIT = 240
# 手动触发检查的最小间隔：5 秒内重复点击只返回缓存，避免被刷
_MIN_FORCE_CHECK_INTERVAL_MS = 5000


@dataclass(slots=True)
class UpdateInfo:
    """对外暴露的更新状态。/info 接口直接序列化这个 dataclass。"""

    check_enabled: bool
    current_version: str
    latest_version: str | None = None
    is_outdated: bool = False
    released_at: str | None = None
    release_url: str | None = None
    release_notes_preview: str | None = None
    last_checked_at_ms: int | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def is_outdated(current: str, latest: str) -> bool:
    """比较 X.Y.Z 风格的语义版本。

    遇到预发布标签（rc / beta / dev 等）保守返回 False，避免误报。
    """
    cur = _parse_semver(current)
    lat = _parse_semver(latest)
    if cur is None or lat is None:
        return False
    return cur < lat


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    text = value.strip().lstrip("vV")
    # 拒绝预发布版本（含 -rc / -beta / + 等）
    if "-" in text or "+" in text:
        return None
    parts = text.split(".")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _short(text: str, limit: int = _RELEASE_NOTES_LIMIT) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


class UpdateChecker:
    """后端版本更新检查器。

    使用方式：
        checker = UpdateChecker(settings, current_version=__version__)
        await checker.start()  # 启动时一次性 fire-and-forget 检查
        ...
        await checker.stop()
        info = checker.snapshot()  # 拿到当前已知状态供 /info 返回
    """

    def __init__(
        self,
        settings: Settings,
        *,
        current_version: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.current_version = current_version
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.update_check_timeout_seconds),
        )
        self._info = UpdateInfo(
            check_enabled=settings.update_check_enabled,
            current_version=current_version,
        )
        self._task: asyncio.Task[None] | None = None
        # 记录上一次"已打日志"的状态签名，避免 force_check_now 重复打同样信息
        self._last_logged_signature: tuple[str | None, bool, bool] = (
            "__init__",
            False,
            False,
        )

    def snapshot(self) -> UpdateInfo:
        return self._info

    async def force_check_now(self) -> UpdateInfo:
        """手动触发立即检查，返回最新 snapshot。

        节流：5 秒内重复调用直接返回缓存，避免前端按钮被刷。
        UPDATE_CHECK_ENABLED=false 时也允许手动触发（用户主动行为优先于配置默认）。
        任何错误已在 _check_once 内部吞掉，调用方一定能拿到 snapshot。
        """
        now_ms = int(time.time() * 1000)
        if (
            self._info.last_checked_at_ms is not None
            and now_ms - self._info.last_checked_at_ms < _MIN_FORCE_CHECK_INTERVAL_MS
        ):
            return self._info
        await self._check_once()
        return self._info

    async def start(self) -> None:
        """启动时一次性触发版本检查（fire-and-forget），不阻塞 lifespan。

        无论开启 / 关闭，都会在启动时把状态打印一次到 stdout，方便运维确认。
        后续若想再查，由前端"立即检查"按钮（POST /info/check-update）发起。
        """
        if self._task is not None:
            return
        if not self.settings.update_check_enabled:
            message = (
                f"[更新检查] 已禁用（UPDATE_CHECK_ENABLED=false，"
                f"当前 v{self.current_version}）"
            )
            print(message, flush=True)
            logger.info(message)
            return
        self._task = asyncio.create_task(
            self._run_startup_check(), name="xuwen-update-check"
        )

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        if self._owned_client:
            await self._client.aclose()

    async def _run_startup_check(self) -> None:
        """启动时跑一次检查，吞掉任何意外异常以免污染 lifespan。

        _check_once 内部已经把网络 / 解析错误都吞了，这层 try/except 只是兜底。
        """
        try:
            await self._check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("启动版本检查失败", exc_info=True)

    async def _check_once(self) -> None:
        """拉一次 GitHub Releases API 并更新 snapshot。

        所有路径（成功 / HTTP 错误 / 网络错误 / 解析失败）结束后都会触发
        _log_status_if_changed，确保运维在控制台一次就能看清"是否最新 / 出错"。
        """
        try:
            try:
                resp = await self._client.get(
                    self.settings.update_check_url,
                    headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
                )
            except httpx.HTTPError as e:
                self._info.last_error = f"网络错误：{type(e).__name__}"
                self._info.last_checked_at_ms = int(time.time() * 1000)
                return

            if resp.status_code >= 400:
                self._info.last_error = f"HTTP {resp.status_code}"
                self._info.last_checked_at_ms = int(time.time() * 1000)
                return

            try:
                data = resp.json()
            except ValueError:
                self._info.last_error = "响应非 JSON"
                self._info.last_checked_at_ms = int(time.time() * 1000)
                return

            if not isinstance(data, dict):
                self._info.last_error = "响应格式异常"
                self._info.last_checked_at_ms = int(time.time() * 1000)
                return

            # GitHub Releases API 字段
            tag_name = str(data.get("tag_name") or data.get("name") or "").strip()
            if not tag_name:
                self._info.last_error = "上游未返回 tag_name"
                self._info.last_checked_at_ms = int(time.time() * 1000)
                return

            latest = tag_name.lstrip("vV")
            released_at = data.get("published_at") or data.get("created_at")
            release_url = data.get("html_url")
            body = data.get("body") or ""

            self._info = UpdateInfo(
                check_enabled=True,
                current_version=self.current_version,
                latest_version=latest,
                is_outdated=is_outdated(self.current_version, latest),
                released_at=str(released_at) if released_at else None,
                release_url=str(release_url) if release_url else None,
                release_notes_preview=_short(str(body)) if body else None,
                last_checked_at_ms=int(time.time() * 1000),
                last_error=None,
            )
        finally:
            self._log_status_if_changed()

    def _log_status_if_changed(self) -> None:
        """状态相比上次有变化时打印到 stdout + logger。

        - print(flush=True)：直接写 stdout，避免被 uvicorn 默认 log 配置吞掉，
          让运维一眼能看到"已是最新版 / 发现新版本 / 检查失败"。
        - logger.info：方便外部日志聚合（journald / docker logs / ELK）抓到。
        启动只查一次时这层去重影响有限；它主要保护前端连点"立即检查"按钮的
        场景，避免相同状态被反复打印。
        日志本身失败不应影响业务逻辑，所以全程吞掉异常。
        """
        info = self._info
        signature: tuple[str | None, bool, bool] = (
            info.latest_version,
            info.is_outdated,
            bool(info.last_error),
        )
        if signature == self._last_logged_signature:
            return
        self._last_logged_signature = signature

        if info.last_error:
            message = (
                f"[更新检查] 失败：{info.last_error}（当前 v{info.current_version}）"
            )
        elif info.is_outdated and info.latest_version:
            url = info.release_url or ""
            url_part = f"，详情 {url}" if url else ""
            message = (
                f"[更新检查] 发现新版本：v{info.current_version}"
                f" → v{info.latest_version}{url_part}"
            )
        elif info.latest_version:
            message = f"[更新检查] 已是最新版（v{info.current_version}）"
        else:
            # 兜底：理论上不会到达（没有 latest_version 也没有 error）
            message = f"[更新检查] 状态未知（当前 v{info.current_version}）"

        try:
            print(message, flush=True)
        except Exception:
            pass
        try:
            logger.info(message)
        except Exception:
            pass
