"""pytest 共享 fixtures。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from xuwen.config import Settings, get_settings

# 项目根目录：backend/ 上一层，即仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_JSON = FIXTURES_DIR / "sample_chat.json"

# 缩减示例（来自用户）—— 末尾可能截断，仅作可选集成测试用
USER_SAMPLE_JSON = REPO_ROOT / "开发缓存" / "缩减示例.json"


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """阻止测试读取开发者 backend/.env。

    pydantic-settings 默认从 cwd 加载 `.env`；若开发者已经在 backend/.env
    里写了 XUWEN_API_KEY 等真实配置，测试 fixture 构造的 Settings 会被这些
    真实值覆盖，导致鉴权 / 模型行为不可预期。这里 autouse 屏蔽掉。

    实现：临时把 cwd 切到 pytest tmp_path（里面没有 .env），并清掉可能
    从外部环境继承的相关变量。
    """
    monkeypatch.chdir(tmp_path)
    # 清掉可能从外部环境继承的相关变量
    for key in list(os.environ.keys()):
        upper = key.upper()
        if upper.startswith(
            (
                "XUWEN_",
                "OPENAI_",
                "EMBEDDING_",
                "LIFE_",
                "UPDATE_",
                "RESPONSE_POLICY_",
                "SILENCE_",
                "VISION_",
                "LABEL_",
                "WEB_",
                "WRITEBACK_",
                "API_AUTH_",
                "APP_",
                "PERSONA_",
                "LANCE_",
                "RELATIONSHIP_",
                "SELF_",
                "FRIEND_",
                "CHAT_MODEL",
                "SESSION_GAP_",
                "WINDOW_",
                "CHUNKING_",
                "ADAPTIVE_CHUNK_",
                "QUERY_REWRITE_",
                "RERANK_",
                "RECENCY_",
                "DEBUG_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    # 测试默认关闭版本更新检查，避免每个集成测试都打 GitHub API（慢 + rate-limit + IP 泄漏）。
    # 需要测 update_check 行为的测试可以在自己的 fixture 里显式 update_check_enabled=True。
    monkeypatch.setenv("UPDATE_CHECK_ENABLED", "false")
    # 清掉 lru_cache，避免 module 级缓存的 settings 漏进来
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def sample_payload() -> dict[str, Any]:
    """加载随仓库附带的固定 sample_chat.json。"""
    with SAMPLE_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def settings_for_sample() -> Settings:
    """匹配 sample_chat.json 的 settings。"""
    return Settings(
        self_uid="uid-self-001",
        self_name="Me",
        friend_uid="uid-friend-001",
        friend_name="TestFriend",
        relationship_type="friend",
    )
