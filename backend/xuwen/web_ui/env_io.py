""".env 文件的行级读写。

设计目标：
- 保留所有注释、空行、字段顺序、inline 注释
- 修改时只替换 Assignment 的值，其它行原样保留
- 新增字段追加到文件末尾
- 写入前原子化（写临时文件 → os.replace）
- 写入前自动备份到 `.env-backups/.env.bak.<timestamp>`（独立目录，不污染 backend 根）

不依赖 python-dotenv 的 set_key，因为它会丢失部分注释格式且非原子。
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

# 备份子目录：跟 .env 同级建一个隐藏目录集中放备份
_BACKUP_DIRNAME = ".env-backups"

# KEY=value 形式。允许 KEY 含字母数字下划线，等号两侧无空格也允许有空格。
# value 可能带引号、含 # 注释（仅识别 value 之后、被空格分隔的 #）。
_ASSIGN_RE = re.compile(
    r"""
    ^
    (?P<key>[A-Za-z_][A-Za-z0-9_]*)
    \s*=\s*
    (?P<value>.*?)
    (?P<trailing>\s+\#.*)?
    $
    """,
    re.VERBOSE,
)


@dataclass
class EnvLine:
    """.env 中的一行。三种之一：assignment / comment / blank。"""

    kind: str  # "assign" | "comment" | "blank"
    raw: str  # 原始整行（不含换行符）
    key: str | None = None
    value: str | None = None
    trailing_comment: str | None = None  # 形如 "  # 说明"

    def render(self) -> str:
        if self.kind != "assign":
            return self.raw
        # 保持简洁：值含空格、引号、特殊符号时用双引号包起来
        v = self.value or ""
        if _needs_quote(v):
            v = '"' + v.replace('"', '\\"') + '"'
        line = f"{self.key}={v}"
        if self.trailing_comment:
            line += self.trailing_comment
        return line


@dataclass
class EnvDocument:
    """.env 文件的内存表示。"""

    lines: list[EnvLine] = field(default_factory=list)

    def get(self, key: str) -> str | None:
        for ln in self.lines:
            if ln.kind == "assign" and ln.key == key:
                return ln.value
        return None

    def keys(self) -> list[str]:
        return [ln.key for ln in self.lines if ln.kind == "assign" and ln.key]

    def set(self, key: str, value: str) -> None:
        """更新已有 key 或在末尾追加新 key。"""
        for ln in self.lines:
            if ln.kind == "assign" and ln.key == key:
                ln.value = value
                return
        self.lines.append(EnvLine(kind="assign", raw="", key=key, value=value))

    def remove(self, key: str) -> bool:
        """注释掉指定 key 而非物理删除，方便回滚。"""
        for ln in self.lines:
            if ln.kind == "assign" and ln.key == key:
                ts = time.strftime("%Y%m%d-%H%M%S")
                ln.kind = "comment"
                ln.raw = f"# {ln.key}={ln.value}  # removed at {ts}"
                ln.key = None
                ln.value = None
                ln.trailing_comment = None
                return True
        return False

    def render(self) -> str:
        return "\n".join(ln.render() for ln in self.lines) + "\n"


def _needs_quote(v: str) -> bool:
    """判断 value 是否需要加引号才能在 .env 里表达准确。"""
    if v == "":
        return False
    if v.startswith(("'", '"')):
        return False  # 已经有引号了不重复加
    # pydantic-settings 默认按行解析，包含 # 或前后有空格时要引号
    return bool(re.search(r"[#\s]", v))


def parse_env(text: str) -> EnvDocument:
    """解析 .env 文本为 EnvDocument。"""
    doc = EnvDocument()
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            doc.lines.append(EnvLine(kind="blank", raw=raw))
            continue
        if stripped.startswith("#"):
            doc.lines.append(EnvLine(kind="comment", raw=raw))
            continue
        m = _ASSIGN_RE.match(raw)
        if m:
            key = m.group("key")
            value = (m.group("value") or "").strip()
            value = _unquote(value)
            trailing = m.group("trailing") or None
            doc.lines.append(
                EnvLine(
                    kind="assign",
                    raw=raw,
                    key=key,
                    value=value,
                    trailing_comment=trailing,
                )
            )
            continue
        # 不认识的行（多行值等）原样保留，归为 comment 避免被改写
        doc.lines.append(EnvLine(kind="comment", raw=raw))
    return doc


def _unquote(v: str) -> str:
    """剥掉成对的双/单引号；不成对则保持原样。"""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        inner = v[1:-1]
        if v[0] == '"':
            inner = inner.replace('\\"', '"')
        return inner
    return v


def load_env(env_path: Path) -> EnvDocument:
    """读取 .env；文件不存在时返回空文档。"""
    if not env_path.exists():
        return EnvDocument()
    text = env_path.read_text(encoding="utf-8")
    return parse_env(text)


def _backup_dir(env_path: Path) -> Path:
    """备份目录：.env 同级的 .env-backups/。"""
    return env_path.parent / _BACKUP_DIRNAME


def write_env_atomic(
    env_path: Path,
    doc: EnvDocument,
    *,
    backup: bool = True,
) -> Path | None:
    """原子写入 .env。

    成功返回备份文件路径（如有备份）。失败时不会覆盖原文件。
    备份统一放到 .env-backups/ 子目录，避免备份文件污染 backend 根目录。
    """
    backup_path: Path | None = None
    env_path.parent.mkdir(parents=True, exist_ok=True)

    if backup and env_path.exists():
        backup_dir = _backup_dir(env_path)
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{env_path.name}.bak.{ts}"
        shutil.copy2(env_path, backup_path)

    tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp_path.write_text(doc.render(), encoding="utf-8")
    os.replace(tmp_path, env_path)
    return backup_path


def list_backups(env_path: Path) -> list[Path]:
    """列出该 .env 的所有备份文件，按时间倒序。

    优先读 .env-backups/ 子目录；老版本可能把备份散落在 .env 同级，
    一并扫描兼容。
    """
    backup_dir = _backup_dir(env_path)
    stem = env_path.name
    found: list[Path] = []
    # 新版位置
    if backup_dir.exists():
        found.extend(p for p in backup_dir.iterdir() if p.name.startswith(stem + ".bak."))
    # 兼容老版散落在父目录的备份
    parent = env_path.parent
    if parent.exists():
        found.extend(p for p in parent.iterdir() if p.name.startswith(stem + ".bak."))
    return sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)


def restore_backup(env_path: Path, backup_path: Path) -> Path:
    """把指定备份还原为当前 .env。还原前会再做一次备份，避免误回滚。"""
    if not backup_path.exists():
        raise FileNotFoundError(f"备份文件不存在：{backup_path}")
    # 先把当前 .env 再备份一份（rollback-of-rollback）
    if env_path.exists():
        backup_dir = _backup_dir(env_path)
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        safety = backup_dir / f"{env_path.name}.bak.{ts}.pre-restore"
        shutil.copy2(env_path, safety)
    shutil.copy2(backup_path, env_path)
    return env_path
