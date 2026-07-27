"""update_check 模块单测。

覆盖语义版本比较 + UpdateChecker._check_once（mock GitHub API）的成功 / 失败路径。
不发起任何真实网络请求（用 respx 拦截 httpx）。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from xuwen.config import Settings
from xuwen.core.update_check import UpdateChecker, UpdateInfo, is_outdated


def test_is_outdated_simple():
    assert is_outdated("0.1.0", "0.2.0") is True
    assert is_outdated("0.2.0", "0.2.0") is False
    assert is_outdated("0.3.0", "0.2.0") is False
    assert is_outdated("v0.1.0", "v0.2.0") is True


def test_is_outdated_rejects_prerelease():
    """预发布版本（含 -rc / -beta）保守返回 False，避免误报。"""
    assert is_outdated("0.2.0", "0.3.0-rc1") is False
    assert is_outdated("0.2.0-beta", "0.3.0") is False


def test_is_outdated_rejects_invalid():
    assert is_outdated("0.1", "0.2.0") is False
    assert is_outdated("abc", "0.2.0") is False


def _settings(**overrides) -> Settings:
    base = {
        "self_name": "Me",
        "self_uid": "u-self",
        "friend_name": "TA",
        "friend_uid": "u-friend",
        "update_check_enabled": True,
        "update_check_url": "https://api.test/releases/latest",
        "update_check_timeout_seconds": 2.0,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_check_once_parses_release():
    settings = _settings()
    payload = {
        "tag_name": "v0.3.0",
        "html_url": "https://github.com/example/repo/releases/tag/v0.3.0",
        "published_at": "2026-06-01T12:00:00Z",
        "body": "新增功能 A B C；修复 bug X；" * 10,
    }
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(
                return_value=httpx.Response(200, json=payload),
            )
            await checker._check_once()
    info = checker.snapshot()
    assert info.latest_version == "0.3.0"
    assert info.is_outdated is True
    assert info.release_url == "https://github.com/example/repo/releases/tag/v0.3.0"
    assert info.released_at == "2026-06-01T12:00:00Z"
    assert info.release_notes_preview is not None
    assert "新增功能 A B C" in info.release_notes_preview
    assert info.last_error is None
    assert info.last_checked_at_ms is not None


@pytest.mark.asyncio
async def test_check_once_handles_404():
    settings = _settings()
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(
                return_value=httpx.Response(404, json={"message": "Not Found"}),
            )
            await checker._check_once()
    info = checker.snapshot()
    assert info.last_error == "HTTP 404"
    assert info.latest_version is None
    assert info.is_outdated is False


@pytest.mark.asyncio
async def test_check_once_handles_network_error():
    settings = _settings()
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(
                side_effect=httpx.ConnectError("offline"),
            )
            await checker._check_once()
    info = checker.snapshot()
    assert info.last_error is not None
    assert "ConnectError" in info.last_error
    assert info.latest_version is None


@pytest.mark.asyncio
async def test_check_once_handles_invalid_json():
    settings = _settings()
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(
                return_value=httpx.Response(200, text="not json"),
            )
            await checker._check_once()
    info = checker.snapshot()
    assert info.last_error == "响应非 JSON"


@pytest.mark.asyncio
async def test_start_noop_when_disabled():
    """update_check_enabled=False 时 start() 直接 no-op，不起 task。"""
    settings = _settings(update_check_enabled=False)
    checker = UpdateChecker(settings, current_version="0.2.0")
    await checker.start()
    assert checker._task is None  # type: ignore[attr-defined]
    await checker.stop()


@pytest.mark.asyncio
async def test_snapshot_initial_state():
    settings = _settings(update_check_enabled=False)
    checker = UpdateChecker(settings, current_version="0.2.0")
    info = checker.snapshot()
    assert isinstance(info, UpdateInfo)
    assert info.check_enabled is False
    assert info.current_version == "0.2.0"
    assert info.latest_version is None
    assert info.is_outdated is False
    await checker.stop()


@pytest.mark.asyncio
async def test_force_check_now_throttles_repeated_calls():
    """5 秒内重复调用 force_check_now 应该走缓存，不再打 API。"""
    settings = _settings()
    payload = {
        "tag_name": "v0.3.0",
        "html_url": "https://example.com/r",
        "body": "x",
    }
    call_count = 0

    def _counter(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(side_effect=_counter)
            info1 = await checker.force_check_now()
            info2 = await checker.force_check_now()
            info3 = await checker.force_check_now()

    assert call_count == 1, "节流应让后续两次调用走缓存"
    assert info1.latest_version == "0.3.0"
    assert info2 is info3


@pytest.mark.asyncio
async def test_check_once_prints_outdated_to_stdout(capsys):
    """成功检查到新版本时应该把状态打印到 stdout（控制台可见）。"""
    settings = _settings()
    payload = {
        "tag_name": "v0.3.0",
        "html_url": "https://example.com/r",
        "body": "release notes",
    }
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(
                return_value=httpx.Response(200, json=payload),
            )
            await checker._check_once()

    out = capsys.readouterr().out
    assert "[更新检查]" in out
    assert "发现新版本" in out
    assert "0.2.0" in out and "0.3.0" in out


@pytest.mark.asyncio
async def test_check_once_prints_up_to_date(capsys):
    """已是最新版应该打印 "已是最新版" 提示。"""
    settings = _settings()
    payload = {"tag_name": "v0.2.0", "html_url": "https://x.com", "body": ""}
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(
                return_value=httpx.Response(200, json=payload),
            )
            await checker._check_once()

    out = capsys.readouterr().out
    assert "已是最新版" in out
    assert "0.2.0" in out


@pytest.mark.asyncio
async def test_check_once_does_not_repeat_same_status(capsys):
    """状态没变时不应该重复打日志，避免周期检查刷屏。"""
    settings = _settings()
    payload = {"tag_name": "v0.3.0", "html_url": "https://x.com", "body": "x"}
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(
                return_value=httpx.Response(200, json=payload),
            )
            await checker._check_once()
            first_out = capsys.readouterr().out
            assert "发现新版本" in first_out

            # 紧接着相同状态再查一次（这里不走 throttle，因为我们直接调 _check_once）
            await checker._check_once()
            second_out = capsys.readouterr().out
            assert second_out == "", "相同状态不应重复打印"


@pytest.mark.asyncio
async def test_check_once_prints_state_transition(capsys):
    """状态从 outdated → up-to-date 应该重新打印一次。"""
    settings = _settings()
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            route = router.get("https://api.test/releases/latest")
            route.mock(
                return_value=httpx.Response(
                    200,
                    json={"tag_name": "v0.3.0", "html_url": "https://x.com", "body": "x"},
                ),
            )
            await checker._check_once()
            capsys.readouterr()  # 丢弃首次输出

            # 用户升级到 0.3.0 后，状态从"有新版本"变为"已是最新版"
            checker.current_version = "0.3.0"
            await checker._check_once()
            out = capsys.readouterr().out
            assert "已是最新版" in out


@pytest.mark.asyncio
async def test_check_once_prints_error(capsys):
    """检查失败应该把错误打印到 stdout。"""
    settings = _settings()
    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(
                return_value=httpx.Response(503),
            )
            await checker._check_once()

    out = capsys.readouterr().out
    assert "[更新检查]" in out
    assert "失败" in out
    assert "HTTP 503" in out


@pytest.mark.asyncio
async def test_start_disabled_prints_status(capsys):
    """UPDATE_CHECK_ENABLED=false 时 start() 应该打印禁用提示。"""
    settings = _settings(update_check_enabled=False)
    checker = UpdateChecker(settings, current_version="0.2.0")
    await checker.start()
    out = capsys.readouterr().out
    assert "[更新检查]" in out
    assert "已禁用" in out
    assert "0.2.0" in out
    assert checker._task is None  # type: ignore[attr-defined]
    await checker.stop()


@pytest.mark.asyncio
async def test_start_runs_check_once_and_finishes():
    """启用状态下 start() 应该跑一次检查就让 task 结束，不再持续 loop。"""
    settings = _settings()
    payload = {"tag_name": "v0.2.0", "html_url": "https://x.com", "body": ""}
    call_count = 0

    def _counter(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient() as client:
        checker = UpdateChecker(settings, current_version="0.2.0", client=client)
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.test/releases/latest").mock(side_effect=_counter)
            await checker.start()
            # 让启动 task 完成
            assert checker._task is not None  # type: ignore[attr-defined]
            await checker._task  # type: ignore[attr-defined]
            assert call_count == 1, "启动应该只调用一次 GitHub API"
            assert checker._task.done()  # type: ignore[attr-defined]
        await checker.stop()
