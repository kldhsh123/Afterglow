"""图片本地持久化。

按 SHA-256 命名文件，自然去重。
对外接口：
- `save_data_url(data_url, settings)` → ImageRef（含 sha + 文件后缀 + 完整路径）
- `read_bytes(sha, settings)` → 原始字节
- `data_url_for(sha, settings)` → 重新拼成 data:image/...;base64,... 用于前端展示
- `validate_data_url(data_url, settings)` → 检查大小、mime 合法性，返回解码后的字节
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from xuwen.config import Settings
from xuwen.core.errors import XuwenError
from xuwen.error_i18n import error_text

# data:image/<mime>;base64,<payload>
_DATA_URL_RE = re.compile(r"^data:image/(?P<mime>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$")

_MIME_TO_EXT: dict[str, str] = {
    "png": "png",
    "jpeg": "jpg",
    "jpg": "jpg",
    "gif": "gif",
    "webp": "webp",
    "bmp": "bmp",
}


class ImageError(XuwenError):
    """图片处理失败。"""

    code = "xuwen.image"
    http_status = 400


@dataclass(slots=True, frozen=True)
class ImageRef:
    """一张图片的本地引用。"""

    sha: str
    ext: str
    path: Path
    size: int

    @property
    def filename(self) -> str:
        return f"{self.sha}.{self.ext}"


def validate_data_url(data_url: str, settings: Settings) -> tuple[str, bytes]:
    """校验 data url 合法性，返回 (mime, raw_bytes)。

    - 必须是 data:image/...;base64,... 形式
    - mime 必须在白名单
    - 解码后字节数不能超过 vision_max_image_bytes
    """
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise ImageError(error_text("Images must use data:image/<type>;base64,... format"))
    mime = m.group("mime").lower()
    if mime not in _MIME_TO_EXT:
        raise ImageError(
            error_text("Unsupported image format: {p0}. Supported: png/jpeg/gif/webp/bmp", p0=mime)
        )
    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception as e:
        raise ImageError(error_text("Failed to decode image base64 data")) from e
    if len(raw) > settings.vision_max_image_bytes:
        mb = settings.vision_max_image_bytes / (1024 * 1024)
        raise ImageError(
            error_text("Image too large (>{p0:.1f}MB). Compress it before sending", p0=mb)
        )
    return mime, raw


def save_data_url(data_url: str, settings: Settings) -> ImageRef:
    """把 data url 解析、校验并落盘，返回 ImageRef。"""
    mime, raw = validate_data_url(data_url, settings)
    sha = hashlib.sha256(raw).hexdigest()
    ext = _MIME_TO_EXT[mime]
    settings.image_data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.image_data_dir / f"{sha}.{ext}"
    if not path.exists():
        path.write_bytes(raw)
    return ImageRef(sha=sha, ext=ext, path=path, size=len(raw))


def find_by_sha(sha: str, settings: Settings) -> Path | None:
    """按 sha 找已存在的图片文件（任意支持的扩展名）。"""
    for ext in set(_MIME_TO_EXT.values()):
        candidate = settings.image_data_dir / f"{sha}.{ext}"
        if candidate.exists():
            return candidate
    return None


def read_bytes(sha: str, settings: Settings) -> bytes:
    """按 sha 读取原图字节。"""
    path = find_by_sha(sha, settings)
    if path is None:
        raise ImageError(error_text("Image not found: {p0}", p0=sha))
    return path.read_bytes()


def data_url_for(sha: str, settings: Settings) -> str:
    """把磁盘上的图片重新打包成 data url。"""
    path = find_by_sha(sha, settings)
    if path is None:
        raise ImageError(error_text("Image not found: {p0}", p0=sha))
    ext = path.suffix.lstrip(".").lower()
    mime = "jpeg" if ext == "jpg" else ext
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/{mime};base64,{b64}"
