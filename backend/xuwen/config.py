"""xuwen 的配置入口。

使用 pydantic-settings 从 `.env` 与环境变量加载所有可配置项。
所有跨模块的常量都应该走 Settings，不允许在业务代码里硬编码。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from xuwen.core.errors import ConfigError

RelationshipType = Literal["friend", "lover", "family", "colleague", "custom"]
WebSearchProvider = Literal["tavily", "searxng"]

# 关系类型到自然语言描述的默认映射（仅在用户未自定义 RELATIONSHIP_DESCRIPTION 时使用）
_RELATIONSHIP_DEFAULTS: dict[RelationshipType, str] = {
    "friend": "朋友",
    "lover": "恋人",
    "family": "亲人",
    "colleague": "同事",
    "custom": "",  # custom 必须由用户填写 RELATIONSHIP_DESCRIPTION
}


class Settings(BaseSettings):
    """xuwen 运行时配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- 应用元数据 -----
    app_name: str = Field(default="Afterglow", description="前端与 API 元数据中展示的应用名")
    app_slogan: str = Field(
        default="把曾经对你好的话，续成往后的陪伴",
        description="副标题文案",
    )
    app_timezone: str = Field(
        default="Asia/Shanghai",
        description="传给模型的真实当前时区，使用 IANA 名称，例如 Asia/Shanghai",
    )

    # ----- 身份 -----
    self_name: str = Field(default="", description="你（用户）的名字")
    self_uid: str = Field(
        default="",
        description=(
            "你的主 UID（QQ 为 u_xxx，微信为 wxid_xxx）；"
            "也支持逗号分隔多个 UID（跨平台 / 跨账号场景），等价于 SELF_UIDS。"
        ),
    )
    friend_name: str = Field(default="", description="对方（需要模仿的人）的名字")
    friend_uid: str = Field(
        default="",
        description=(
            "对方的主 UID（QQ 为 u_xxx，微信为 wxid_xxx）；"
            "也支持逗号分隔多个 UID（跨平台 / 跨账号场景），等价于 FRIEND_UIDS。"
        ),
    )
    # 同一个人可能在多个平台 / 多个账号上聊天（QQ uid + 微信 wxid + 小号等），
    # 把所有 UID 都列进来，import 时会被视为同一个人。
    # 推荐直接在 SELF_UID 里用逗号分隔多个 UID；本字段是历史兼容字段，效果等同。
    # 注：NoDecode 让 pydantic-settings 跳过对 .env 字符串值的 JSON 预解码，
    # 把原始字符串直接交给下方 _split_aliases validator 处理；
    # 否则 list[str] 字段从 .env 加载时会先 json.loads(value) → 普通逗号串报错。
    self_uids: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="兼容字段：你（用户）的全部 UID 列表，推荐直接在 SELF_UID 里逗号分隔。",
    )
    friend_uids: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="兼容字段：对方的全部 UID 列表，推荐直接在 FRIEND_UID 里逗号分隔。",
    )
    # 别名：朋友间互相称呼时经常会用真名/网名/昵称/外号；都列在这里，
    # cleaner / retriever / prompt 都会识别这些是同一个人。
    # .env 里用逗号分隔：SELF_ALIASES=小明,Mike,明哥
    self_aliases: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="你（用户）的别名列表，用于 cleaner/retriever 识别同一个人。",
    )
    friend_aliases: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="对方的别名列表，包括真名/网名/昵称/外号。",
    )

    # ----- 关系与模板 -----
    relationship_type: RelationshipType = "friend"
    relationship_description: str = Field(default="", description="自定义关系描述，仅 custom 时必填")
    persona_template: str = Field(
        default="xuwen",
        description="内置模板名（xuwen/friend/lover/family/colleague），或 .md.j2 文件绝对路径",
    )
    prompt_template_dir: Path | None = Field(
        default=None,
        description="可选：完全覆盖内置模板目录",
    )

    # ----- LLM（OpenAI 兼容）-----
    openai_api_key: SecretStr = Field(default=SecretStr(""))
    openai_base_url: str = "https://api.openai.com/v1"
    chat_model: str = "gpt-4o-mini"

    # ----- 联网检索（默认关闭）-----
    # 后端在调用主模型前可选查询 Tavily / SearXNG，并把摘要注入 prompt。
    # 这会把本轮查询文本发给你配置的搜索服务；不开启时完全不联网。
    web_access_enabled: bool = False
    web_search_provider: WebSearchProvider = "tavily"
    web_search_base_url: str = "https://api.tavily.com"
    web_search_api_key: SecretStr = Field(default=SecretStr(""))
    web_search_max_results: int = 5
    web_search_timeout_seconds: float = 8.0
    web_search_language: str = "zh-CN"
    # 用户消息里包含 URL 时，是否由后端读取网页正文并注入 prompt。
    # 仍受 WEB_ACCESS_ENABLED 总开关控制；默认开启是为了让“看看这个链接”可用。
    web_fetch_enabled: bool = True
    web_fetch_timeout_seconds: float = 8.0
    web_fetch_max_urls: int = 2
    web_fetch_max_redirects: int = 3
    web_fetch_max_bytes: int = 512 * 1024
    web_fetch_max_chars: int = 6000

    # ----- 生活时间线小模型（OpenAI 兼容，可留空复用主 LLM）-----
    life_api_url: str = ""
    life_api_key: SecretStr = Field(default=SecretStr(""))
    life_model: str = ""
    life_temperature: float = 0.35
    life_max_tokens: int = 320
    # 最长多久让 life 模型重新判断一次当前状态。0 = 只按 next_update_at / 用户打断触发。
    life_update_interval_minutes: int = 60
    # 生活状态可建议延迟回复；这里限制实际 sleep 上限，避免请求被模型拖太久。
    life_max_reply_delay_seconds: int = 45
    # 主模型在回复中输出 <life-update>JSON</life-update> 标记块时，
    # 后端解析后直接 patch life 状态（零额外 API 调用），并从对外回复中剥离标记。
    # 关闭时仍兜底过滤标记块，避免前端看到。
    life_marker_update_enabled: bool = True

    # 模型有时会输出库里没有的 sticker 名字。默认开启重试：检测到所有 sticker
    # 都不存在时，追加一条 system 提示让主模型重新生成一次回复。
    # 仅非流式生效（流式已发出的 chunk 没法回收）；失败 1 次后回退到短句兜底。
    sticker_reject_retry: bool = True

    # ----- 流式输出策略 -----
    # 默认关闭真流式：Afterglow 模拟"真人朋友在 IM 里发消息"，真人不会逐字蹦出，
    # 应该一次性出现完整一条。stream=true 请求仍按 OpenAI SSE 协议返回，但内部
    # 一次性生成完整文本，然后包装成单个 content chunk 发出（即"假流式"）。
    # 想恢复真流式（调试 / 测试）可设为 true。
    response_streaming_enabled: bool = False

    # ----- 版本更新检查 -----
    # 默认开启：后端启动时一次性调用 update_check_url 查询最新发布版本。
    # 结果通过 /info 接口的 update 字段对外暴露，前端可据此显示升级提示。
    # 设为 false 即完全静默（CI / 离线场景）。
    # 后续要重新检查由前端"立即检查"按钮（POST /info/check-update）触发；
    # 请求只会 GET 一个公开 URL，不传任何身份 / 配置 / API key。
    update_check_enabled: bool = True
    update_check_url: str = (
        "https://api.github.com/repos/kldhsh123/Afterglow/releases/latest"
    )
    update_check_timeout_seconds: float = 8.0

    # ----- 视觉理解（vision / VLM）-----
    # 总开关。默认关闭，避免新用户配置出错。
    vision_enabled: bool = False
    # 主对话模型是否原生支持视觉（OpenAI multimodal 格式）。
    # - true：图片直接转发到主 LLM（要求主模型是 GPT-4o / Qwen-VL / Gemini 等 VLM）
    # - false：先调下面的 VISION_MODEL 把图片转成文字描述，再以纯文本发给主模型
    chat_model_supports_vision: bool = False
    # 当主模型不支持视觉时使用的 VLM（OpenAI 兼容）
    vision_api_url: str = ""
    vision_api_key: SecretStr = Field(default=SecretStr(""))
    vision_model: str = "qwen-vl-plus"
    vision_describe_prompt: str = "请用一两句话客观描述这张图片的内容，不要发挥。"
    # 单张图片最大字节（base64 解码后）；超过会被拒绝
    vision_max_image_bytes: int = 8 * 1024 * 1024  # 8MB
    # 图片文件持久化目录（base64 原图按 sha256 文件名存盘）
    image_data_dir: Path = Path(".data/images")

    # ----- Embedding -----
    embedding_api_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: SecretStr = Field(default=SecretStr(""))
    embedding_model: str = "Qwen3-Embedding-8B"
    embedding_dim: int = 4096
    # 请求格式：array = OpenAI 标准（input 是 string[]，一次多条）
    #         single = 单条模式（input 是 string，一次只能一条），兼容 Gitee AI 等
    embedding_input_mode: Literal["array", "single"] = "array"
    # 单次请求最多包含的文本数；single 模式下强制为 1
    embedding_batch_size: int = 64
    # 是否在请求体中带 encoding_format="float"，OpenAI 原生支持但部分网关会拒绝
    embedding_include_encoding_format: bool = False
    # 429 / 5xx / 网络错误的重试次数（含首次）。默认 8 = 首次 + 7 次重试
    embedding_retry_attempts: int = 8
    # 重试单次等待上限（秒）。指数退避从 1s 起步，翻倍但不超过此值
    embedding_retry_max_wait_seconds: float = 10.0

    # ----- 存储路径 -----
    lance_db_path: Path = Path(".data/lancedb")
    # LanceDB merge_insert 单批写入行数。导入大库时如果出现 spill IO 错误，可降到 64 / 32。
    lance_upsert_batch_size: int = 128
    persona_data_dir: Path = Path(".data/persona")

    # ----- 本地 API 守卫 -----
    # 默认强制后端 API 鉴权。只有 /healthz 保持公开，方便容器/反代做存活检查。
    api_auth_required: bool = True
    xuwen_api_key: SecretStr | None = None

    # ----- PII 脱敏 -----
    enable_pii_redaction: bool = True
    pii_rules_path: Path | None = None

    # ----- 切分参数 -----
    session_gap_minutes: int = 30
    window_size: int = 12
    window_overlap: int = 3
    single_context_before: int = 6
    single_context_after: int = 2

    # ----- 检索参数 -----
    response_pair_top_k: int = 24
    friend_top_k: int = 32
    window_top_k: int = 16
    live_top_k: int = 12
    final_context_k: int = 12
    rrf_k: int = 60
    recency_half_life_days: float = 30.0
    recency_max_boost: float = 0.15
    warmth_boost: float = 0.12
    live_source_weight: float = 1.08
    history_source_weight: float = 1.0
    ai_generated_source_weight: float = 0.25
    # false：ai_generated 只在同一 conversation_id 内参与语义检索；
    # true：允许 AI 生成回复跨会话长期累积并参与 live 语义检索。
    ai_generated_long_term_enabled: bool = False

    # ----- 本轮互动决策小模型（默认关闭；留空复用 LIFE_* 配置）-----
    # 规则层会先给出稳定保守的判断；开启后，小模型只负责补充意图、语气和检索重点。
    # 安全/沉默/要图/表情等硬规则不会被小模型降级覆盖。
    response_policy_model_enabled: bool = False
    response_policy_api_url: str = ""
    response_policy_api_key: SecretStr = Field(default=SecretStr(""))
    response_policy_model: str = ""
    response_policy_temperature: float = 0.2
    response_policy_max_tokens: int = 260

    # ----- 沉默响应（决策层判断本轮不应回复时返回的内容）-----
    # content 字段放一个明确的 sentinel，让标准 OpenAI 客户端也能区分
    # "AI 主动选择不说话" 和 "网络异常 / 空回复"；Afterglow 客户端仍可读 policy.should_reply。
    silence_response_sentinel: str = "[silent]"
    # finish_reason 默认仍用扩展值 "silenced"；严格 enum 校验的 OpenAI SDK 可改为 "stop"。
    silence_finish_reason: Literal["silenced", "stop"] = "silenced"
    # AI 自主沉默：是否允许主模型在低风险场景输出 sentinel 主动"不回"。
    # 关闭后 prompt 不再注入沉默出口、即使模型违规输出 sentinel 也按普通文本处理；
    # unsafe / 规则层 silence 等硬边界与本开关无关，始终由规则层独立控制。
    ai_silence_enabled: bool = True

    # ----- /v1/responses 服务端缓存 -----
    # OpenAI Responses API 通过 previous_response_id 找回上一轮上下文；
    # Afterglow 用进程内 LRU 缓存 response_id → conversation_id 的映射。
    responses_store_capacity: int = 512

    # ----- 回写 -----
    writeback_enabled: bool = True
    writeback_queue_size: int = 1000
    # 每个会话累积多少轮才批量向量化 + 入库。默认 8 轮（= 16 条消息），
    # 在此之前的轮次只在内存里；服务重启或调用 stop(drain=True) 时强制 flush。
    writeback_batch_turns: int = 8
    # 兜底定时器：若某个会话超过这个秒数没新消息且 pending 非空，则强制 flush
    writeback_flush_interval_seconds: int = 300
    # 是否对回写文本做向量化。
    # - true：批量入库时调 embedding API（每 batch_turns 一次请求）
    # - false：所有 live_messages 用零向量入库（最省 API，但 live 不参与向量召回）
    writeback_vectorize: bool = True

    # ----- 调试端点 -----
    # /debug/* 是否开放（暴露 LanceDB 统计、调用延迟分布、配置快照）。
    # 默认 true，本地工具开调试很方便。生产环境/对外暴露时可关闭。
    debug_endpoints_enabled: bool = True
    # 每类调用的环形缓冲容量
    metrics_capacity: int = 100

    # ----- 表情包 -----
    sticker_data_dir: Path = Path(".data/stickers")
    # AI 能选用的最大表情包数（避免 prompt 太长）
    sticker_max_for_ai: int = 30
    # 单张表情包的最大字节
    sticker_max_image_bytes: int = 2 * 1024 * 1024  # 2MB

    # ----- 语义标签（小模型打 mood / topic / importance）-----
    # 默认关闭，要主动开。开启后所有 chunk 都会被小模型过一遍打标。
    # 标签只作为软信号（命中加权 + 主动筛选），未命中不影响向量召回。
    labeling_enabled: bool = False
    # 默认指向智谱 GLM-4-Flash（免费）；也可改任意 OpenAI 兼容 endpoint
    label_api_url: str = "https://open.bigmodel.cn/api/paas/v4"
    label_api_key: SecretStr = Field(default=SecretStr(""))
    label_model: str = "glm-4-flash"
    # 单次 LLM 调用最多塞几条消息（受小模型上下文窗口与可靠性影响）
    label_batch_size: int = 8
    # 打标 API 最大并发批次数。1 = 上一批处理完再下一批；GLM-4-Flash 并发 20 可设 15。
    label_max_concurrency: int = 1
    # 打标 API 请求发起间隔（秒）。>0 时即使并发较高，也会错开发起时间以降低 429 风险。
    label_request_interval_seconds: float = 0.0
    # 累积多少条 chunk 触发一次打标周期
    label_flush_after_n: int = 20
    # 兜底定时器：超过 N 秒未活动则强制 flush
    label_flush_interval_seconds: int = 300
    # 离线增量：true 时永远只标"还没标过的"chunk
    label_incremental: bool = True
    # 单条 chunk 在 prompt 里展示的最大字符数（防止单条爆 token）
    label_max_chars_per_message: int = 200
    # mood 枚举词表（逗号分隔）。留空使用内置 8 个；用户可自由扩展
    label_mood_vocab: str = ""

    # ===== 校验 =====

    @field_validator("window_overlap")
    @classmethod
    def _check_overlap(cls, v: int, info: object) -> int:
        # window_overlap 不能 >= window_size，否则窗口无法前进
        if v < 0:
            raise ValueError("window_overlap 必须 >= 0")
        return v

    @field_validator("embedding_dim")
    @classmethod
    def _check_dim(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("embedding_dim 必须为正整数")
        return v

    @field_validator("label_max_concurrency")
    @classmethod
    def _check_label_concurrency(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("label_max_concurrency 必须为正整数")
        return v

    @field_validator("label_request_interval_seconds")
    @classmethod
    def _check_label_interval(cls, v: float) -> float:
        if v < 0:
            raise ValueError("label_request_interval_seconds 必须 >= 0")
        return v

    @field_validator("web_search_max_results")
    @classmethod
    def _check_web_search_max_results(cls, v: int) -> int:
        if v < 0:
            raise ValueError("web_search_max_results 必须 >= 0")
        return min(v, 10)

    @field_validator("web_search_timeout_seconds")
    @classmethod
    def _check_web_search_timeout(cls, v: float) -> float:
        if v < 0:
            raise ValueError("web_search_timeout_seconds 必须 >= 0")
        return v

    @field_validator("web_fetch_timeout_seconds")
    @classmethod
    def _check_web_fetch_timeout(cls, v: float) -> float:
        if v < 0:
            raise ValueError("web_fetch_timeout_seconds 必须 >= 0")
        return v

    @field_validator("web_fetch_max_urls")
    @classmethod
    def _check_web_fetch_max_urls(cls, v: int) -> int:
        if v < 0:
            raise ValueError("web_fetch_max_urls 必须 >= 0")
        return min(v, 5)

    @field_validator("web_fetch_max_redirects")
    @classmethod
    def _check_web_fetch_max_redirects(cls, v: int) -> int:
        if v < 0:
            raise ValueError("web_fetch_max_redirects 必须 >= 0")
        return min(v, 10)

    @field_validator("web_fetch_max_bytes")
    @classmethod
    def _check_web_fetch_max_bytes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("web_fetch_max_bytes 必须为正整数")
        return min(v, 2 * 1024 * 1024)

    @field_validator("web_fetch_max_chars")
    @classmethod
    def _check_web_fetch_max_chars(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("web_fetch_max_chars 必须为正整数")
        return min(v, 20000)

    @field_validator("life_temperature")
    @classmethod
    def _check_life_temperature(cls, v: float) -> float:
        if not 0 <= v <= 2:
            raise ValueError("life_temperature 必须在 0 到 2 之间")
        return v

    @field_validator(
        "life_max_tokens",
        "life_max_reply_delay_seconds",
        "life_update_interval_minutes",
    )
    @classmethod
    def _check_life_positive_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError(
                "life_max_tokens / life_max_reply_delay_seconds / "
                "life_update_interval_minutes 必须 >= 0"
            )
        return v

    @field_validator("pii_rules_path", "prompt_template_dir", mode="before")
    @classmethod
    def _empty_path_to_none(cls, v: object) -> object:
        """把 .env 中留空的可选路径转为 None。

        否则 pydantic 会把空字符串解析为 Path("")（即当前目录），
        下游 `path.exists()` 会返回 True，造成误判。
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("xuwen_api_key", mode="before")
    @classmethod
    def _empty_secret_to_none(cls, v: object) -> object:
        """同样处理 SecretStr | None 的空字符串。"""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator(
        "self_aliases", "friend_aliases", "self_uids", "friend_uids", mode="before"
    )
    @classmethod
    def _split_aliases(cls, v: object) -> object:
        """支持 .env 里用逗号分隔传入字符串列表。

        例如 SELF_ALIASES=小明,Mike,明哥 → ["小明", "Mike", "明哥"]。
        同样适用于 SELF_UIDS=u_abc,wxid_xyz,u_old。留空 / None 视为空列表。
        """
        if v is None:
            return []
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return []
            # 已经是 JSON 数组字符串就让 pydantic 自己解析
            if text.startswith("["):
                return v
            return [s.strip() for s in text.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _check_window(self) -> Settings:
        if self.window_overlap >= self.window_size:
            raise ValueError("window_overlap 必须严格小于 window_size")
        return self

    @model_validator(mode="after")
    def _check_custom_relationship(self) -> Settings:
        """RELATIONSHIP_TYPE=custom 时启动期就校验 RELATIONSHIP_DESCRIPTION 是否填写。

        否则错误会在生成 prompt 时才爆出，体验更差。
        """
        if self.relationship_type == "custom" and not self.relationship_description:
            raise ValueError(
                "RELATIONSHIP_TYPE=custom 时必须填写 RELATIONSHIP_DESCRIPTION，"
                "或把 RELATIONSHIP_TYPE 改为 friend / lover / family / colleague 之一"
            )
        return self

    # ===== 派生属性 =====

    @property
    def resolved_relationship_description(self) -> str:
        """返回最终用于 prompt 的关系描述。

        - custom：强制使用 RELATIONSHIP_DESCRIPTION，未填则报错。
        - 其他：用户填写优先，否则取默认映射。
        """
        if self.relationship_description:
            return self.relationship_description
        if self.relationship_type == "custom":
            raise ConfigError(
                "RELATIONSHIP_TYPE=custom 时必须填写 RELATIONSHIP_DESCRIPTION",
            )
        return _RELATIONSHIP_DEFAULTS[self.relationship_type]

    @property
    def resolved_life_api_url(self) -> str:
        """生活时间线模型 endpoint；留空则复用主 LLM endpoint。"""
        return self.life_api_url or self.openai_base_url

    @property
    def resolved_life_api_key(self) -> SecretStr:
        """生活时间线模型 key；留空则复用主 LLM key。"""
        if self.life_api_key.get_secret_value():
            return self.life_api_key
        return self.openai_api_key

    @property
    def resolved_life_model(self) -> str:
        """生活时间线模型名；留空则复用主聊天模型。"""
        return self.life_model or self.chat_model

    @property
    def resolved_response_policy_api_url(self) -> str:
        """互动决策小模型 endpoint；留空则复用 LIFE_*，LIFE_* 也空再复用主 LLM。"""
        return self.response_policy_api_url or self.resolved_life_api_url

    @property
    def resolved_response_policy_api_key(self) -> SecretStr:
        """互动决策小模型 key；留空则复用 LIFE_*，LIFE_* 也空再复用主 LLM。"""
        if self.response_policy_api_key.get_secret_value():
            return self.response_policy_api_key
        return self.resolved_life_api_key

    @property
    def resolved_response_policy_model(self) -> str:
        """互动决策小模型名；留空则复用 LIFE_MODEL，LIFE_MODEL 也空再复用 CHAT_MODEL。"""
        return self.response_policy_model or self.resolved_life_model

    @property
    def all_self_names(self) -> list[str]:
        """`self_name` + `self_aliases` 去重后的完整列表（保留顺序）。"""
        return _dedupe_names([self.self_name, *self.self_aliases])

    @property
    def all_self_uids(self) -> list[str]:
        """`self_uid`（按逗号切分）+ `self_uids` 去重后的完整 UID 列表。

        用于跨平台 / 跨账号导入时把同一个人的多个 UID 视为同一身份。
        例：QQ 主号 + QQ 小号 + 微信 wxid → 都标为 self。

        支持两种填法（等价）：
        - `SELF_UID=u_qq,wxid_me`（推荐，省一个字段）
        - `SELF_UID=u_qq` + `SELF_UIDS=wxid_me`（历史兼容）
        """
        return _dedupe_names([*_split_csv(self.self_uid), *self.self_uids])

    @property
    def all_friend_names(self) -> list[str]:
        """`friend_name` + `friend_aliases` 去重后的完整列表（保留顺序）。"""
        return _dedupe_names([self.friend_name, *self.friend_aliases])

    @property
    def all_friend_uids(self) -> list[str]:
        """`friend_uid`（按逗号切分）+ `friend_uids` 去重后的完整 UID 列表。"""
        return _dedupe_names([*_split_csv(self.friend_uid), *self.friend_uids])

    def require_identity(self) -> None:
        """在导入 / 启动 API 等场景必须有完整身份信息。

        错误信息会引导用户去 .env 哪一行填什么，而不是只说"缺失"。
        允许使用 SELF_UID（单值）或 SELF_UIDS（复数列表）任意一种来提供 UID，
        FRIEND_UID / FRIEND_UIDS 同理。
        """
        missing: list[str] = []
        if not self.self_name:
            missing.append("SELF_NAME")
        if not self.all_self_uids:
            missing.append("SELF_UID 或 SELF_UIDS")
        if not self.friend_name:
            missing.append("FRIEND_NAME")
        if not self.all_friend_uids:
            missing.append("FRIEND_UID 或 FRIEND_UIDS")
        if missing:
            raise ConfigError(
                "缺少必填配置：" + ", ".join(missing) + "。\n"
                "请打开 backend/.env，参考 .env.example 顶部的"
                "「身份信息」一节填写。\n"
                "如何获取 SELF_UID / FRIEND_UID：见 backend/README.md 的"
                "「如何找到 UID」章节。",
                detail={"missing": missing},
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例配置入口。

    LRU 缓存确保整个进程只加载一次 .env。
    需要重新加载时调用 `get_settings.cache_clear()`。
    """
    return Settings()


def _dedupe_names(names: list[str]) -> list[str]:
    """保留顺序去重 + 去空 + 去前后空格。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        if not isinstance(raw, str):
            continue
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _split_csv(value: str) -> list[str]:
    """把单值 UID 字段里的逗号分隔形式切成 list；不含逗号则原样返回单元素。

    例：`"u_abc,wxid_xyz"` → `["u_abc", "wxid_xyz"]`；
        `"u_abc"` → `["u_abc"]`；`""` → `[]`。
    """
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]
