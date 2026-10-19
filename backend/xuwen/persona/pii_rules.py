"""PII（个人可识别信息）脱敏规则。

- 默认规则集覆盖：手机号、邮箱、身份证、银行卡、IPv4。
- 用户可通过 PII_RULES_PATH 加载自定义规则文件（YAML / JSON 列表）。
- 规则文件格式：[{name, pattern, replacement, flags?}]

特别说明：
- 银行卡用 Luhn 算法二次校验，避免误伤订单号、长时间戳、消息 id。
- 身份证除正则外还校验出生日期合法性（含闰月）。
- QQ 号、URL、域名**不脱敏**（设计决策见 README）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from xuwen.core.errors import ConfigError
from xuwen.error_i18n import error_text


@dataclass(slots=True, frozen=True)
class PIIRule:
    """单条脱敏规则。

    - pattern：正则
    - replacement：固定替换串
    - validator：可选的二次校验回调，接收正则 group(0) 返回 True 才替换
    """

    name: str
    pattern: re.Pattern[str]
    replacement: str
    validator: Callable[[str], bool] | None = None

    def apply(self, text: str) -> str:
        if self.validator is None:
            return self.pattern.sub(self.replacement, text)

        validator = self.validator
        replacement = self.replacement

        def _sub(match: re.Match[str]) -> str:
            return replacement if validator(match.group(0)) else match.group(0)

        return self.pattern.sub(_sub, text)


# ---------------------------------------------------------------------------
# 校验函数
# ---------------------------------------------------------------------------


def _luhn_valid(card: str) -> bool:
    """Luhn 算法校验银行卡号。

    见 https://en.wikipedia.org/wiki/Luhn_algorithm
    """
    digits = [int(ch) for ch in re.sub(r"[ -]", "", card) if ch.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = (len(digits) - 2) % 2
    for i, d in enumerate(digits[:-1]):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    checksum += digits[-1]
    return checksum % 10 == 0


def _id_card_valid(idn: str) -> bool:
    """18 位身份证号合法性校验：日期 + ISO 7064 校验位。"""
    if len(idn) != 18:
        return False
    body, check = idn[:17], idn[17].upper()
    if not body.isdigit():
        return False
    # 出生日期
    year, month, day = int(body[6:10]), int(body[10:12]), int(body[12:14])
    if not (1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31):
        return False
    # 闰月细化
    days_in_month = [31, 29 if _is_leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if day > days_in_month[month - 1]:
        return False
    # ISO 7064 校验位
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    s = sum(int(c) * w for c, w in zip(body, weights, strict=True))
    expected = "10X98765432"[s % 11]
    return check == expected


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


# 内置默认规则。
# 注意顺序：长 pattern 在前，避免被短 pattern 提前匹配吞掉。
# 仅保留高敏感字段：身份证 / 银行卡 / 手机号 / 邮箱 / IP。
# QQ 号、URL/域名 不做脱敏（用户配置）。
DEFAULT_RULES: list[PIIRule] = [
    # 18 位身份证（含末位校验位 X/x）。再用 ISO 7064 二次校验。
    PIIRule(
        name="id_card",
        pattern=re.compile(
            r"\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
        ),
        replacement="[身份证]",
        validator=_id_card_valid,
    ),
    # 16-19 位银行卡号，用 Luhn 校验避免误伤订单号 / 时间戳 / 消息 id。
    PIIRule(
        name="bank_card",
        pattern=re.compile(r"\b(?:\d[ -]?){15,18}\d\b"),
        replacement="[银行卡]",
        validator=_luhn_valid,
    ),
    # 中国大陆手机号 1[3-9]xxxxxxxxx，11 位。
    PIIRule(
        name="cn_mobile",
        pattern=re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        replacement="[手机号]",
    ),
    # 邮箱
    PIIRule(
        name="email",
        pattern=re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
        replacement="[邮箱]",
    ),
    # IPv4
    PIIRule(
        name="ipv4",
        pattern=re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        replacement="[IP]",
    ),
]


def load_rules(path: Path | None = None) -> list[PIIRule]:
    """加载脱敏规则。

    - path 为 None：返回默认规则。
    - path 指向 YAML/JSON：在默认规则前追加用户规则（用户规则优先匹配）。
    """
    if path is None:
        return list(DEFAULT_RULES)

    if not path.exists():
        raise ConfigError(error_text("PII rules file not found: {p0}", p0=path))

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)

    if not isinstance(raw, list):
        raise ConfigError(error_text("The PII rules file must be an array of rule objects"))

    custom: list[PIIRule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "custom")
        pattern_str = entry.get("pattern")
        replacement = str(entry.get("replacement") or f"[{name}]")
        flags_str = str(entry.get("flags") or "")
        if not isinstance(pattern_str, str) or not pattern_str:
            continue
        flags = 0
        if "i" in flags_str.lower():
            flags |= re.IGNORECASE
        if "m" in flags_str.lower():
            flags |= re.MULTILINE
        custom.append(
            PIIRule(name=name, pattern=re.compile(pattern_str, flags), replacement=replacement)
        )

    # 用户规则优先（放在前面）
    return [*custom, *DEFAULT_RULES]


def redact(text: str, rules: list[PIIRule]) -> str:
    """对文本依次应用所有规则。"""
    if not text:
        return text
    for r in rules:
        text = r.apply(text)
    return text
