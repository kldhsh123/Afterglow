"""/images/{sha} 端点：把 .data/images 里的原图按 sha 取出来返回。

让前端能展示历史聊天中带的图片（即使浏览器关闭重开后也能看到）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from xuwen.chat_api.image_store import find_by_sha
from xuwen.chat_api.state import AppState, get_state
from xuwen.error_i18n import error_text

router = APIRouter(tags=["images"])

_MIME_BY_EXT: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


@router.get("/images/{sha}")
def get_image(sha: str, state: AppState = Depends(get_state)) -> Response:
    # sha 必须是 64 字符十六进制（防路径穿越）
    if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha.lower()):
        raise HTTPException(status_code=400, detail=error_text("Invalid sha"))
    path = find_by_sha(sha, state.settings)
    if path is None:
        raise HTTPException(status_code=404, detail=error_text("Image not found"))
    ext = path.suffix.lstrip(".").lower()
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    return Response(
        content=path.read_bytes(),
        media_type=mime,
        headers={"cache-control": "public, max-age=86400"},
    )
