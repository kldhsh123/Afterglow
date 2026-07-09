"""历史聊天图片离线导入。

`import-images <export-dir>` 在普通文本导入完成后手动运行：
- 从导出目录里的 JSON 提取图片引用；
- 在 resources/images 中查找原图；
- 按 sha256 去重保存到 .data/images；
- 调用 VLM 生成摘要，并写入 history_images 向量表。
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xuwen.chat_api.vision_client import VisionClient
from xuwen.config import Settings
from xuwen.core.errors import IngestionError, ParseError
from xuwen.core.models import HistoryImageChunk, NormalizedMessage, Session
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.ingestion.parser import load_qq_json, parse_messages
from xuwen.ingestion.splitter import split_sessions
from xuwen.memory.schema import TABLE_HISTORY_IMAGES
from xuwen.memory.store import MemoryStore

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_PROMPT_VERSION = "v1"


@dataclass(slots=True, frozen=True)
class ImageImportReport:
    total_refs: int
    matched_files: int
    missing_files: int
    unique_images: int
    described_images: int
    reused_descriptions: int
    skipped_existing_rows: int
    upserted_rows: int


@dataclass(slots=True, frozen=True)
class _ImageRef:
    message_id: str
    image_name: str


@dataclass(slots=True, frozen=True)
class _ResolvedImageRef:
    message: NormalizedMessage
    session_id: str
    image_name: str
    path: Path
    sha: str
    ext: str
    mime: str
    size: int
    context_before: str
    context_after: str


async def import_history_images(
    export_dir: str | Path,
    settings: Settings,
    *,
    store: MemoryStore | None = None,
    embedder: EmbeddingClient | None = None,
    vision_client: VisionClient | None = None,
    plugin_name: str | None = None,
) -> ImageImportReport:
    settings.require_identity()
    if not settings.vision_enabled:
        raise IngestionError("未启用视觉理解。请设置 VISION_ENABLED=true。")
    if not settings.vision_api_url or not settings.vision_api_key.get_secret_value():
        raise IngestionError("请配置 VISION_API_URL / VISION_API_KEY 后再导入图片。")

    root = Path(export_dir)
    json_path = _find_single_json(root)
    images_dir = root / "resources" / "images"
    if not images_dir.is_dir():
        raise IngestionError(f"找不到图片目录：{images_dir}")

    payload = load_qq_json(json_path)
    parsed = parse_messages(payload, settings, plugin_name=plugin_name)
    cleaned = Cleaner(settings).clean_many(parsed)
    sessions = split_sessions(cleaned, settings)
    msg_index, session_index = _index_messages(sessions)
    image_refs = _extract_image_refs(payload)

    resolved: list[_ResolvedImageRef] = []
    missing = 0
    for ref in image_refs:
        msg = msg_index.get(ref.message_id)
        if msg is None:
            continue
        path = _find_image_file(images_dir, ref.image_name)
        if path is None:
            missing += 1
            continue
        raw = path.read_bytes()
        if len(raw) > settings.vision_max_image_bytes:
            missing += 1
            continue
        sha = hashlib.sha256(raw).hexdigest()
        ext = _image_ext(path)
        mime = _mime_for_ext(ext)
        _copy_to_image_cache(settings, sha=sha, ext=ext, raw=raw)
        before, after = _context_for_message(session_index[msg.message_id], msg.message_id, settings)
        resolved.append(
            _ResolvedImageRef(
                message=msg,
                session_id=session_index[msg.message_id].session_id,
                image_name=Path(ref.image_name).name,
                path=path,
                sha=sha,
                ext=ext,
                mime=mime,
                size=len(raw),
                context_before=before,
                context_after=after,
            )
        )

    if store is None:
        store = MemoryStore(settings)
        await store.connect()
        store.ensure_tables()

    owns_embedder = embedder is None
    if embedder is None:
        embedder = EmbeddingClient(settings)

    owns_vision = vision_client is None
    if vision_client is None:
        vision_client = VisionClient(settings)

    try:
        chunk_ids = [f"image-{r.message.message_id}-{r.sha[:16]}" for r in resolved]
        existing = await store.existing_ids(TABLE_HISTORY_IMAGES, chunk_ids)
        pending = [r for r, cid in zip(resolved, chunk_ids, strict=False) if cid not in existing]

        descriptions = await _describe_unique_images(
            pending,
            settings,
            store,
            vision_client,
        )
        chunks = [
            _build_chunk(r, descriptions[r.sha], settings)
            for r in pending
            if descriptions.get(r.sha)
        ]
        vectors = await embedder.embed_texts([c.description for c in chunks]) if chunks else []
        embeddings = {c.chunk_id: vectors[i] for i, c in enumerate(chunks)}
        upserted = await store.upsert_history_image_chunks(chunks, embeddings)
    finally:
        if owns_vision:
            await vision_client.aclose()
        if owns_embedder:
            await embedder.aclose()
        # store 与普通 importer 保持一致，不主动关闭。

    unique_shas = {r.sha for r in resolved}
    reused = sum(1 for r in pending if r.sha in descriptions) - len({
        r.sha for r in pending if r.sha in descriptions
    })
    return ImageImportReport(
        total_refs=len(image_refs),
        matched_files=len(resolved),
        missing_files=missing,
        unique_images=len(unique_shas),
        described_images=len({r.sha for r in pending if r.sha in descriptions}),
        reused_descriptions=max(0, reused),
        skipped_existing_rows=len(existing),
        upserted_rows=upserted,
    )


def _find_single_json(root: Path) -> Path:
    if not root.is_dir():
        raise IngestionError(f"不是目录：{root}")
    json_files = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".json")
    if len(json_files) != 1:
        raise IngestionError("图片导入目录必须包含且只包含一个 JSON 文件")
    return json_files[0]


def _extract_image_refs(payload: dict[str, Any]) -> list[_ImageRef]:
    if _looks_like_afterglow(payload):
        return _extract_afterglow_refs(payload)
    if _looks_like_qq(payload):
        return _extract_qq_refs(payload)
    raise ParseError("import-images 目前支持 Afterglow v1 和 QQChatExporter V5 图片引用")


def _looks_like_afterglow(payload: dict[str, Any]) -> bool:
    afterglow = payload.get("afterglow")
    return isinstance(afterglow, dict) and afterglow.get("format") == "afterglow-chat"


def _looks_like_qq(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("chatInfo"), dict) or isinstance(payload.get("metadata"), dict)


def _extract_afterglow_refs(payload: dict[str, Any]) -> list[_ImageRef]:
    refs: list[_ImageRef] = []
    for raw in payload.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        message_id = str(raw.get("id") or raw.get("message_id") or "")
        for item in raw.get("attachments") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").lower() != "image":
                continue
            name = _safe_name(str(item.get("name") or item.get("filename") or ""))
            if message_id and name:
                refs.append(_ImageRef(message_id=message_id, image_name=name))
    return refs


def _extract_qq_refs(payload: dict[str, Any]) -> list[_ImageRef]:
    refs: list[_ImageRef] = []
    for raw in payload.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        message_id = str(raw.get("id") or "")
        content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        candidates: list[str] = []
        for resource in content.get("resources") or []:
            if isinstance(resource, dict) and str(resource.get("type") or "").lower() == "image":
                candidates.append(_first_resource_name(resource))
        for element in content.get("elements") or []:
            if not isinstance(element, dict) or str(element.get("type") or "").lower() != "image":
                continue
            data = element.get("data") if isinstance(element.get("data"), dict) else {}
            candidates.append(_first_resource_name(data))
        for value in candidates:
            name = _safe_name(value)
            if message_id and name:
                refs.append(_ImageRef(message_id=message_id, image_name=name))
    # 同一条 QQ 消息 resources/elements 可能重复描述同一张图。
    seen: set[tuple[str, str]] = set()
    out: list[_ImageRef] = []
    for ref in refs:
        key = (ref.message_id, ref.image_name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _first_resource_name(raw: dict[str, Any]) -> str:
    for key in ("filename", "url", "localPath"):
        value = str(raw.get(key) or "")
        if value:
            return value
    return ""


def _safe_name(value: str) -> str:
    if not value:
        return ""
    name = Path(value.replace("\\", "/")).name
    return name if name not in {"", ".", ".."} else ""


def _find_image_file(images_dir: Path, image_name: str) -> Path | None:
    name = _safe_name(image_name)
    if not name:
        return None
    exact = images_dir / name
    if exact.is_file() and exact.suffix.lower() in _IMAGE_EXTS:
        return exact
    lowered = name.lower()
    candidates = [p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXTS]
    for p in candidates:
        if p.name.lower() == lowered:
            return p
    for p in candidates:
        if p.name.lower().endswith(f"_{lowered}"):
            return p
    return None


def _index_messages(sessions: list[Session]) -> tuple[dict[str, NormalizedMessage], dict[str, Session]]:
    msg_index: dict[str, NormalizedMessage] = {}
    session_index: dict[str, Session] = {}
    for session in sessions:
        for msg in session.messages:
            msg_index[msg.message_id] = msg
            session_index[msg.message_id] = session
    return msg_index, session_index


def _context_for_message(
    session: Session,
    message_id: str,
    settings: Settings,
) -> tuple[str, str]:
    messages = session.messages
    idx = next((i for i, m in enumerate(messages) if m.message_id == message_id), -1)
    if idx < 0:
        return "", ""
    before = messages[max(0, idx - settings.single_context_before) : idx]
    after = messages[idx + 1 : idx + 1 + settings.single_context_after]
    return _render_dialogue(before, settings), _render_dialogue(after, settings)


def _render_dialogue(messages: list[NormalizedMessage], settings: Settings) -> str:
    lines: list[str] = []
    for msg in messages:
        text = msg.text.strip()
        if not text:
            continue
        if msg.sender_role == "self":
            speaker = settings.self_name or "我"
        elif msg.sender_role == "friend":
            speaker = settings.friend_name or "TA"
        elif msg.sender_role == "system":
            speaker = "系统"
        else:
            speaker = msg.sender_name or "其他人"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


async def _describe_unique_images(
    refs: list[_ResolvedImageRef],
    settings: Settings,
    store: MemoryStore,
    vision_client: VisionClient,
) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    unique: dict[str, _ResolvedImageRef] = {}
    for ref in refs:
        unique.setdefault(ref.sha, ref)

    for sha, ref in unique.items():
        existing_rows = await store.list_history_images_by_sha(sha, limit=1)
        existing = next(
            (str(r.get("description") or "") for r in existing_rows if r.get("description")),
            "",
        )
        if existing:
            descriptions[sha] = existing
            continue
        data_url = _data_url_for_file(ref.path, ref.mime, settings)
        descriptions[sha] = (await vision_client.describe_images([data_url]))[0]
    return descriptions


def _data_url_for_file(path: Path, mime: str, settings: Settings) -> str:
    raw = path.read_bytes()
    if len(raw) > settings.vision_max_image_bytes:
        raise IngestionError(f"图片过大：{path.name}")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _copy_to_image_cache(settings: Settings, *, sha: str, ext: str, raw: bytes) -> None:
    settings.image_data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.image_data_dir / f"{sha}.{ext}"
    if not path.exists():
        path.write_bytes(raw)


def _image_ext(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext == "jpeg":
        return "jpg"
    return ext


def _mime_for_ext(ext: str) -> str:
    if ext == "jpg":
        return "image/jpeg"
    guessed = mimetypes.types_map.get(f".{ext}")
    return guessed or f"image/{ext}"


def _build_chunk(
    ref: _ResolvedImageRef,
    description: str,
    settings: Settings,
) -> HistoryImageChunk:
    msg = ref.message
    return HistoryImageChunk(
        chunk_id=f"image-{msg.message_id}-{ref.sha[:16]}",
        message_id=msg.message_id,
        session_id=ref.session_id,
        seq=msg.seq,
        timestamp_ms=msg.timestamp_ms,
        sender_uid=msg.sender_uid,
        sender_name=msg.sender_name,
        sender_role=msg.sender_role,
        image_sha=ref.sha,
        image_name=ref.image_name,
        mime=ref.mime,
        size=ref.size,
        description=description,
        context_before=ref.context_before,
        context_after=ref.context_after,
        vision_model=settings.vision_model,
        vision_prompt_version=_PROMPT_VERSION,
        tags=["image"],
    )
