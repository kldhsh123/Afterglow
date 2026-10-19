"""/v1/documents/extract：上传文档 → 返回纯文本。

前端拿到 `text` 后自行拼接到 user message 里发出去，LLM 完全不用感知 file 概念。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from xuwen.chat_api.document import (
    DocumentError,
    extract,
    supported_extensions,
)
from xuwen.chat_api.state import AppState, get_state
from xuwen.error_i18n import error_text

router = APIRouter(prefix="/v1/documents", tags=["documents"])


class ExtractResponse(BaseModel):
    filename: str
    extension: str
    text: str
    char_count: int
    estimated_tokens: int


class SupportedFormatsResponse(BaseModel):
    extensions: list[str]


@router.get("/formats", response_model=SupportedFormatsResponse)
def list_formats() -> SupportedFormatsResponse:
    """列出当前支持的文档扩展名。"""
    return SupportedFormatsResponse(extensions=supported_extensions())


@router.post("/extract", response_model=ExtractResponse)
async def extract_document(
    file: UploadFile = File(...),
    state: AppState = Depends(get_state),
) -> ExtractResponse:
    """上传文档并提取文本。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail=error_text("Missing filename"))
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=error_text("The file is empty"))
    try:
        doc = extract(data, file.filename)
    except DocumentError as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    return ExtractResponse(
        filename=doc.filename,
        extension=doc.extension,
        text=doc.text,
        char_count=doc.char_count,
        estimated_tokens=doc.estimated_tokens,
    )
