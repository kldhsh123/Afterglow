"""服务商预设：选一个预设 → 自动填好 base_url + 默认模型 + 申请入口链接。

预设按用途分类：聊天 LLM / Embedding / 打标 LLM / 视觉 LLM。
所有预设都是 OpenAI 兼容接口。"用户自定义"留作兜底，由前端开放任意输入。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Preset:
    id: str
    label: str  # 给小白看的名字
    base_url: str
    default_model: str
    apply_url: str  # 申请 API key 的入口
    hint: str  # 一句话提示


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


def find_preset(presets: list[Preset], preset_id: str) -> Preset | None:
    for p in presets:
        if p.id == preset_id:
            return p
    return None
