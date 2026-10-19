"""Build the public editor schema from Settings, templates and bilingual references."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASIC = {
    "XUWEN_LANGUAGE",
    "APP_TIMEZONE",
    "SELF_NAME",
    "SELF_UID",
    "FRIEND_NAME",
    "FRIEND_UID",
    "RELATIONSHIP_TYPE",
    "RELATIONSHIP_DESCRIPTION",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "CHAT_MODEL",
    "CHAT_API_PROTOCOL",
    "EMBEDDING_API_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "XUWEN_API_KEY",
}


def references(path: Path) -> dict:
    result = {}
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^#{2,3} ", line):
            section = line.lstrip("# ")
        match = re.match(r"^\| `([A-Z][A-Z0-9_]+)` \| (.*?) \| (.*) \|$", line)
        if match and match.group(2) != "`XUWEN_API_KEY`":
            key, default, description = match.groups()
            if key in result:
                raise ValueError(f"{path}: duplicate {key}")
            result[key] = {
                "group": section,
                "description": description,
                "documentedDefault": default,
            }
    return result


def template_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if not match:
            raise ValueError(f"{path}: expected a single-line KEY=value")
        key, value = match.groups()
        if key in values:
            raise ValueError(f"{path}: duplicate {key}")
        if value.startswith(('"', "'")) and value.endswith(value[0]):
            value = value[1:-1]
        values[key] = value
    return values


def build_schema() -> list[dict]:
    tree = ast.parse((ROOT / "backend/xuwen/config.py").read_text(encoding="utf-8"))
    aliases = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    fields = {}
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name not in (
            "Settings",
            "LanguageSettings",
        ):
            continue
        for field in cls.body:
            if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                fields[field.target.id.upper()] = field
    texts = {
        lang: references(ROOT / "docs" / filename)
        for lang, filename in [
            ("en", "en/environment.md"),
            ("zh", "wiki/后端环境变量.md"),
        ]
    }
    values = template_values(ROOT / "backend/.env.example")
    chinese = template_values(ROOT / "backend/.env.example.zh-CN")
    if values != chinese:
        raise ValueError(
            "English and Chinese .env templates must have identical keys and values"
        )
    for label, items in [
        ("English reference", texts["en"]),
        ("Chinese reference", texts["zh"]),
        ("template", values),
    ]:
        if set(items) != set(fields):
            raise ValueError(
                f"{label}: missing={sorted(set(fields) - set(items))}, extra={sorted(set(items) - set(fields))}"
            )
    result = []
    for key, default in values.items():
        field = fields[key]
        annotation = field.annotation
        if isinstance(annotation, ast.Name):
            annotation = aliases.get(annotation.id, annotation)
        typename = ast.unparse(annotation)
        entry = {
            "key": key,
            "default": default,
            "basic": key in BASIC,
            "secret": "SecretStr" in typename or key.endswith(("_API_KEY", "_TOKEN")),
            "type": typename if typename in ("bool", "int", "float") else "str",
            "text": {
                lang: {
                    "group": info[key]["group"],
                    "description": info[key]["description"],
                }
                for lang, info in texts.items()
            },
        }
        if (
            isinstance(annotation, ast.Subscript)
            and ast.unparse(annotation.value) == "Literal"
        ):
            elements = (
                annotation.slice.elts
                if isinstance(annotation.slice, ast.Tuple)
                else [annotation.slice]
            )
            entry["options"] = [ast.literal_eval(item) for item in elements]
        if (
            isinstance(field.value, ast.Call)
            and ast.unparse(field.value.func) == "Field"
        ):
            for kw in field.value.keywords:
                if kw.arg in ("ge", "gt", "le", "lt"):
                    entry[kw.arg] = ast.literal_eval(kw.value)
        result.append(entry)
    return result


if __name__ == "__main__":
    print(json.dumps(build_schema(), ensure_ascii=False, indent=2))
