"""表情包持久化（基于 JSON 索引 + 文件系统）。

为何不放进 LanceDB？
- 表情包数量典型 10-100 张，无需向量检索
- 用 JSON 索引足够简单、易备份、易手工编辑
- 图片本身按 SHA-256 命名落盘到 sticker_data_dir，与历史聊天图片同源思路

存储布局：
    <sticker_data_dir>/
        index.json               # 列表 + 元数据
        <sha>.<ext>              # 图片文件
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from xuwen.config import Settings
from xuwen.core.errors import XuwenError

Owner = Literal["ai", "self", "shared"]

# 表情包名字允许的字符（中文 / 英数 / 下划线 / 中划线）
_NAME_RE = re.compile(r"^[\w一-鿿\-]{1,32}$")
# 图片 data URL
_DATA_URL_RE = re.compile(r"^data:image/(?P<mime>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$")
_MIME_TO_EXT: dict[str, str] = {
    "png": "png", "jpeg": "jpg", "jpg": "jpg",
    "gif": "gif", "webp": "webp", "bmp": "bmp",
}


class StickerError(XuwenError):
    code = "xuwen.sticker"
    http_status = 400


@dataclass(slots=True)
class Sticker:
    """单张表情包。"""

    name: str                        # 唯一标识，AI 用 [sticker:name] 调用
    description: str                 # 给 AI 看的语义描述，如"开心打趣"
    sha: str
    extension: str
    owner: Owner = "shared"          # ai 只 AI 用 / self 只用户用 / shared 共用
    tags: list[str] = field(default_factory=list)
    created_at_ms: int = 0

    def filename(self) -> str:
        return f"{self.sha}.{self.extension}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StickerStore:
    """表情包持久化与查询。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._dir = settings.sticker_data_dir
        self._index_path = self._dir / "index.json"
        self._cache: dict[str, Sticker] | None = None

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Sticker]:
        if self._cache is not None:
            return self._cache
        self._dir.mkdir(parents=True, exist_ok=True)
        if not self._index_path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StickerError(f"表情包索引损坏：{e}") from e
        if not isinstance(raw, list):
            self._cache = {}
            return self._cache
        self._cache = {}
        for entry in raw:
            try:
                s = Sticker(**entry)
                self._cache[s.name] = s
            except (TypeError, ValueError):
                continue
        return self._cache

    def _save(self) -> None:
        data = [s.to_dict() for s in self._load().values()]
        self._index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_all(self, owner: Owner | None = None) -> list[Sticker]:
        items = list(self._load().values())
        if owner is not None:
            items = [s for s in items if s.owner == owner or s.owner == "shared"]
        items.sort(key=lambda s: s.created_at_ms)
        return items

    def get(self, name: str) -> Sticker | None:
        return self._load().get(name)

    def add(
        self,
        *,
        name: str,
        description: str,
        data_url: str,
        owner: Owner = "shared",
        tags: list[str] | None = None,
    ) -> Sticker:
        from xuwen.core.time import now_ms

        if not _NAME_RE.match(name):
            raise StickerError(
                "name 仅允许中文 / 英数 / 下划线 / 中划线，长度 1-32",
            )
        if not description.strip():
            raise StickerError("必须给表情包写一句说明（AI 用它判断什么时候发）")

        mime, raw = _decode_data_url(data_url)
        if len(raw) > self.settings.sticker_max_image_bytes:
            mb = self.settings.sticker_max_image_bytes / (1024 * 1024)
            raise StickerError(f"表情包过大（>{mb:.1f}MB），请先压缩")

        sha = hashlib.sha256(raw).hexdigest()
        ext = _MIME_TO_EXT[mime]
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{sha}.{ext}"
        if not path.exists():
            path.write_bytes(raw)

        sticker = Sticker(
            name=name,
            description=description.strip(),
            sha=sha,
            extension=ext,
            owner=owner,
            tags=list(tags or []),
            created_at_ms=now_ms(),
        )
        cache = self._load()
        cache[name] = sticker
        self._save()
        return sticker

    def update(
        self,
        name: str,
        *,
        description: str | None = None,
        owner: Owner | None = None,
        tags: list[str] | None = None,
    ) -> Sticker:
        cache = self._load()
        sticker = cache.get(name)
        if sticker is None:
            raise StickerError(f"找不到表情包：{name}")
        if description is not None:
            if not description.strip():
                raise StickerError("description 不能为空")
            sticker.description = description.strip()
        if owner is not None:
            sticker.owner = owner
        if tags is not None:
            sticker.tags = list(tags)
        self._save()
        return sticker

    def delete(self, name: str) -> bool:
        cache = self._load()
        sticker = cache.pop(name, None)
        if sticker is None:
            return False
        # 文件不删，留作历史聊天溯源（其它表情/历史消息可能引用同一 sha）
        self._save()
        return True

    def image_path(self, name: str) -> Path | None:
        sticker = self.get(name)
        if sticker is None:
            return None
        path = self._dir / sticker.filename()
        return path if path.exists() else None

    def available_for_ai(self) -> list[Sticker]:
        """AI 可用的表情包：owner=ai 或 shared，限数量。"""
        cache = self._load()
        items = [s for s in cache.values() if s.owner in ("ai", "shared")]
        items.sort(key=lambda s: (-len(s.tags), s.created_at_ms))
        return items[: self.settings.sticker_max_for_ai]


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise StickerError("仅支持 data:image/<type>;base64,... 形式")
    mime = m.group("mime").lower()
    if mime not in _MIME_TO_EXT:
        raise StickerError(f"不支持的图片格式：{mime}")
    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception as e:
        raise StickerError("图片 base64 解码失败") from e
    return mime, raw


# AI 在文本里用这种占位调用：
#   [sticker:嘿嘿] 或 [sticker:name=嘿嘿]
# 解析出 name；同时支持忽略大小写的英数
STICKER_TOKEN_RE = re.compile(
    r"\[sticker(?::|=)([^\]\s]+)\]",
    re.IGNORECASE,
)


def find_sticker_tokens(text: str) -> list[tuple[int, int, str]]:
    """扫描文本中所有 [sticker:xxx] 占位。

    返回 [(start, end, name), ...]，让上层可做替换。
    """
    out: list[tuple[int, int, str]] = []
    for m in STICKER_TOKEN_RE.finditer(text):
        out.append((m.start(), m.end(), m.group(1)))
    return out


def render_sticker_block_for_prompt(stickers: list[Sticker]) -> str:
    """把表情包列表渲染成 system prompt 的一段说明。"""
    if not stickers:
        return (
            "【表情包】当前没有可用的表情包；**绝对不要**输出 `[sticker:xxx]` 形式的占位，"
            "用文字 / emoji 表达即可（否则会出现一段乱码）。"
        )
    names = "、".join(f"[sticker:{s.name}]" for s in stickers)
    lines = [
        "【表情包】只能从下面这份列表里挑，**不要自创**也不要修改名字（少一个字 / 多一个字都会让前端渲染失败）：",
    ]
    for s in stickers:
        lines.append(f"- [sticker:{s.name}]：{s.description}")
    lines.append(
        f"以上是当前**全部**可用名字：{names}。"
        "用名字一字不差地写出来，否则不要输出 `[sticker:...]` 这种格式——用文字代替。"
    )
    lines.append("挑选要贴合语境的；不要为了发而发，文字回应优先。")
    return "\n".join(lines)
