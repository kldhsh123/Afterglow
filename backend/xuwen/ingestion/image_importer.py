"""历史聊天图片离线导入。

`import-images <export-dir>` 在普通文本导入完成后手动运行：
- 从导出目录里的 JSON / JSONL chunks 提取图片引用；
- 在整个导出目录中按相对路径或文件名查找原图；
- 按 sha256 去重保存到 .data/images；
- 调用 VLM 生成摘要，并写入 history_images 向量表。
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xuwen.chat_api.vision_client import VisionClient
from xuwen.config import Settings
from xuwen.core.errors import IngestionError, ParseError
from xuwen.core.models import HistoryImageChunk, NormalizedMessage, Session
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.ingestion.parser import detect_plugin, load_qq_json
from xuwen.ingestion.plugins import (
    ImageReferenceImportPlugin,
    ImportImageRef,
    jsonl_records,
    select_plugin,
)
from xuwen.ingestion.splitter import split_sessions
from xuwen.memory.schema import TABLE_HISTORY_IMAGES
from xuwen.memory.store import MemoryStore

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_PROMPT_VERSION = "v1"
_FAILED_DESCRIPTION_PREFIXES = (
    "[图片：识别失败",
    "[图片：识别超时",
    "[图片：无描述",
)
_EXISTING_SHA_REUSE_LIMIT = 5000


@dataclass(slots=True, frozen=True)
class ImageImportReport:
    total_refs: int
    matched_files: int
    missing_files: int
    unique_images: int
    described_images: int
    reused_descriptions: int
    skipped_failed_descriptions: int
    skipped_existing_rows: int
    upserted_rows: int


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
    """
    Import image references from chat history, generate descriptions, and store searchable image records.
    
    Parameters:
        export_dir (str | Path): Directory containing chat exports and image files.
        settings (Settings): Import, vision, and storage configuration.
        store (MemoryStore | None): Optional persistence store.
        embedder (EmbeddingClient | None): Optional embedding client.
        vision_client (VisionClient | None): Optional vision client.
        plugin_name (str | None): Optional preferred ingestion plugin.
    
    Returns:
        ImageImportReport: Counts for discovered references, matched files, descriptions, reused records, and upserted rows.
    
    Raises:
        IngestionError: If the export data is invalid, vision processing is disabled or unconfigured, or an image exceeds the configured size limit.
    """
    settings.require_identity()
    if not settings.vision_enabled:
        raise IngestionError("未启用视觉理解。请设置 VISION_ENABLED=true。")
    if not settings.vision_api_url or not settings.vision_api_key.get_secret_value():
        raise IngestionError("请配置 VISION_API_URL / VISION_API_KEY 后再导入图片。")

    root = Path(export_dir)
    payloads = _load_chat_payloads(root)
    parsed: list[NormalizedMessage] = []
    image_refs: list[ImportImageRef] = []
    for payload in payloads:
        plugin = select_plugin(payload, preferred=plugin_name)
        parsed.extend(plugin.parse(payload, settings))
        if isinstance(plugin, ImageReferenceImportPlugin):
            image_refs.extend(plugin.extract_image_refs(payload))
    parsed.sort(key=lambda message: (message.timestamp_ms, message.seq))
    cleaned = Cleaner(settings).clean_many(parsed)
    sessions = split_sessions(cleaned, settings)
    msg_index, session_index = _index_messages(sessions)
    image_files = _index_image_files(root)

    resolved: list[_ResolvedImageRef] = []
    missing = 0
    for ref in image_refs:
        msg = msg_index.get(ref.message_id)
        if msg is None:
            continue
        path = _find_image_file(root, image_files, ref.image_name)
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
                image_name=_safe_name(ref.image_name),
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
        existing_rows = await store.list_history_images_by_ids(chunk_ids)
        reusable_existing = await _reusable_existing_image_ids(
            store,
            existing_rows,
        )
        pending = [
            r
            for r, cid in zip(resolved, chunk_ids, strict=False)
            if cid not in reusable_existing
        ]

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
        skipped_failed_descriptions=sum(1 for r in pending if r.sha not in descriptions),
        skipped_existing_rows=len(reusable_existing),
        upserted_rows=upserted,
    )


def _load_chat_payloads(root: Path) -> list[dict[str, Any]]:
    """
    Load recognizable chat payloads from JSON, JSONL, and NDJSON files under a directory.
    
    Parameters:
    	root (Path): Directory to search recursively for chat export files.
    
    Returns:
    	list[dict[str, Any]]: Chat payloads that contain message records and match an ingestion plugin.
    
    Raises:
    	IngestionError: If the path is not a directory, JSONL or NDJSON files cannot be parsed, or no recognizable chat payloads are found.
    """
    if not root.is_dir():
        raise IngestionError(f"不是目录：{root}")
    candidates = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl", ".ndjson"}
    )
    payloads: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in candidates:
        try:
            payload = load_qq_json(path)
        except ParseError as e:
            if path.suffix.lower() in {".jsonl", ".ndjson"}:
                errors.append(f"{path.relative_to(root)}：{e}")
            continue
        has_message_records = (
            jsonl_records(payload) is not None
            or isinstance(payload.get("messages"), list)
        )
        if has_message_records and detect_plugin(payload) is not None:
            payloads.append(payload)
    if errors:
        raise IngestionError("目录中存在无法解析的 JSONL：" + "；".join(errors[:5]))
    if not payloads:
        raise IngestionError("图片导入目录中没有可识别的聊天 JSON / JSONL")
    return payloads


def _safe_name(value: str) -> str:
    """
    Extract a safe filename component from a path-like value.
    
    Parameters:
        value (str): Path-like value to normalize.
    
    Returns:
        str: The final path component, or an empty string if no valid name exists.
    """
    if not value:
        return ""
    name = Path(value.replace("\\", "/")).name
    return name if name not in {"", ".", ".."} else ""


def _index_image_files(root: Path) -> dict[str, list[Path]]:
    """
    Build an index of supported image files under a directory.
    
    Parameters:
        root (Path): Directory to search recursively.
    
    Returns:
        dict[str, list[Path]]: Mapping of lowercase filenames to matching file paths.
    """
    index: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTS:
            index.setdefault(path.name.lower(), []).append(path)
    return index


def _find_image_file(
    root: Path,
    image_files: dict[str, list[Path]],
    image_name: str,
) -> Path | None:
    """
    Find an image file by relative path or indexed filename.
    
    Parameters:
        root (Path): Root directory used to resolve relative image paths.
        image_files (dict[str, list[Path]]): Indexed image files keyed by lowercase filename.
        image_name (str): Image path or filename to locate.
    
    Returns:
        Path | None: The matching image path, or None if no supported image file is found.
    """
    relative = image_name.replace("\\", "/").strip().lstrip("/")
    if relative:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            candidate = root
        if candidate.is_file() and candidate.suffix.lower() in _IMAGE_EXTS:
            return candidate

    name = _safe_name(image_name)
    if not name:
        return None
    lowered = name.lower()
    exact = image_files.get(lowered)
    if exact:
        return exact[0]
    for indexed_name, candidates in image_files.items():
        if indexed_name.endswith(f"_{lowered}") and candidates:
            return candidates[0]
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
        existing_rows = await store.list_history_images_by_sha(
            sha,
            limit=_EXISTING_SHA_REUSE_LIMIT,
        )
        existing = next(
            (
                description
                for r in existing_rows
                if (description := _usable_image_description(str(r.get("description") or "")))
            ),
            "",
        )
        if existing:
            descriptions[sha] = existing
            continue
        data_url = _data_url_for_file(ref.path, ref.mime, settings)
        description = _usable_image_description((await vision_client.describe_images([data_url]))[0])
        if description:
            descriptions[sha] = description
    return descriptions


async def _reusable_existing_image_ids(
    store: MemoryStore,
    existing_rows: Iterable[dict[str, Any]],
) -> set[str]:
    """返回可直接跳过的已有图片行。

    失败占位不能阻止重跑：如果旧导入缓存了失败标记，就让该图片继续 pending，
    后续成功时用真实摘要覆盖旧行。
    """
    reusable: set[str] = set()
    failed_ids: list[str] = []
    for row in existing_rows:
        chunk_id = str(row.get("id") or "")
        if not chunk_id or bool(row.get("deleted")):
            continue
        if _usable_image_description(str(row.get("description") or "")):
            reusable.add(chunk_id)
        else:
            failed_ids.append(chunk_id)
    if failed_ids:
        await store.soft_delete_ids(TABLE_HISTORY_IMAGES, failed_ids)
    return reusable


def _usable_image_description(description: str) -> str:
    desc = description.strip()
    if not desc:
        return ""
    if any(desc.startswith(prefix) for prefix in _FAILED_DESCRIPTION_PREFIXES):
        return ""
    return desc


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
