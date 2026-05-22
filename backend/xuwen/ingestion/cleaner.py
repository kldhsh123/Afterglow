"""文本清洗器：把 parser 输出的 NormalizedMessage 的 text 字段做归一化。

设计原则：**轻清洗，保留口吻**。错别字、语气词、emoji、重复字一律保留，
因为它们是模仿目标的关键风格信号。

主要操作：
1. 去掉不可见控制字符
2. @ 提及替换为 @你 / @我（按名字 / 按 uid 都能识别）
3. 孤立出现的 uid 字符串清理（QQ 偶尔会把 uid 漏到正文）
4. 撤回消息文本固定为 [撤回]
5. 系统消息空文本归一化
6. 资源占位符（[图片]/[语音]/...）追加在末尾（如果原文本不含）
7. （可选）PII 脱敏（手机号 / 邮箱 / 身份证 / 银行卡 / IP）

注：URL、域名、QQ 号不做处理，保留原文。
"""

from __future__ import annotations

import re
from dataclasses import replace

from xuwen.config import Settings
from xuwen.core.models import MessageKind, NormalizedMessage
from xuwen.persona.pii_rules import PIIRule, load_rules, redact

# 控制字符（除常见空白）
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# 形如 [图片: xxx.png] 的原始占位标记，统一替换为 [图片]
_BRACKET_MEDIA_RE = re.compile(r"\[(图片|语音|视频|文件|表情|动画表情)[:：][^\]]+\]")

# QQ 自带文字表情 token：`[/汪汪]` `[/呲牙]` `[/破涕为笑]` 等。
# 这种 token 既不是 Unicode emoji 也不是图片，直接进库会让主模型在回复时
# 模仿出 "[/xxx]" 字面字符串。统一归一化为 [表情]。
_QQ_NATIVE_FACE_RE = re.compile(r"\[/[^\]\n]{1,16}\]")

# QQ uid 通用形式：`u_` + 22 个 base64url 字符
# 用于兜底匹配未知 uid（不是 self/friend 的那种）
_GENERIC_QQ_UID_RE = re.compile(r"@?u_[A-Za-z0-9_-]{20,24}")


def _build_mention_pattern(names: list[str]) -> re.Pattern[str] | None:
    """根据多个名字/别名构造 `@(name1|name2|...)` 提及正则。

    名字按长度降序排，避免短名字优先匹配（如先匹"明"再吃掉"小明"）。
    """
    cleaned = [n for n in names if n]
    if not cleaned:
        return None
    cleaned.sort(key=len, reverse=True)
    alts = "|".join(re.escape(n) for n in cleaned)
    return re.compile(rf"@(?:{alts})")


