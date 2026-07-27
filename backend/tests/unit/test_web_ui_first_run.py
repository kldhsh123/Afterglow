"""web_ui.first_run 首次模式检测测试。

判定逻辑：缺 SELF_UID / FRIEND_UID / OPENAI_API_KEY / EMBEDDING_API_KEY / XUWEN_API_KEY
任一即视为首次模式。
"""

from __future__ import annotations

from pydantic import SecretStr

from xuwen.config import Settings
from xuwen.web_ui.first_run import check_first_run


def _complete_settings() -> Settings:
    """完整配置：5 个关键字段都齐了。"""
    return Settings(
        _env_file=None,
        self_uid="u_me",
        self_name="me",
        friend_uid="u_friend",
        friend_name="friend",
        openai_api_key=SecretStr("sk-test"),
        embedding_api_key=SecretStr("sk-emb"),
        xuwen_api_key=SecretStr("local-token"),
    )


def test_complete_config_not_first_run() -> None:
    fr = check_first_run(_complete_settings())
    assert fr.is_first_run is False
    assert fr.missing_keys == []


def test_missing_self_uid_triggers_first_run() -> None:
    fr = check_first_run(
        Settings(
            _env_file=None,
            self_name="me",
            friend_name="friend",
            friend_uid="u_friend",
            openai_api_key=SecretStr("sk-test"),
            embedding_api_key=SecretStr("sk-emb"),
            xuwen_api_key=SecretStr("local"),
        )
    )
    assert fr.is_first_run is True
    assert "SELF_UID" in fr.missing_keys


def test_missing_self_name_triggers_first_run() -> None:
    """has UID but no NAME → 主进程 require_identity 会失败，first_run 也要报"""
    fr = check_first_run(
        Settings(
            _env_file=None,
            self_uid="u_me",
            friend_uid="u_friend",
            friend_name="friend",
            openai_api_key=SecretStr("sk-test"),
            embedding_api_key=SecretStr("sk-emb"),
            xuwen_api_key=SecretStr("local"),
        )
    )
    assert fr.is_first_run is True
    assert "SELF_NAME" in fr.missing_keys


def test_missing_friend_name_triggers_first_run() -> None:
    fr = check_first_run(
        Settings(
            _env_file=None,
            self_name="me",
            self_uid="u_me",
            friend_uid="u_friend",
            openai_api_key=SecretStr("sk-test"),
            embedding_api_key=SecretStr("sk-emb"),
            xuwen_api_key=SecretStr("local"),
        )
    )
    assert fr.is_first_run is True
    assert "FRIEND_NAME" in fr.missing_keys


def test_missing_openai_key_triggers_first_run() -> None:
    fr = check_first_run(
        Settings(
            _env_file=None,
            self_name="me",
            self_uid="u_me",
            friend_name="friend",
            friend_uid="u_friend",
            embedding_api_key=SecretStr("sk-emb"),
            xuwen_api_key=SecretStr("local"),
        )
    )
    assert fr.is_first_run is True
    assert "OPENAI_API_KEY" in fr.missing_keys


def test_missing_xuwen_api_key_triggers_first_run() -> None:
    fr = check_first_run(
        Settings(
            _env_file=None,
            self_name="me",
            self_uid="u_me",
            friend_name="friend",
            friend_uid="u_friend",
            openai_api_key=SecretStr("sk-test"),
            embedding_api_key=SecretStr("sk-emb"),
        )
    )
    assert fr.is_first_run is True
    assert "XUWEN_API_KEY" in fr.missing_keys


def test_empty_xuwen_api_key_counts_as_missing() -> None:
    """xuwen_api_key 是 SecretStr("") 或 None 都要视为缺失。"""
    fr = check_first_run(
        Settings(
            _env_file=None,
            self_name="me",
            self_uid="u_me",
            friend_name="friend",
            friend_uid="u_friend",
            openai_api_key=SecretStr("sk-test"),
            embedding_api_key=SecretStr("sk-emb"),
            xuwen_api_key=SecretStr(""),
        )
    )
    assert fr.is_first_run is True
    assert "XUWEN_API_KEY" in fr.missing_keys


def test_describe_returns_chinese_joined_string() -> None:
    fr = check_first_run(
        Settings(_env_file=None)  # 全空
    )
    description = fr.describe()
    assert "SELF_UID" in description
    assert "、" in description  # 中文顿号分隔


def test_describe_empty_when_complete() -> None:
    fr = check_first_run(_complete_settings())
    assert fr.describe() == ""


def test_multi_uid_via_self_uids_list_also_counts() -> None:
    """SELF_UIDS 列表填了即使 SELF_UID 单值为空也算齐。"""
    fr = check_first_run(
        Settings(
            _env_file=None,
            self_name="me",
            self_uids=["u_a", "u_b"],
            friend_name="friend",
            friend_uid="u_friend",
            openai_api_key=SecretStr("sk"),
            embedding_api_key=SecretStr("sk"),
            xuwen_api_key=SecretStr("k"),
        )
    )
    assert fr.is_first_run is False


def test_config_ui_path_prefix_rejects_root() -> None:
    """根 / 或空前缀会让主鉴权被绕过，必须在 Settings 构造时拒绝。"""
    import pytest
    from pydantic import ValidationError

    for bad in ("/", "", "   "):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, config_ui_path_prefix=bad)


def test_config_ui_path_prefix_requires_leading_slash() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, config_ui_path_prefix="config")


def test_config_ui_path_prefix_strips_trailing_slash() -> None:
    s = Settings(_env_file=None, config_ui_path_prefix="/admin/")
    assert s.config_ui_path_prefix == "/admin"


def test_config_ui_path_prefix_rejects_reserved_exact() -> None:
    """精确匹配业务前缀必须拒绝。"""
    import pytest
    from pydantic import ValidationError

    for bad in ("/v1", "/memory", "/healthz", "/debug", "/images"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, config_ui_path_prefix=bad)


def test_config_ui_path_prefix_rejects_reserved_subpath() -> None:
    """子路径同样要拒：/v1/chat 仍会让 startswith('/v1/chat/') 放行 /v1/chat/completions。"""
    import pytest
    from pydantic import ValidationError

    for bad in ("/v1/chat", "/v1/chat/completions", "/memory/stats", "/debug/foo"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, config_ui_path_prefix=bad)


def test_config_ui_path_prefix_accepts_non_reserved() -> None:
    """跟 reserved 无关的前缀都可以。"""
    for ok in ("/config", "/admin", "/setup-ui", "/internal/config"):
        s = Settings(_env_file=None, config_ui_path_prefix=ok)
        assert s.config_ui_path_prefix == ok.rstrip("/")
