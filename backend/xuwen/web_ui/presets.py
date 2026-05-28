"""服务商预设：选一个预设 → 自动填好 base_url + 默认模型 + 申请入口链接。

预设按用途分类：聊天 LLM / Embedding / 打标 LLM / 视觉 LLM /
LLM 重排 / Cross-encoder 重排。所有 OpenAI 兼容接口的预设结构一致；
cross-rerank 因为有 protocol 维度，用 extra 承载。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Preset:
    id: str
    label: str  # 给小白看的名字
    base_url: str
    default_model: str
    apply_url: str  # 申请 API key 的入口
    hint: str  # 一句话提示
    # 额外字段：cross-rerank 用 {"protocol": "jina|dashscope"}；其它分类暂不用。
    # 设计为 dict 是为了让前端不用为新协议字段升级类型；后端能加任何 string→string 元数据。
    extra: dict[str, str] = field(default_factory=dict)


# 聊天 LLM 预设
# 只保留官方原厂 + 本地 + 自定义中转站。不列国内其它"代理转售"渠道，
# 用户如果用的是 one-api / newapi / 各类中转服务，统一走"自定义中转站"。
CHAT_PRESETS: list[Preset] = [
    Preset(
        id="deepseek",
        label="DeepSeek（推荐，便宜国内速度快）",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-v4-pro",
        apply_url="https://platform.deepseek.com/api_keys",
        hint="官方原厂，密钥以 sk- 开头",
    ),
    Preset(
        id="gemini",
        label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-3.5-flash",
        apply_url="https://aistudio.google.com/apikey",
        hint="Google AI Studio 申请，国内需代理；走官方 OpenAI 兼容端点",
    ),
    Preset(
        id="custom",
        label="自定义中转站 / OpenAI 兼容接口",
        # 用 RFC 2606 example 域作占位，让用户一眼看出要替换
        base_url="https://your-relay.example.com/v1",
        default_model="",
        apply_url="",
        hint="支持 one-api / newapi / Azure 部署等 OpenAI 兼容服务",
    ),
    Preset(
        id="ollama",
        label="本地 Ollama",
        base_url="http://127.0.0.1:11434/v1",
        default_model="",
        apply_url="https://ollama.com/library",
        hint="本地运行 ollama serve 即可调用，密钥可填任意值（如 ollama）",
    ),
]

# Embedding 预设
EMBEDDING_PRESETS: list[Preset] = [
    Preset(
        id="dashscope",
        label="阿里云 DashScope（推荐，有免费额度）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="Qwen3-Embedding-8B",
        apply_url="https://bailian.console.aliyun.com/?apiKey=1",
        hint="默认 Qwen3-Embedding-8B（4096 维），上下文召回更精准",
    ),
    Preset(
        id="siliconflow",
        label="SiliconFlow（国内，模型多）",
        base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen3-Embedding-8B",
        apply_url="https://cloud.siliconflow.cn/account/ak",
        hint="维度 4096",
    ),
    Preset(
        id="custom",
        label="自定义中转站 / OpenAI 兼容接口",
        base_url="https://your-relay.example.com/v1",
        default_model="",
        apply_url="",
        hint="支持 one-api / newapi / 自部署 embedding 服务等 OpenAI 兼容接口",
    ),
    Preset(
        id="ollama-emb",
        label="本地 Ollama",
        base_url="http://127.0.0.1:11434/v1",
        default_model="nomic-embed-text",
        apply_url="https://ollama.com/library/nomic-embed-text",
        hint="本地运行 ollama pull nomic-embed-text",
    ),
]

# 打标 LLM 预设：只需要便宜小模型
LABEL_PRESETS: list[Preset] = [
    Preset(
        id="zhipu-flash",
        label="智谱 GLM-4-Flash（推荐，免费额度大）",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-flash",
        apply_url="https://www.bigmodel.cn/usercenter/proj-mgmt/apikeys",
        hint="智谱开放平台提供，账号并发上限 20，足够打标使用",
    ),
    Preset(
        id="custom",
        label="自定义中转站 / OpenAI 兼容接口",
        base_url="https://your-relay.example.com/v1",
        default_model="",
        apply_url="",
        hint="任意支持 OpenAI /chat/completions 协议的小模型服务",
    ),
]


# LLM-as-reranker 预设：要求指令跟随好，主流便宜小模型都行
RERANKER_PRESETS: list[Preset] = [
    Preset(
        id="zhipu-flash",
        label="智谱 GLM-4-Flash（推荐，免费额度）",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-flash",
        apply_url="https://www.bigmodel.cn/usercenter/proj-mgmt/apikeys",
        hint="便宜稳定，指令跟随对 JSON 输出友好",
    ),
    Preset(
        id="dashscope-turbo",
        label="阿里 Qwen-Turbo",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-turbo",
        apply_url="https://bailian.console.aliyun.com/?apiKey=1",
        hint="如果 embedding 已用 DashScope 可以复用 key",
    ),
    Preset(
        id="reuse-label",
        label="复用打标模型（最省配置）",
        base_url="",
        default_model="",
        apply_url="",
        hint="留空 RERANK_API_URL/KEY/MODEL 后端会自动复用 LABEL_*/LIFE_*/主 LLM",
    ),
    Preset(
        id="custom",
        label="自定义中转站 / OpenAI 兼容接口",
        base_url="https://your-relay.example.com/v1",
        default_model="",
        apply_url="",
        hint="任意 OpenAI 兼容小模型服务",
    ),
]


# Cross-encoder 专用 reranker 预设：协议二选一（jina-style 或 dashscope）
CROSS_RERANKER_PRESETS: list[Preset] = [
    Preset(
        id="dashscope-gte",
        label="阿里 DashScope gte-rerank（推荐，中文好）",
        base_url="https://dashscope.aliyuncs.com/api/v1",
        default_model="gte-rerank-v2",
        apply_url="https://bailian.console.aliyun.com/?apiKey=1",
        hint="如果 embedding 已用 DashScope 可以复用 key；走 dashscope 原生协议",
        extra={"protocol": "dashscope"},
    ),
    Preset(
        id="siliconflow-bge",
        label="SiliconFlow bge-reranker-v2-m3（国内托管开源）",
        base_url="https://api.siliconflow.cn/v1",
        default_model="BAAI/bge-reranker-v2-m3",
        apply_url="https://cloud.siliconflow.cn/account/ak",
        hint="按 token 计费，注册有免费额度；走 jina 兼容协议",
        extra={"protocol": "jina"},
    ),
    Preset(
        id="jina-v2",
        label="Jina Reranker v2",
        base_url="https://api.jina.ai/v1",
        default_model="jina-reranker-v2-base-multilingual",
        apply_url="https://jina.ai/api-dashboard/key-manager",
        hint="国际服务，注册有免费额度，国内访问可能需要代理",
        extra={"protocol": "jina"},
    ),
    Preset(
        id="cohere",
        label="Cohere Rerank v3",
        base_url="https://api.cohere.com/v2",
        default_model="rerank-multilingual-v3.0",
        apply_url="https://dashboard.cohere.com/api-keys",
        hint="国际服务，按千次计费，国内访问需要代理",
        extra={"protocol": "jina"},
    ),
    Preset(
        id="custom",
        label="自定义（本地 bge / 私有 reranker 服务）",
        base_url="http://127.0.0.1:8080/v1",
        default_model="BAAI/bge-reranker-v2-m3",
        apply_url="",
        hint="任意暴露 jina-style /rerank 或 DashScope text-rerank 协议的服务",
        extra={"protocol": "jina"},
    ),
]


def find_preset(presets: list[Preset], preset_id: str) -> Preset | None:
    for p in presets:
        if p.id == preset_id:
            return p
    return None
