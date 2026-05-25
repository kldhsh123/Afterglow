"""首次配置检测。

判定逻辑：缺少以下任一关键字段就视为"首次配置中"：
- SELF_UID / FRIEND_UID（身份）
- OPENAI_API_KEY（聊天 AI）
- EMBEDDING_API_KEY（向量服务）
- XUWEN_API_KEY（访问鉴权）

只要进入首次模式，就强制启用配置 UI（即使 .env 显式写了 CONFIG_UI_ENABLED=false），
让小白第一次就能进得去。配置完成（关键字段都齐了）后下一次启动自动恢复用户写的值。
"""

from __future__ import annotations

from dataclasses import dataclass

from xuwen.config import Settings


@dataclass(slots=True)
class FirstRunStatus:
    is_first_run: bool
    missing_keys: list[str]

    def describe(self) -> str:
        if not self.missing_keys:
            return ""
        return "、".join(self.missing_keys)


def check_first_run(settings: Settings) -> FirstRunStatus:
    """返回首次配置状态 + 缺失字段列表。

    与 Settings.require_identity() 保持同一组判定（NAME + UID + 关键 API key），
    避免出现"first_run 觉得够了但 import_history 实际跑不动"的不一致。
    """
    missing: list[str] = []

    if not settings.self_name:
        missing.append("SELF_NAME")
    if not settings.all_self_uids:
        missing.append("SELF_UID")
    if not settings.friend_name:
        missing.append("FRIEND_NAME")
    if not settings.all_friend_uids:
        missing.append("FRIEND_UID")
    if not settings.openai_api_key.get_secret_value():
        missing.append("OPENAI_API_KEY")
    if not settings.embedding_api_key.get_secret_value():
        missing.append("EMBEDDING_API_KEY")
    if settings.xuwen_api_key is None or not settings.xuwen_api_key.get_secret_value():
        missing.append("XUWEN_API_KEY")

    return FirstRunStatus(is_first_run=bool(missing), missing_keys=missing)
