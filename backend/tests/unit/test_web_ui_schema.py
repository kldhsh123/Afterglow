"""web_ui.schema 从 .env.example 解析字段元数据测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from xuwen.web_ui.schema import (
    SchemaSnapshot,
    build_schema,
    parse_env_example,
)


REPO_ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_parse_env_example_collects_descriptions(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text(
        "# ----- 分组A -----\n"
        "# OPENAI 主模型说明第一行。\n"
        "# 第二行说明。\n"
        "OPENAI_API_KEY=\n"
        "\n"
        "# ----- 分组B -----\n"
        "# 字段说明 B。\n"
        "OTHER=1\n",
        encoding="utf-8",
    )
    desc, group = parse_env_example(example)
    assert "OPENAI 主模型说明第一行" in desc["OPENAI_API_KEY"]
    assert desc["OTHER"] == "字段说明 B。"
    assert group["OPENAI_API_KEY"] == "分组A"
    assert group["OTHER"] == "分组B"


def test_parse_env_example_missing_file_returns_empty(tmp_path: Path) -> None:
    desc, group = parse_env_example(tmp_path / "no.example")
    assert desc == {}
    assert group == {}


@pytest.fixture()
def real_schema() -> SchemaSnapshot:
    return build_schema(REPO_ENV_EXAMPLE)


def test_build_schema_includes_core_fields(real_schema: SchemaSnapshot) -> None:
    keys = {f.key for f in real_schema.fields}
    assert "SELF_NAME" in keys
    assert "OPENAI_API_KEY" in keys
    assert "EMBEDDING_API_KEY" in keys
    assert "XUWEN_API_KEY" in keys
    assert "LABELING_ENABLED" in keys


def test_secret_fields_marked_secret(real_schema: SchemaSnapshot) -> None:
    by_key = {f.key: f for f in real_schema.fields}
    assert by_key["OPENAI_API_KEY"].secret is True
    assert by_key["EMBEDDING_API_KEY"].secret is True
    assert by_key["XUWEN_API_KEY"].secret is True


def test_required_fields_flagged(real_schema: SchemaSnapshot) -> None:
    by_key = {f.key: f for f in real_schema.fields}
    for k in ("SELF_NAME", "SELF_UID", "FRIEND_NAME", "FRIEND_UID", "XUWEN_API_KEY"):
        assert by_key[k].required, f"{k} should be required"


def test_advanced_fields_marked_advanced(real_schema: SchemaSnapshot) -> None:
    by_key = {f.key: f for f in real_schema.fields}
    # 调优 / 内部字段应当标为 advanced，向导默认折叠
    assert by_key["RRF_K"].advanced is True
    assert by_key["WINDOW_SIZE"].advanced is True
    assert by_key["WRITEBACK_BATCH_TURNS"].advanced is True


def test_enum_field_carries_choices(real_schema: SchemaSnapshot) -> None:
    by_key = {f.key: f for f in real_schema.fields}
    rel = by_key["RELATIONSHIP_TYPE"]
    assert rel.type == "enum"
    assert rel.choices is not None
    for c in ("friend", "lover", "family", "colleague", "custom"):
        assert c in rel.choices


def test_groups_collected_in_order(real_schema: SchemaSnapshot) -> None:
    # 不强约束具体名字，但要确保至少抓到了几个组
    assert len(real_schema.groups) >= 3


def test_int_and_bool_types_inferred(real_schema: SchemaSnapshot) -> None:
    by_key = {f.key: f for f in real_schema.fields}
    assert by_key["EMBEDDING_DIM"].type == "int"
    assert by_key["LABELING_ENABLED"].type == "bool"
    assert by_key["VISION_ENABLED"].type == "bool"
