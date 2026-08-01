"""Settings 身份配置单测：focused on multi-UID（跨平台 / 跨账号）扩展。"""

from __future__ import annotations

import pytest

from xuwen.config import Settings
from xuwen.core.errors import ConfigError

MODEL_TIMEOUT_FIELDS = (
    "chat_timeout_seconds",
    "life_timeout_seconds",
    "vision_timeout_seconds",
    "embedding_timeout_seconds",
    "adaptive_chunk_timeout_seconds",
    "query_rewrite_timeout_seconds",
    "rerank_timeout_seconds",
    "cross_rerank_timeout_seconds",
    "response_policy_timeout_seconds",
    "schedule_timeout_seconds",
    "label_timeout_seconds",
)


@pytest.mark.parametrize("field_name", MODEL_TIMEOUT_FIELDS)
@pytest.mark.parametrize(
    "invalid_value",
    [float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
    ids=["nan", "inf", "negative_inf", "zero", "negative"],
)
def test_model_timeouts_reject_non_finite_and_non_positive_values(
    field_name: str,
    invalid_value: float,
):
    with pytest.raises(ValueError, match="模型 timeout 必须为有限正数"):
        Settings(_env_file=None, **{field_name: invalid_value})


@pytest.mark.parametrize("field_name", MODEL_TIMEOUT_FIELDS)
def test_model_timeouts_accept_positive_finite_values(field_name: str):
    settings = Settings(_env_file=None, **{field_name: 1.5})
    assert getattr(settings, field_name) == 1.5


def test_embedding_throttle_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EMBEDDING_MAX_CONCURRENCY", raising=False)
    monkeypatch.delenv("EMBEDDING_MAX_REQUESTS_PER_MINUTE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.embedding_max_concurrency == 20
    assert settings.embedding_max_requests_per_minute == 0


def test_life_timeout_default_allows_slow_small_models():
    assert Settings(_env_file=None).life_timeout_seconds == 30.0


def test_embedding_throttle_rejects_invalid_values():
    with pytest.raises(ValueError):
        Settings(embedding_max_concurrency=0)

    with pytest.raises(ValueError):
        Settings(embedding_max_requests_per_minute=-1)


def test_all_self_uids_merges_main_and_uids_list():
    """all_self_uids 应当合并 self_uid 主值 + self_uids 列表，去重保序。"""
    settings = Settings(
        self_uid="u_qq_main",
        self_uids=["wxid_me", "u_qq_alt"],
        self_name="Me",
        friend_uid="u_friend",
        friend_name="TA",
    )
    assert settings.all_self_uids == ["u_qq_main", "wxid_me", "u_qq_alt"]


def test_all_self_uids_dedupe_when_main_in_list():
    """主 UID 与列表重复时，按出现顺序保留首次，去重。"""
    settings = Settings(
        self_uid="wxid_me",
        self_uids=["wxid_me", "u_qq_alt"],
        self_name="Me",
        friend_uid="u_friend",
        friend_name="TA",
    )
    assert settings.all_self_uids == ["wxid_me", "u_qq_alt"]


def test_all_self_uids_handles_empty_main():
    """主 UID 留空时也应能正常工作。"""
    settings = Settings(
        self_uid="",
        self_uids=["wxid_me", "u_qq_alt"],
        self_name="Me",
        friend_uid="u_friend",
        friend_name="TA",
    )
    assert settings.all_self_uids == ["wxid_me", "u_qq_alt"]


def test_all_friend_uids_merges_correctly():
    settings = Settings(
        self_uid="u_self",
        self_name="Me",
        friend_uid="u_friend_main",
        friend_uids=["wxid_friend", "u_friend_alt"],
        friend_name="TA",
    )
    assert settings.all_friend_uids == ["u_friend_main", "wxid_friend", "u_friend_alt"]


def test_uids_field_accepts_comma_separated_env():
    """.env 里 `SELF_UIDS=a,b,c` 应当被 _split_aliases validator 切成列表。"""
    settings = Settings(
        self_uid="",
        self_uids="u_a,u_b,u_c",  # 模拟从 .env 传入的字符串
        self_name="Me",
        friend_uid="u_friend",
        friend_name="TA",
    )
    assert settings.self_uids == ["u_a", "u_b", "u_c"]
    assert settings.all_self_uids == ["u_a", "u_b", "u_c"]


def test_self_uid_single_field_accepts_comma_separated():
    """推荐写法：`SELF_UID=a,b,c` 单字段就能填多个 UID，无需 SELF_UIDS。"""
    settings = Settings(
        self_uid="u_qq,wxid_me,u_qq_alt",
        self_name="Me",
        friend_uid="u_friend,wxid_friend",
        friend_name="TA",
    )
    # 单值字段本身保留原始字符串
    assert settings.self_uid == "u_qq,wxid_me,u_qq_alt"
    # 但 all_self_uids 把它按逗号切开
    assert settings.all_self_uids == ["u_qq", "wxid_me", "u_qq_alt"]
    assert settings.all_friend_uids == ["u_friend", "wxid_friend"]


def test_self_uid_and_uids_merge_without_duplicates():
    """SELF_UID 多值 + SELF_UIDS 多值，两边重复的 UID 应去重。"""
    settings = Settings(
        self_uid="u_a,u_b",
        self_uids=["u_b", "u_c"],
        self_name="Me",
        friend_uid="u_friend",
        friend_name="TA",
    )
    assert settings.all_self_uids == ["u_a", "u_b", "u_c"]


def test_require_identity_accepts_uids_list_without_main():
    """只填 SELF_UIDS / FRIEND_UIDS（不填单值）也应通过校验。"""
    settings = Settings(
        self_uid="",
        self_uids=["wxid_me"],
        self_name="Me",
        friend_uid="",
        friend_uids=["wxid_friend"],
        friend_name="TA",
    )
    settings.require_identity()  # 不应抛异常


def test_require_identity_fails_when_both_uid_empty():
    """主值和复数列表都空 → 应报错并提示填 SELF_UID 或 SELF_UIDS。"""
    settings = Settings(
        self_uid="",
        self_uids=[],
        self_name="Me",
        friend_uid="u_friend",
        friend_name="TA",
    )
    with pytest.raises(ConfigError) as exc:
        settings.require_identity()
    assert "SELF_UID" in exc.value.message
