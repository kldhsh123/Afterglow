"""/healthz / /readyz / /info 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from xuwen import __version__
from xuwen.chat_api.schemas import (
    AppInfoResponse,
    HealthResponse,
    ReadinessResponse,
    UpdateInfoPayload,
)
from xuwen.chat_api.state import AppState, get_state

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse, tags=["meta"])
def healthz() -> HealthResponse:
    """进程存活探针。任何能进入这里的请求都说明进程在线。"""
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=ReadinessResponse, tags=["meta"])
async def readyz(state: AppState = Depends(get_state)) -> ReadinessResponse:
    """检查 LanceDB 表 / persona 卡片 / LLM-embedding 配置是否齐全。"""
    issues: list[str] = []
    settings = state.settings
    if not settings.openai_api_key.get_secret_value():
        issues.append("OPENAI_API_KEY 未配置")
    if not settings.embedding_api_key.get_secret_value():
        issues.append("EMBEDDING_API_KEY 未配置")
    if not (settings.self_uid and settings.friend_uid):
        issues.append("SELF_UID / FRIEND_UID 未配置")
    try:
        stats = await state.store.stats()
        if stats.friend_messages == 0:
            issues.append(
                "向量库为空，请先用 `python -m xuwen.ingestion.cli import <json>` 导入历史聊天"
            )
    except Exception as e:
        issues.append(f"LanceDB 访问失败：{type(e).__name__}")
    return ReadinessResponse(ready=not issues, issues=issues)


@router.post("/info/check-update", response_model=UpdateInfoPayload, tags=["meta"])
@router.post("/v1/info/check-update", response_model=UpdateInfoPayload, tags=["meta"])
async def check_update(state: AppState = Depends(get_state)) -> UpdateInfoPayload:
    """手动触发一次版本检查，返回最新 snapshot。

    内部 5 秒节流：连续点击只第一次真打 GitHub API。
    UPDATE_CHECK_ENABLED=false 时也允许这个端点（用户主动行为）。
    """
    info = await state.update_checker.force_check_now()
    return UpdateInfoPayload.model_validate(info.to_dict())


@router.get("/info", response_model=AppInfoResponse, tags=["meta"])
@router.get("/v1/info", response_model=AppInfoResponse, tags=["meta"])
def info(state: AppState = Depends(get_state)) -> AppInfoResponse:
    """返回前端需要的应用元数据。"""
    s = state.settings
    has_card = (s.persona_data_dir / "persona_card.md").exists()
    try:
        rel = s.resolved_relationship_description
    except Exception:
        rel = ""
    return AppInfoResponse(
        app_name=s.app_name,
        app_slogan=s.app_slogan,
        friend_name=s.friend_name,
        self_name=s.self_name,
        relationship_type=s.relationship_type,
        relationship_description=rel,
        persona_template=s.persona_template,
        embedding_model=s.embedding_model,
        chat_model=s.chat_model,
        version=__version__,
        has_persona_card=has_card,
        update=UpdateInfoPayload.model_validate(
            state.update_checker.snapshot().to_dict()
        ),
    )