class Cleaner:
    """聊天文本清洗器。"""

    def __init__(self, settings: Settings, rules: list[PIIRule] | None = None) -> None:
        self.settings = settings
        if settings.enable_pii_redaction:
            self.rules = rules if rules is not None else load_rules(settings.pii_rules_path)
        else:
            self.rules = []
        # 名字形式的 @ 提及替换；同时识别 self_aliases / friend_aliases 里的所有别名。
        self._self_mention_re = _build_mention_pattern(settings.all_self_names)
        self._friend_mention_re = _build_mention_pattern(settings.all_friend_names)
        # uid 形式的 @ 提及替换（兼容 QQ 在 mention 文本里直接写 @u_xxxx 的情况）
        self._self_uid_re = (
            self._uid_pattern(settings.self_uid) if settings.self_uid else None
        )
        self._friend_uid_re = (
            self._uid_pattern(settings.friend_uid) if settings.friend_uid else None
        )

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def clean(self, msg: NormalizedMessage) -> NormalizedMessage:
        """返回清洗后的新 NormalizedMessage（dataclass 不可变，使用 replace）。"""
        text = self._clean_text(msg)
        return replace(msg, text=text)

    def clean_many(self, msgs: list[NormalizedMessage]) -> list[NormalizedMessage]:
        return [self.clean(m) for m in msgs]

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _clean_text(self, msg: NormalizedMessage) -> str:
        # 1) 撤回：强制覆盖
        if msg.kind == MessageKind.RECALLED:
            return "[撤回]"

        # 2) 系统消息：空文本归一化
        if msg.kind == MessageKind.SYSTEM:
            text = (msg.text or "").strip()
            return text or "[系统消息]"

        text = msg.text or ""
        text = _CONTROL_RE.sub("", text)
        text = self._normalize_brackets(text)
        text = self._normalize_mentions(text)
        text = self._normalize_uids(text)

        # 3) 追加占位符（仅当原文不含同名占位符）
        for ph in msg.placeholders:
            if ph not in text:
                text = (text + " " + ph).strip() if text else ph

        # 4) PII 脱敏
        if self.rules:
            text = redact(text, self.rules)

        # 5) 占位符消息但 text 仍空 → 至少给出第一种媒体占位
        if not text.strip() and msg.kind == MessageKind.PLACEHOLDER and msg.placeholders:
            text = msg.placeholders[0]

        # 6) 未知类型且无文本 → 标记
        if not text.strip() and msg.kind == MessageKind.UNKNOWN:
            text = f"[{msg.raw_type or '未知消息'}]"

        return text.strip()

    def _normalize_brackets(self, text: str) -> str:
        """把 [图片: xxx.png] 这种带文件名的占位统一为 [图片]，
        再把 QQ 自带文字表情 `[/汪汪]` 归一化为 [表情]，避免模型学到这种诡异格式。"""
        text = _BRACKET_MEDIA_RE.sub(lambda m: f"[{m.group(1)}]", text)
        text = _QQ_NATIVE_FACE_RE.sub("[表情]", text)
        return text

    def _normalize_mentions(self, text: str) -> str:
        """把 @自己 替换为 @你，@朋友 替换为 @我（站在 friend 视角生成训练样本）。

        注意：模板中朋友是被模仿的对象，因此朋友说"@我"对应模型 "@Me/@你"。
        这里直接转视角，让 chunk 文本里朋友提到自己时是"@我"，提到对方时是"@你"。
        """
        if self._self_mention_re is not None:
            text = self._self_mention_re.sub("@你", text)
        if self._friend_mention_re is not None:
            text = self._friend_mention_re.sub("@我", text)
        return text

    def _normalize_uids(self, text: str) -> str:
        """清理消息中出现的 QQ uid。

        - 已配置的 self_uid / friend_uid：按视角替换为 @你 / @我
        - 其它未知 uid（兜底）：替换为 @某人，避免泄漏 + 污染词频统计
        """
        if self._self_uid_re is not None:
            text = self._self_uid_re.sub("@你", text)
        if self._friend_uid_re is not None:
            text = self._friend_uid_re.sub("@我", text)
        # 兜底：仍残留的 u_xxx 格式 uid（含 @ 或不含）
        text = _GENERIC_QQ_UID_RE.sub("@某人", text)
        return text

    @staticmethod
    def _uid_pattern(uid: str) -> re.Pattern[str]:
        """构造匹配某个 uid 的正则。

        兼容三种出现形式：
        - 完整：@u_ExampleSelfUid00000-w
        - 截断（去掉 u_ 前缀和 -w 后缀的 base64 主体）：ExampleSelfUid00000
        - 单独成词：u_ExampleSelfUid00000-w（无 @ 前缀）
        """
        full = re.escape(uid)
        # 去掉 `u_` 前缀和最后 `-x` 风格的尾标识，得到 base64url 主体
        body = uid
        if body.startswith("u_"):
            body = body[2:]
        # 移除尾部 `-x` 形式（如 `-w`）
        body = re.sub(r"-[A-Za-z0-9]$", "", body)
        body_re = re.escape(body) if len(body) >= 10 else None

        alts = [f"@?{full}"]
        if body_re is not None:
            alts.append(rf"@?(?<![A-Za-z0-9]){body_re}(?![A-Za-z0-9])")
        return re.compile("|".join(alts))
