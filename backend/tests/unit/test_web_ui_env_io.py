"""web_ui.env_io 行级 .env 读写测试。

覆盖：
- 注释 / 空行 / inline 注释 / 引号值的解析与回写保真
- set 修改已有 key，set 不存在 key 时追加到末尾
- remove 注释化（不物理删除）
- write_env_atomic 原子写 + 自动备份
- 不存在的 .env load 应返回空文档
- list_backups 按 mtime 倒序
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from xuwen.web_ui.env_io import (
    EnvDocument,
    list_backups,
    load_env,
    parse_env,
    restore_backup,
    write_env_atomic,
)


def test_parse_preserves_comments_and_blanks() -> None:
    src = "# top\n\nKEY=value\n# trailing\n"
    doc = parse_env(src)
    kinds = [ln.kind for ln in doc.lines]
    assert kinds == ["comment", "blank", "assign", "comment"]


def test_parse_assignment_with_inline_comment() -> None:
    doc = parse_env("URL=https://x.example  # 这是注释\n")
    a = next(ln for ln in doc.lines if ln.kind == "assign")
    assert a.key == "URL"
    assert a.value == "https://x.example"
    assert a.trailing_comment is not None
    assert "这是注释" in a.trailing_comment


def test_unquote_paired_double_quotes() -> None:
    doc = parse_env('NAME="hello world"\n')
    assert doc.get("NAME") == "hello world"


def test_unquote_paired_single_quotes() -> None:
    doc = parse_env("NAME='abc def'\n")
    assert doc.get("NAME") == "abc def"


def test_unquote_unbalanced_keeps_raw() -> None:
    # 单边引号不当 unquote 处理
    doc = parse_env('NAME="abc\n')
    assert doc.get("NAME") == '"abc'


def test_set_updates_existing_key() -> None:
    doc = parse_env("FOO=old\nBAR=keep\n")
    doc.set("FOO", "new")
    out = doc.render()
    assert "FOO=new" in out
    assert "BAR=keep" in out
    # 顺序保持：FOO 仍在 BAR 前
    assert out.index("FOO=new") < out.index("BAR=keep")


def test_set_appends_new_key_at_end() -> None:
    doc = parse_env("FOO=1\n")
    doc.set("NEW_KEY", "abc")
    out = doc.render()
    assert "FOO=1" in out
    assert out.rstrip().endswith("NEW_KEY=abc")


def test_render_quotes_values_with_spaces() -> None:
    doc = EnvDocument()
    doc.set("MSG", "hello world")
    out = doc.render()
    assert 'MSG="hello world"' in out


def test_render_quotes_values_with_hash() -> None:
    doc = EnvDocument()
    doc.set("VAL", "abc#def")
    out = doc.render()
    assert '"abc#def"' in out


def test_remove_comments_out_rather_than_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = parse_env("FOO=secret\nBAR=keep\n")
    monkeypatch.setattr(time, "strftime", lambda _: "20260525-120000")
    removed = doc.remove("FOO")
    assert removed is True
    out = doc.render()
    # 应该被注释而非删除
    assert "# FOO=secret" in out
    assert "removed at 20260525-120000" in out
    # 仍能 render BAR
    assert "BAR=keep" in out


def test_remove_returns_false_for_missing_key() -> None:
    doc = parse_env("FOO=1\n")
    assert doc.remove("NOPE") is False


def test_load_env_returns_empty_when_missing(tmp_path: Path) -> None:
    doc = load_env(tmp_path / "no_such.env")
    assert doc.lines == []


def test_write_env_atomic_creates_file_and_backup(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=old\n", encoding="utf-8")

    doc = load_env(env_path)
    doc.set("FOO", "new")
    backup = write_env_atomic(env_path, doc, backup=True)

    assert backup is not None and backup.exists()
    # 新设计：备份放在 .env-backups/ 子目录，不污染 backend 根
    assert backup.parent.name == ".env-backups"
    assert backup.read_text(encoding="utf-8") == "FOO=old\n"
    assert "FOO=new" in env_path.read_text(encoding="utf-8")


def test_write_env_atomic_no_backup_when_target_missing(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    doc = EnvDocument()
    doc.set("FOO", "1")
    backup = write_env_atomic(env_path, doc, backup=True)
    # 原文件不存在 → 不会备份
    assert backup is None
    assert env_path.exists()


def test_list_backups_sorted_by_mtime_desc(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("v=1\n", encoding="utf-8")

    backup_dir = tmp_path / ".env-backups"
    backup_dir.mkdir()
    b1 = backup_dir / ".env.bak.20260101-010101"
    b1.write_text("old\n", encoding="utf-8")
    b2 = backup_dir / ".env.bak.20260102-020202"
    b2.write_text("newer\n", encoding="utf-8")
    import os

    os.utime(b1, (1_000_000, 1_000_000))
    os.utime(b2, (2_000_000, 2_000_000))

    backups = list_backups(env_path)
    assert [p.name for p in backups][:2] == [b2.name, b1.name]


def test_list_backups_compatible_with_legacy_location(tmp_path: Path) -> None:
    """旧版备份散落在 .env 同级，list_backups 应一并扫描。"""
    env_path = tmp_path / ".env"
    env_path.write_text("v=1\n", encoding="utf-8")
    legacy = tmp_path / ".env.bak.20250101-010101"
    legacy.write_text("legacy\n", encoding="utf-8")
    backups = list_backups(env_path)
    assert legacy in backups


def test_restore_backup_overwrites_current_and_safetybackup(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("now=current\n", encoding="utf-8")
    backup_dir = tmp_path / ".env-backups"
    backup_dir.mkdir()
    backup = backup_dir / ".env.bak.20260101-010101"
    backup.write_text("now=old\n", encoding="utf-8")

    restored = restore_backup(env_path, backup)
    assert restored == env_path
    assert env_path.read_text(encoding="utf-8") == "now=old\n"
    # 应该额外产生一个 .pre-restore 备份（也在 .env-backups/ 里）
    pre = [p for p in backup_dir.iterdir() if p.name.endswith(".pre-restore")]
    assert pre, ".pre-restore 备份未生成"


def test_restore_backup_raises_when_backup_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        restore_backup(tmp_path / ".env", tmp_path / "nope.bak")


def test_full_roundtrip_preserves_layout(tmp_path: Path) -> None:
    """业务最关心的场景：保留注释 + 修改字段 + 追加新 key 不破坏结构。"""
    src = (
        "# 顶层注释\n"
        "\n"
        "# ----- 分组 -----\n"
        "OPENAI_API_KEY=\n"
        "CHAT_MODEL=gpt-x  # inline\n"
        "\n"
        "# 下一段\n"
        "EMBEDDING_DIM=1024\n"
    )
    env_path = tmp_path / ".env"
    env_path.write_text(src, encoding="utf-8")

    doc = load_env(env_path)
    doc.set("OPENAI_API_KEY", "sk-fake")
    doc.set("NEW_FIELD", "added")
    write_env_atomic(env_path, doc, backup=False)

    out = env_path.read_text(encoding="utf-8")
    assert "# 顶层注释" in out
    assert "# ----- 分组 -----" in out
    assert "OPENAI_API_KEY=sk-fake" in out
    assert "CHAT_MODEL=gpt-x  # inline" in out  # inline 注释保留
    assert "EMBEDDING_DIM=1024" in out
    assert out.rstrip().endswith("NEW_FIELD=added")
