"""/v1/stickers/* 表情包管理。

- GET    /v1/stickers              列出（可按 owner 过滤）
- POST   /v1/stickers              新建（data URL）
- PATCH  /v1/stickers/{name}       更新元数据
- DELETE /v1/stickers/{name}       删除
- GET    /v1/stickers/{name}/image 取图片字节
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from xuwen.chat_api.state import AppState, get_state
from xuwen.chat_api.sticker_store import (
    Owner,
    Sticker,
    StickerError,
    StickerStore,
)
from xuwen.error_i18n import error_text

router = APIRouter(prefix="/v1/stickers", tags=["stickers"])


_MIME_BY_EXT: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


class StickerResponse(BaseModel):
    name: str
    description: str
    owner: str
    tags: list[str]
    extension: str
    sha: str
    created_at_ms: int
    image_url: str  # 前端用，相对路径


class StickerListResponse(BaseModel):
    items: list[StickerResponse]


class CreateStickerRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    description: str = Field(..., min_length=1, max_length=200)
    data_url: str = Field(..., description="data:image/...;base64,...")
    owner: Owner = "shared"
    tags: list[str] = Field(default_factory=list)


class UpdateStickerRequest(BaseModel):
    description: str | None = None
    owner: Owner | None = None
    tags: list[str] | None = None


def _to_response(s: Sticker, base_path: str = "/v1/stickers") -> StickerResponse:
    return StickerResponse(
        name=s.name,
        description=s.description,
        owner=s.owner,
        tags=list(s.tags),
        extension=s.extension,
        sha=s.sha,
        created_at_ms=s.created_at_ms,
        image_url=f"{base_path}/{s.name}/image",
    )


def _get_store(state: AppState) -> StickerStore:
    return StickerStore(state.settings)


@router.get("", response_model=StickerListResponse)
def list_stickers(
    owner: Owner | None = None,
    state: AppState = Depends(get_state),
) -> StickerListResponse:
    store = _get_store(state)
    items = store.list_all(owner=owner)
    return StickerListResponse(items=[_to_response(s) for s in items])


@router.post("", response_model=StickerResponse, status_code=201)
def create_sticker(
    req: CreateStickerRequest,
    state: AppState = Depends(get_state),
) -> StickerResponse:
    store = _get_store(state)
    if store.get(req.name) is not None:
        raise HTTPException(
            status_code=409, detail=error_text("Sticker {p0!r} already exists", p0=req.name)
        )
    try:
        sticker = store.add(
            name=req.name,
            description=req.description,
            data_url=req.data_url,
            owner=req.owner,
            tags=req.tags,
        )
    except StickerError as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    return _to_response(sticker)


@router.patch("/{name}", response_model=StickerResponse)
def update_sticker(
    name: str,
    req: UpdateStickerRequest,
    state: AppState = Depends(get_state),
) -> StickerResponse:
    store = _get_store(state)
    try:
        sticker = store.update(
            name,
            description=req.description,
            owner=req.owner,
            tags=req.tags,
        )
    except StickerError as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    return _to_response(sticker)


@router.delete("/{name}")
def delete_sticker(name: str, state: AppState = Depends(get_state)) -> dict[str, str]:
    store = _get_store(state)
    ok = store.delete(name)
    if not ok:
        raise HTTPException(status_code=404, detail=error_text("Sticker not found"))
    return {"status": "deleted", "name": name}


@router.get("/{name}/image")
def get_sticker_image(name: str, state: AppState = Depends(get_state)) -> Response:
    store = _get_store(state)
    path = store.image_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail=error_text("Sticker not found"))
    ext = path.suffix.lstrip(".").lower()
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    return Response(
        content=path.read_bytes(),
        media_type=mime,
        headers={"cache-control": "public, max-age=86400"},
    )
