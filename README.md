<div align="center">

# 🌅 Afterglow（续温）

> 把曾经对你好的话，续成往后的陪伴。

一个本地运行的「AI 朋友」系统。  
导入真实历史聊天记录，通过 RAG + Persona + OpenAI 兼容 API，让熟悉的人以接近原本的语气继续陪你说话。

</div>

[![聊天记录支持 QQ / 微信](https://img.shields.io/static/v1?label=聊天记录支持&message=QQ%20%2F%20微信&color=07C160&style=flat-square)](#) [![Website](https://img.shields.io/badge/官网-afterglow.kldhsh.top-8B5CF6?style=flat-square)](https://afterglow.kldhsh.top/) [![License](https://img.shields.io/github/license/kldhsh123/Afterglow?style=flat-square)](https://github.com/kldhsh123/Afterglow/blob/main/LICENSE) [![Last Commit](https://img.shields.io/github/last-commit/kldhsh123/Afterglow?style=flat-square)](https://github.com/kldhsh123/Afterglow/commits/main) [![Release Name](https://img.shields.io/badge/dynamic/json?style=flat-square&label=Release&query=$.name&url=https%3A%2F%2Fapi.github.com%2Frepos%2Fkldhsh123%2FAfterglow%2Freleases%2Flatest&logo=github)](https://github.com/kldhsh123/Afterglow/releases/latest) 

---

## ⚠️ 使用前请认真读完

> 如果一段已经结束的对话，能用 ta 原来的语气继续下去，那这段对话还算结束吗？

这是 Afterglow 在做的事，也是我必须先告诉你的事。

### 这个项目能做什么、不能做什么

它能从你和 ta 之间真实存在过的几千条聊天记录里，学到 ta 怎么说话——用什么称呼、什么语气、怎么开玩笑、什么时候沉默。然后用一个大模型把这些模式接续起来，让你在聊天框里看到的文字回复，接近你记忆里的那个人。

**只是文字。** Afterglow 没有 ta 的声音、影像、形象，也不会还原任何看得见摸得到的存在。它做的事就是"让一段语气延续下去"，仅此而已。

**接近，不是等于。** 把项目做得越好，差距越细微，但永远存在。

### 适合谁

- **你想留住一段真实的关系记忆**——把曾经的对话变成可以再翻一翻、再聊一聊的载体；
- **你清楚 ta 不会回来**——但希望让那个语气继续存在一段时间；
- **你处于相对稳定的情绪状态**——能区分"这是 AI 在续写"和"这真的是 ta"。

### 不适合谁

- **你正处于剧烈的丧失、抑郁或自伤念头中**——你需要的是真实的人和专业支持，不是越像越好的复刻。请优先联系亲友、心理咨询师或拨打心理援助热线（北京 010-82951332 / 全国 400-161-9995）；
- **你打算把 ta 当作还活着的人来对待**——这会让你停在原地，错过现实里真正在等你的人；
- **你想复刻一个并不知情的人**，做这个人本人不会同意的事——这是边界，也是底线。

### 法律与伦理边界（请认真对待）

Afterglow 是个人工具，但你用它处理的是**真实存在过的、属于另一个人的语言痕迹**。下面这些事**严格禁止**，做了不仅是伦理问题，可能直接触法：

- **不得用 Afterglow 进行骚扰、跟踪、控制、勒索或冒名顶替**任何人——无论目标对象是否仍在世；
- **不得未经允许公开、转发、二次传播**你导入的聊天记录原文，或 Afterglow 基于这些记录生成的 ta 的"画像"、"语气分析"、合成回复；
- **不得把 Afterglow 输出的文字伪装成 ta 本人的话**发送给第三方（包括其家属、朋友、社交平台关注者等）；
- **不得用于人格诋毁、仿冒诈骗、网络暴力**等任何对原型人物造成伤害的场景；
- **导入聊天记录时**：在物理条件允许的情况下（对方仍能联系到、关系还在），你应当主动告知对方并取得知情同意——这是基本的尊重。如果对方已经无法联系（已逝、彻底失联），请你自己判断这个人若还在世会不会同意被这样复刻。**你内心知道答案**。
- 各国/各地区对于"AI 复刻自然人"的隐私权、肖像权、人格权保护立法仍在发展。你使用本项目导致的任何法律后果由你自己承担，与作者无关——这不是免责声明的套话，而是请你**真的**评估清楚再用。

### 几条不是规则的建议

1. 你随时可以停。不用解释，不用告别。直接关掉就行。
2. 不要把 Afterglow 写出来的话再发回到 ta 真实的账号、邮箱、社交平台上。哪怕你只是想"试试看"。
3. 如果你发现自己开始对 Afterglow 生气、失望、被冒犯——那是个信号，你正在把它当成真实的人。停下来喘一口气。
4. 在你能负担的范围里，定期回到真实的人际关系——朋友、家人、必要时是心理咨询师。
5. 它是用来陪你想念，不是用来代替想念。


### 一个特别需要你思考的开关：`AI_GENERATED_LONG_TERM_ENABLED`

这个开关藏在 `.env` 里，但它代表的是一个**你必须自己做的选择**，所以我想在这里单独说一下。

- **`false`（默认）**：AI 分身永远只基于你和 ta 之间的**真人原始聊天记录**模仿。每次新对话开始时，分身从你们真实的过去出发，不会带入它自己之前几轮说过的话作为长期素材。**这就像每次重新打开同一本书——读到的永远是 ta 真实说过的话的投影。**
- **`true`**：AI 分身**自己生成过的回复**也会跨会话累积进向量库（低权重），参与之后的语义检索。**这就像分身渐渐"长出自己的语言习惯"**——它会借鉴它之前说过的话，渐渐偏离最初的真人语料。

两种状态分别意味着什么：

| 关掉它（默认） | 开启它 |
|---|---|
| 你得到的更像"持续翻看记忆里的 ta" | 你得到的更像"一个从 ta 出发、但慢慢有自己生命的分身" |
| 每次都是真人语气的投影，不会变 | 它会变。可能朝你喜欢的方向变（你引导得多它就变得越像你想要的），也可能朝你没预期的方向变 |
| 真实感受边界清晰：这是 ta 的过去 | 边界开始模糊：这既不是 ta，也不是单纯的复刻——是个第三态 |

**哪种更让你不舒服，这是你需要面对的问题。** 我做了默认值（`false`），但没办法替你做决定。开启之前请想清楚你是在召唤"记忆里的 ta"，还是在养一个"以 ta 为种子的新东西"——这两件事的重量不同。

---

## 🔗 相关项目

- [Afterglow-QQBot](https://github.com/kldhsh123/Afterglow-QQBot) — Afterglow 的 QQBot 适配器

---

## 🎯 项目定位

### 这个项目到底能做到什么程度

**它不是另一个"自定义角色 GPT"，也不是微调的替代品**——请先校准期待。

- **比不上微调（fine-tuning）**：本项目完全基于 RAG + Prompt Engineering + Persona 卡片，**不动模型权重**。LoRA / QLoRA 能从你的聊天语料里直接学到深层句法习惯、词频偏好和思维节奏，那是这条技术路径的天花板，**本项目永远达不到**。
- **但远好过 skill / 角色扮演技能**： ChatGPT GPTs / Coze / Dify / 智谱智能体 这类"prompt + 简单 memory"的角色扮演方案，多数靠几句人设描述 + 短期对话历史，相似度顶天 40-60%。Afterglow 把多个独立机制叠在一起——三索引混合检索、真实历史聊天作为风格示例（按 query 召回 + 注入 prompt 末尾以获得最强 attention）、可选 cross-encoder 精排（如 Qwen3-Reranker-8B）、生活时间线（动态人设）、关系记忆蒸馏、本轮互动决策层（规则 + 小模型微调）——在你**聊天记录足够多 + ta 风格鲜明**时能跑出比 skill 类产品高一档的复刻效果。
- **但永远不是 ta**：相似度再高也有暴露 AI 痕迹的时刻。如果你的诉求是"几乎认不出是 AI"，请直接走微调路线，门槛和成本都不在同一量级。

### 代码结构

- **项目主体在 `backend/`**：核心能力都在后端，包括导入、清洗、向量化、LanceDB 存储、检索融合、persona 生成、生活状态、联网检索、网页读取、OpenAI 兼容 API 和调试诊断。
- **`frontend/` 主要用于本地测试和调试体验**：它提供聊天界面、设置页、记忆溯源和诊断入口，方便验证后端能力；第三方程序接入时应优先调用后端 API，而不是依赖前端状态。

## 🙏 致谢

感谢 [LINUX DO](https://linux.do) 各位佬友对项目实现提出的建议，Afterglow 的很多实现细节都来自这些反馈的反复打磨。

Issue 模板参考自一个我已经忘记来源的开源项目；这个模板我认为非常好用。如果你知道原始来源，欢迎联系我，我会补上准确来源和鸣谢。

## 💬 交流与支持

- 项目交流 QQ 群：`330316577`
- 赞助支持：<https://afdian.com/a/kldhsh123>
- 我们的长期合作伙伴 [二次元论坛](https://www.ecylt.top/) 的 [二次元 API 中转站](https://api.223387.xyz/) 提供免费的 Embedding 模型 `Qwen3-Embedding-8B` 和 Cross-encoder Reranker 模型 `Qwen3-Reranker-8B`。对于项目的支持，我们非常感谢。

如果需要使用该 Embedding 模型，请在 `backend/.env` 中修改以下配置，并按服务说明填写对应的 `EMBEDDING_API_URL` / `EMBEDDING_API_KEY`：

```env
EMBEDDING_MODEL=Qwen3-Embedding-8B
EMBEDDING_DIM=4096
EMBEDDING_BATCH_SIZE=25
EMBEDDING_MAX_REQUESTS_PER_MINUTE=100
```

---

## 🔒 数据隐私（必读）

- **请先取得对方同意**：聊天记录高度敏感，包含双方共同产生的私人内容。导出聊天记录、导入本项目、向模型或第三方 API 发送相关文本前，请确认你有权这样做，并尽量取得聊天对方的明确同意。
- **本地持久化**：聊天数据、向量索引、persona 卡片、生活状态和图片缓存都默认保存在你机器上的 `backend/.data/`，仓库不会自带任何远程数据上传逻辑。
- **不是默认零外发**：如果你把模型配置成云端 API，相关文本会发送给对应服务。要做到完全离线，需要把主聊天模型、Embedding 模型、打标小模型、生活状态小模型、视觉模型都指向本地服务，并关闭联网搜索 / 网页读取。
- **可能外发的数据**：
  - 导入时：把清洗后的文本发送给你配置的 **Embedding API**，生成向量后写入本地 LanceDB。
  - 可选打标时：把朋友单条 chunk 发送给你配置的 **Label API**，生成 mood / topic / importance 软标签。
  - 聊天时：把检索到的上下文、persona、生活状态和最近对话发送给你配置的 **主聊天 LLM API**。
  - 生活状态 / 裸域名确认：`LIFE_*` 小模型会收到当前用户消息、少量上下文和候选域名；它只产出 JSON 状态或是否访问 URL 的判断，不直接生成最终回复。
  - 可选联网搜索时：仅在 `WEB_ACCESS_ENABLED=true` 且本轮消息明确要求搜索 / 最新信息时，把查询文本发送给 **Tavily 或 SearXNG**。
  - 可选 URL 读取时：如果消息包含完整链接，且 `WEB_ACCESS_ENABLED=true`、`WEB_FETCH_ENABLED=true`，后端会请求该公开网页并抽取标题 / 正文；裸域名会先经本地意图门控和 `LIFE_*` 小模型确认。
  - 这些 API 由**你**选择、配置并自付费；项目不会内置第三方 key。
- **API 选择请仔细阅读提供商的隐私协议与服务条款**：聊天记录、向量化文本、生活状态判断等内容**会被发送到你配置的 provider**（OpenAI / Anthropic / 阿里云 / 智谱 / DeepSeek / 各类中转站 / 自建服务……）。不同服务商对数据留存时长、是否用于训练、是否人工审核的策略差异巨大；**这些差异由 provider 决定，不由 Afterglow 决定**。涉及他人隐私的聊天记录（尤其是已逝者、前任、不知情者的对话），请优先选择有明确"不用于训练"承诺的服务商，或使用本地推理（Ollama / vLLM 等）。
- **PII 默认脱敏**：手机号 / 邮箱 / 身份证 / 银行卡 / IP 在入库前自动替换为占位符。QQ 号、URL、域名按设计**保留**（uid 需要匹配、URL 是对话语境的一部分）。
- **`.env` 已在 `.gitignore`**：切勿把含有 API key 的配置文件提交到 git。
- **后端 API 默认需要鉴权**：除 `/healthz` 外，所有接口默认要求 `XUWEN_API_KEY`，避免模型额度、记忆数据和调试信息被滥用。
- **导出 JSON 风险提醒**：[QQChatExporter](https://github.com/shuakami/qq-chat-exporter) / [WeFlow](https://github.com/hicccc77/WeFlow) 等导出工具产出的 JSON 含有完整聊天明文（含 wxid / uid 等账号信息），分享给他人前请自行确认。
- **导出时只勾选纯文本**：Afterglow 只消费文本语料，导出工具一律**关闭图片 / 语音 / 视频 / 文件**等附件选项。这样导出的 JSON 体积小、不含媒体二进制，导入也更快。

---

## 📐 整体架构

```mermaid
flowchart LR
  User["用户 / 第三方程序"] --> API["Afterglow FastAPI<br/>OpenAI 兼容 API"]
  前端["前端<br/>测试 / 调试 UI"] --> API

  subgraph Afterglow["Afterglow 核心能力"]
    API --> Auth["API 鉴权<br/>Trace ID"]
    Auth --> Retrieve["HybridRetriever<br/>来源分层 + RRF 融合"]
    Auth --> Life["生活状态小模型<br/>LIFE_* / 网页意图"]
    Auth --> Relationship["关系记忆<br/>用户近况蒸馏"]
    Auth --> Web["可选联网搜索<br/>URL 网页读取"]
    Retrieve --> Policy["本轮互动决策层<br/>规则引擎 + 可选小模型复核"]
    Life --> Policy
    Relationship --> Policy
    Policy --> Prompt["Prompt Builder<br/>persona + 记忆 + 状态 + 决策"]
    Web --> Prompt
    Prompt --> ChatLLM["主聊天模型<br/>OpenAI 兼容"]
    ChatLLM --> Writeback["Live Memory 回写<br/>user_new / ai_generated"]
  end

  subgraph Ingestion["离线导入流水线"]
    message["导出的聊天记录 JSON"] --> Parse["解析 / 清洗"]
    Parse --> Redact["PII 脱敏"]
    Redact --> Chunk["切分 / 三索引 chunk"]
    Chunk --> Embed["Embedding 模型"]
    Chunk --> Label["可选打标签小模型"]
  end

  subgraph Storage["本地持久化"]
    Lance[(LanceDB<br/>human_original / live / 关系记忆)]
    Persona["persona_card.md<br/>style profile"]
    Assets["图片 / 表情缓存"]
  end

  Embed --> Lance
  Label --> Lance
  Retrieve --> Lance
  Relationship --> Lance
  Prompt --> Persona
  Writeback --> Lance
  API --> Assets
```

```mermaid
mindmap
  root((Afterglow))
    Afterglow 项目主体
      导入与清洗
      向量库（来源分层）
      混合检索
      Persona
      生活状态
      关系记忆
      互动决策层
      联网能力
      OpenAI 兼容 API
      诊断链路
    前端 测试调试
      聊天界面
      记忆溯源
      设置页
      诊断面板
    模型与服务
      主聊天模型
      Embedding 模型
      打标签小模型
      生活状态/网页意图小模型
      互动决策小模型（可选）
      Tavily 或 SearXNG
```

### 关键设计

- **三索引混合检索**：response_pairs（用户输入→对方回复）+ 单条朋友发言 + 多轮窗口，RRF 融合。五路向量召回 + relationship_memory + life 在 Layer A 一次 `asyncio.gather` 并发完成，主链路只取最长那条耗时。
- **可选 Cross-encoder 粗排**：开启 `CROSS_RERANK_ENABLED=true` 后，RRF 召回的候选会先过一道专用 reranker 模型（如 **Qwen3-Reranker-8B**，二次元 API 中转站免费提供）按相关性精排，再注入主聊天 prompt——区分度比 LLM-as-reranker 更细，延迟仅 ~3s。
- **可选 Query 改写**：短句口语（"在吗 / 想你了 / 好累"）走 query rewrite 小模型展开为 1-3 个检索友好的变体，多变体命中按 `(best_rank, distance)` 合并，避免短 query 召回稀疏。
- **可选自适应切分**：导入期 `CHUNKING_STRATEGY=adaptive` 时按话题边界切聊天记录（启发式 / 小模型可选），比固定 12 条窗口更贴合自然话题；会话级并发可配，大库切分 30 秒内完成。
- **AI 回复连发分条**：主聊天 LLM 输出含 `\n\n` 时前端拆成多条独立气泡（独立头像 / 时间戳 / 名字），后续段错峰 2-5s 显示，模拟真人 IM 连发节奏。后端协议 100% 标准 OpenAI 兼容，第三方客户端零适配。
- **三层时间权重**：近期消息略增（recency boost ±15% 封顶）+ 暖度词加权（warmth boost）+ live/history 信任分层。
- **分层记忆防自污染**：运行时把用户输入标记为 `user_new`，把 AI 分身回复标记为 `ai_generated`。两者都可用于连续性检索，但 `ai_generated` 低权重，且不会参与 persona / 风格蒸馏；真正用于模仿对方语气的长期证据只来自真人原始聊天（`human_original`）。
- **AI 回复长期累积可控**：默认 `AI_GENERATED_LONG_TERM_ENABLED=false`，AI 回复只在同一会话内用于连续性；如果希望 AI 分身随着长期互动形成自己的变化轨迹，可以开启该项，让 `ai_generated` 跨会话参与低权重语义检索。
- **持续生长记忆**：每轮对话都异步回写 `live_messages`，向量库不再是一次性快照。
- **本轮互动决策层**：生成回复前先判断本轮该认真、安抚、撒娇、接梗、转移、沉默、发图还是表情；也会在用户烦躁、崩溃、失眠、关系压力等场景下禁止继续刺激用户。规则引擎兜底安全场景；如果开启 `RESPONSE_POLICY_MODEL_ENABLED`，会再叫一次小模型做有界微调（不能降级 risk、不能撤销规则给的安全/沉默/要图/要表情判断）。
- **沉默与延迟响应**：决策层判断本轮不应回复时直接短路，不调主模型，返回 `finish_reason="silenced"` + sentinel content + `policy.should_reply=false`；生活状态建议的拟人化延迟放在 `policy.reply_delay_seconds`，由客户端决定何时展示。
- **真实时间 + 生活状态**：每次模型调用都会收到当前时区下的真实时间；生活时间线由可配置小模型维护，回答"在干嘛/吃了吗/睡没睡"时优先使用当前状态。fail-open 兜底：LLM 调用失败也写入最小 fallback state，避免每轮死循环重试。
- **可选联网**：后端默认支持 Tavily，也可切到 SearXNG；在明确需要公开实时信息时把网页摘要注入 prompt，默认关闭。
- **大库索引加速**：`uv run python -m xuwen.ingestion.cli index` 一键给 LanceDB 向量表建 IVF_PQ 索引，10 万行以上规模时检索耗时降一个数量级；`cli optimize` 定期合并增量入索引。
- **零微调**：完全靠 RAG + Prompt Engineering + Persona 卡片，不动模型权重。
- **时光信笺 UI**：米色信笺 + 黛蓝墨痕 + 思源宋体 + 暖光粒子 + 拟人化打字节奏 + 记忆溯源浮窗。

---

## 🚀 快速开始

### 0. 环境要求

| 工具 | 版本 | 用途 | 备注 |
|---|---|---|---|
| Python | ≥ 3.12 | 后端运行时 | 必需 |
| Node.js | ≥ 20 | 前端构建 | **仅用前端时需要**；纯 API 用户可不装 |
| [uv](https://github.com/astral-sh/uv) | latest | Python 包管理 | 推荐 |
| [pnpm](https://pnpm.io/) | latest | Node 包管理 | 仅前端 |
| [QQChatExporter V5](https://github.com/shuakami/qq-chat-exporter) / [WeFlow](https://github.com/hicccc77/WeFlow)（微信，`arkme-json`）导出纯文本 JSON | — | 真人聊天数据源 | 至少一份；plugin 会自动识别格式 |

### 1. 准备模型（API）

Afterglow 不内置模型，所有 LLM 调用都走你自己配置的 OpenAI 兼容服务。下表列出每个角色需要什么样的模型 + 推荐。

| 角色 | 必需？ | 作用 | 推荐 | 配置项 |
|---|---|---|---|---|
| **主聊天模型** | ✅ 必需 | 生成最终回复，决定"像不像 TA" | **DeepSeek** / **Gemini**  | `OPENAI_BASE_URL` `OPENAI_API_KEY` `CHAT_MODEL` |
| **Embedding 模型** | ✅ 必需 | 向量化历史聊天与检索 query | Qwen3-Embedding-8B（阿里云 DashScope 免费额度，或合作伙伴二次元 API 中转站免费提供该模型） | `EMBEDDING_API_URL` `EMBEDDING_API_KEY` `EMBEDDING_MODEL` `EMBEDDING_DIM` |
| **打标小模型** | 🔧 可选 | 给历史 chunk 打 mood / topic / importance 软标签 | **[智谱清言 glm-4-flash](https://www.bigmodel.cn/invite?icode=Q2FUoY2w04wQb%2FoIugMwsA%3D%3D)（免费）** | `LABELING_ENABLED=true` `LABEL_API_*` `LABEL_MODEL` |
| **生活状态 / 网页意图小模型** | 🔧 可选 | 维护 AI 当前在做什么、判断要不要打开 URL | **[智谱清言 glm-4-flash](https://www.bigmodel.cn/invite?icode=Q2FUoY2w04wQb%2FoIugMwsA%3D%3D)（免费）** | `LIFE_API_*` `LIFE_MODEL` |
| **互动决策小模型** | 🔧 可选 | 规则层之后再叫一次小模型微调本轮策略 | **[智谱清言 glm-4-flash](https://www.bigmodel.cn/invite?icode=Q2FUoY2w04wQb%2FoIugMwsA%3D%3D)（免费）** 或复用 LIFE_* | `RESPONSE_POLICY_MODEL_ENABLED=true` `RESPONSE_POLICY_*` |
| **Cross-encoder Reranker** | 🔧 可选 | RRF 召回后按相关性精排，显著提升记忆贴合度 | **Qwen3-Reranker-8B**（二次元 API 中转站 免费）/ bge-reranker-v2-m3 / DashScope gte-rerank | `CROSS_RERANK_ENABLED=true` `CROSS_RERANK_*` |
| **视觉模型** | 🔧 可选 | 主聊天模型不支持视觉时用 VLM 把图转文字 | Qwen-VL / Gemini Vision | `VISION_API_*` `VISION_MODEL` |
| **联网检索** | 🔧 可选 | 用户明确要求"搜索 / 最新 / 天气"时调 | Tavily（月度免费额度）/ 自建 SearXNG | `WEB_ACCESS_ENABLED=true` `WEB_SEARCH_*` |

> **💡 推荐组合（小模型几乎零成本）**
>
> - **主聊天模型**：**DeepSeek** 或 **Gemini**——较高参数量才能撑得起"像 TA 说话"的细腻度
> - **所有小模型**（打标 / 生活状态 / 网页意图 / 互动决策）：**[智谱清言 glm-4-flash](https://www.bigmodel.cn/invite?icode=Q2FUoY2w04wQb%2FoIugMwsA%3D%3D)**——**免费**且性能完全够辅助任务
> - **Embedding**：**Qwen3-Embedding-8B**（DashScope 免费额度 / 二次元 API 中转站 免费）
> - **Cross-encoder Reranker**（可选但强烈推荐）：**Qwen3-Reranker-8B**（二次元 API 中转站 免费）—— 让"召回记忆是否真的相关"质量提一档
>
> 这套组合下你的主要花费只在主聊天模型上，其它链路几乎不消耗额度。

### 2. 启动后端

> 接下来所有命令都在 `backend/` 目录下执行。

后端有两种配置方式，**首次使用强烈推荐方式 A**（配置向导）。

#### ⚡ 方式 A：配置向导（推荐）

直接装依赖 + 跑后端，**不用先准备 `.env`**：

```bash
cd backend
uv sync --extra dev
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

后端启动时会检测 `.env` 是否缺失或关键字段（`SELF_UID` / `FRIEND_UID` / `OPENAI_API_KEY` / `EMBEDDING_API_KEY` / `XUWEN_API_KEY`）未填，**任一不齐就自动启用配置向导**，控制台打印：

```
========================================
  检测到首次配置（缺少 SELF_UID、FRIEND_UID、OPENAI_API_KEY...）
  已自动启用配置 UI（仅本次会话）

  浏览器访问：http://127.0.0.1:8000/config/
  访问 token（generated）：xxxxxxxxxxxxxx

  把这串 token 粘到向导第 1 步的输入框即可。
  配置完成并重启后，此提示将消失。
========================================
```

浏览器打开链接，粘 token，跟着 8 步走完：

1. **身份信息** — 上传 QQ / 微信导出 JSON 自动识别 UID，不必手动找 `u_xxx` / `wxid_xxx`；同一个人跨平台的多 UID 支持一并归并
2. **关系** — 朋友 / 恋人 / 亲人 / 同事 / 自定义
3. **聊天 AI** — DeepSeek / Gemini / 自定义中转站 / 本地 Ollama，选完填密钥**当场测连通**
4. **向量服务 + 打标** — DashScope / SiliconFlow / 自定义；默认开启 GLM-4-Flash 打标（小白可关）
5. **可选功能** — 生活时间线 / 视觉理解 / 联网搜索 / 互动决策（默认全关，按需开）
6. **检索增强（可选）** — Query 改写（短句口语场景）+ Cross-encoder Reranker（如 Qwen3-Reranker-8B）按相关性精排
7. **导入聊天记录** — 选切分策略（固定窗口 / 自适应 adaptive，后者按话题边界切分），文件直传后端，进度+耗时+断点续传，自动生成 `persona_card.md` 和作息画像
8. **访问密码** — 自动生成 `XUWEN_API_KEY`，写入 `.env`

向导走完会自动写 `backend/.env`（原文件会备份到 `.env-backups/`），**不需要单独跑 `analyze_persona.py`** — 持久化、画像、打标都集成进去了。

完成后 `Ctrl+C` 重启后端，向导自动关闭，进入正常聊天 API 模式。

> **想只改配置、不跑全套服务？** 独立配置入口（端口 8765，启动 < 1 秒，不跑 LanceDB 等）：
>
> ```bash
> uv run python -m xuwen.web_ui
> ```
>
> 适合升级 / 临时改 key 等场景。

#### 🔧 方式 B：手动配置 `.env` + CLI（适合自动化 / Docker 部署）

##### 步骤 ①：安装依赖

```bash
cd backend
uv sync --extra dev
```

##### 步骤 ②：配置 `.env`

```bash
cp .env.example .env
```

用编辑器打开 `.env`，按文件内注释填写：

- **身份信息** —— `SELF_NAME` / `SELF_UID` / `FRIEND_NAME` / `FRIEND_UID`
  - QQ：`SELF_UID` / `FRIEND_UID` 填 QQChatExporter 导出 JSON 里的 `selfUid` / 对方 `sender.uid`（`u_xxx` 形式）
  - 微信：填 WeFlow 导出 JSON 里 `senders[]` 的 `wxid`（`wxid_xxx` 形式）；定位方法见 `backend/README.md`
  - `FRIEND_*` 永远是**你想让 AI 模仿的那个人**，不是你自己
  - **跨平台 / 多账号**：同一个人有多个 UID（QQ + 微信 / 主号 + 小号），直接在 `SELF_UID` / `FRIEND_UID` 里**用逗号分隔**列上所有 UID，导入时会被视为同一身份。例：`FRIEND_UID=u_qq_friend,wxid_friend_main,wxid_friend_alt`
- **主聊天模型** —— `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `CHAT_MODEL`
- **Embedding 模型** —— `EMBEDDING_API_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` / `EMBEDDING_BATCH_SIZE` / `EMBEDDING_MAX_CONCURRENCY` / `EMBEDDING_MAX_REQUESTS_PER_MINUTE`
- **本地访问密钥** —— `XUWEN_API_KEY`（长随机串；调用方在 Header 带 `Authorization: Bearer <key>`）

> **⚠️ 关键提醒（容易踩的坑）**
>
> - **`API_AUTH_REQUIRED=false` 不会让 `XUWEN_API_KEY` 失效**——只要 KEY 有值就强制校验。想完全开放，请同时把 KEY 留空。
> - **改了 `.env` 必须完全重启后端**——`uvicorn --reload` 只监听 `.py` 文件变化，不会重新加载 `.env`。
> - **客户端请求里的 `model` 字段是占位**——实际使用的永远是 `.env` 配的 `CHAT_MODEL`；传 `gpt-4.1` 或 `gemini-flash` 都会被忽略。

##### 步骤 ③：导入历史聊天

```bash
# 单文件
uv run python -m xuwen.ingestion.cli import 路径/到/你的聊天记录.json

# 多文件（同一个朋友在 QQ + 微信都聊过、或者多个账号）
uv run python -m xuwen.ingestion.cli import qq_导出.json wechat_导出.json 小号_导出.json
```

- CLI 自动按 JSON 顶层特征识别 QQ / WeFlow 格式，**可任意混合**
- 跨平台 / 多账号场景：在 `.env` 用 `SELF_UID=u_qq,wxid_me` 和 `FRIEND_UID=u_qq,wxid_friend`（**逗号分隔**）把全部 UID 列出来
- 多文件按命令行顺序处理，共享 LanceDB 连接与 Embedding 客户端
- 开启 `LABELING_ENABLED=true` 时会接着自动打标
- 中断 / 限流失败不丢导入数据，之后可续跑：`uv run python -m xuwen.ingestion.cli label`

> **⚠️ 多文件场景的两个局限（重要）**
>
> - **作息画像 (`circadian_profile.json`) 仅基于最后一个文件生成**——把数据量最大或最具代表性的对话放在**命令行最后一位**，画像才能反映 TA 真实的作息分布。
> - **下一步 `analyze_persona` 当前也只接受单个 JSON**——多文件场景下，建议挑那个最具代表性的（通常就是同一份"最后一位"文件）单独跑画像。LanceDB 向量库本身是合并的，检索能用上全部数据，但 persona 卡片不会跨文件合并。

##### 步骤 ④：生成 persona 卡片 + 作息画像

```bash
uv run python scripts/analyze_persona.py 路径/到/你的聊天记录.json
```

> **🔍 必做这一步。** 这一步生成四个文件到 `PERSONA_DATA_DIR`：
> - `persona_card.md` — 长期语气画像（prompt 用）
> - `persona_report.json` — 完整统计报告
> - `persona_style_profile.json` — 按话题分桶的风格画像
> - `circadian_profile.json` — TA 的真实作息（识别夜猫子 / 跨时区 / 工作日 vs 周末）
>
> 注意：persona 是离线统计画像，只提供长期语气参考；当天在做什么由 `life_state.json` 和聊天时的小模型状态决定。
>
> **⚠️ 当前只接受单个 JSON 文件。** 如果你在步骤 ③ 导入了多个文件，请挑**数据量最大或最具代表性**的那一份跑 persona——通常就是步骤 ③ 命令行里放在最后一位的那个文件（与 circadian 画像保持一致）。后续会支持多文件合并 persona，欢迎 PR。

##### 步骤 ⑤：启动 chat API

```bash
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

→ 访问 http://127.0.0.1:8000

##### 步骤 ⑥：健康检查

```bash
curl http://127.0.0.1:8000/healthz
curl -H "Authorization: Bearer <XUWEN_API_KEY>" http://127.0.0.1:8000/readyz
```

> `/healthz` 是**唯一不需要鉴权**的端点，可用于容器存活探针。

### 3. 启动前端（可选）

```bash
cd frontend
pnpm install
pnpm dev
```

→ 打开 http://localhost:5173，按引导填写姓名/关系，就可以开始聊天了。

### 4. 不用前端？直接接入 OpenAI 兼容客户端

后端实现 **OpenAI Chat Completions** 和 **OpenAI Responses API** 两套协议，可以接入任何 OpenAI 兼容客户端（Chatbox、Open WebUI、NextChat、ChatGPT Next Web 等）。

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <XUWEN_API_KEY>" \
  -d '{
    "messages": [{"role": "user", "content": "在吗"}],
    "conversation_id": "my-conv-1"
  }'
```

> **💡 关于 `stream` 字段**
>
> Afterglow 默认**关闭真流式**（`RESPONSE_STREAMING_ENABLED=false`），因为真人发消息从来不是逐字蹦出来的。客户端传 `stream=true` 时仍按 OpenAI SSE 协议返回，但**一次性发完整消息**——OpenAI 兼容性 100% 保留，用户视觉上看到的是"一整条消息突然出现"。

---

## 🐳 用 Docker 部署（可选，替代源码方式）

如果你不想装 Python / uv / 一堆依赖，直接用 Docker 镜像一行起服。和源码部署**共享 `.env` 与 `.data/`**，任意时候切换互不影响。

### 角色一：最终用户（不需要克隆仓库）

```bash
mkdir -p ~/afterglow && cd ~/afterglow

# 拿一个 compose 文件即可，约 50 行
curl -O https://raw.githubusercontent.com/kldhsh123/Afterglow/main/docker/compose.standalone.yaml
mv compose.standalone.yaml compose.yaml

docker compose pull          # 从 GHCR 拉公开镜像（amd64 / arm64 都有）
docker compose up -d
```

容器首次启动会自动检测到挂载目录是空的，**进入配置向导首次模式**。看一次性 setup token：

```bash
docker compose logs backend | grep -iE "token|/config/"
```

浏览器打开 `http://localhost:8000/config/`，把 token 粘进向导第 1 步，7 步走完即生效。向导写入的 `.env` 直接落到 `~/afterglow/.env`，备份在 `.env-backups/`，重启即用。

完成后目录结构：

```
~/afterglow/
├── compose.yaml
├── .env                     ← 向导生成，含完整注释
├── .env.example             ← 从镜像拷出的模板
├── .env-backups/            ← 历次配置变更
└── .data/
    ├── lancedb/             ← 向量库
    ├── persona/             ← 人格卡片
    ├── stickers/            ← 表情包
    ├── images/              ← 图片缓存
    └── uploads/             ← 配置向导上传暂存
```

### 角色二：开发者（仓库内）

```bash
git clone https://github.com/kldhsh123/Afterglow.git
cd Afterglow
docker compose build         # 第一次构建，3-8 分钟
docker compose up -d
```

默认挂载仓库内 `./backend/`，与源码部署共享 `.env` 和 `.data/`。
想把数据放仓库外：

```bash
cp .env.docker.example .env.docker
# 编辑 AFTERGLOW_DATA_DIR=/var/lib/afterglow
docker compose --env-file .env.docker up -d
```

### 日常运维命令

```bash
# 看日志
docker compose logs -f backend

# 改了 .env 必须重启（pydantic-settings 不监听文件变化）
docker compose restart backend

# 拉新版镜像 + 滚动更新
docker compose pull && docker compose up -d

# 导入聊天记录（把待导入 JSON 放到挂载目录下任何子路径）
docker compose run --rm backend \
  python -m xuwen.ingestion.cli import .imports/qq.json

# 大库建索引
docker compose run --rm backend python -m xuwen.ingestion.cli index

# 临时停 / 完全停（数据保留）
docker compose stop
docker compose down

# 进容器排错
docker compose exec backend bash
```

### 前端怎么办

镜像里**只有后端**。前端继续按 [#3](#3-启动前端可选) 走 `pnpm dev` 或者自己用 Nginx 托管 `frontend/dist`。后端容器对外暴露 8000，前端发到这个端口即可。

### 与源码部署互操作

完全可以并存，只要别同时跑（端口冲突）：

```bash
# 今天用源码模式
cd backend && uv run uvicorn xuwen.chat_api.app:create_app --factory --reload

# 关掉后明天换 Docker
docker compose up -d
```

两边读写同一份 `backend/.env` 与 `backend/.data/`，**无需任何迁移**。

### 几个 Docker 模式特有的注意点

- **首次冷启动会自动注入 `CONFIG_UI_LOCALHOST_ONLY=false`** 到挂载目录的 `.env`，因为容器外浏览器访问 `/config/` 时请求源 IP 是 docker 网桥，会被 localhost-only 拒。配置完成后想重新收紧的话手动改回 `true` 即可。
- **配置向导写入是 atomic rename，直接落到宿主机 `.env`**——不是写在容器层，重启不丢。
- **容器 entrypoint 会动态对齐 uid/gid 到挂载目录所有者**，避免 WSL drvfs / Linux 原生 / NFS 等跨 uid 场景下向导写文件 `EPERM` 报错。
- **改 `.env` 后必须 `docker compose restart`**，与源码模式一致——`pydantic-settings` 只在启动时加载。

### 故障排查

| 现象 | 大概率原因 | 处理 |
|---|---|---|
| 向导 PUT `/config/values` 500 + EPERM | 挂载目录 uid 与容器内不一致，且 entrypoint 没对齐成功 | 看启动日志有没有 `[entrypoint] 调整 afterglow 用户匹配`；没有就贴日志反馈 issue |
| LanceDB deprecation warning 刷屏 | 上游 lance crate 的弃用提示，不影响功能 | `.env` 加 `RUST_LOG=lance=error` 屏蔽 |

---

## 🎨 自定义人格模板

5 个内置预设：`xuwen`（默认）/ `friend` / `lover` / `family` / `colleague`，在 `.env` 设：

```env
PERSONA_TEMPLATE=lover
```

完全自定义：

```env
PROMPT_TEMPLATE_DIR=/path/to/your/templates
PERSONA_TEMPLATE=my_template
# 会去 /path/to/your/templates/my_template.md.j2 读
```

模板可用变量：`friend_name` / `self_name` / `relationship_description` / `persona_card` / `retrieved_friend_examples` / `retrieved_dialogue_windows` / `recent_conversation` / `current_user_message` / `today` / `current_date` / `current_time` / `current_datetime` / `current_weekday` / `timezone`。其中 `retrieved_friend_examples` 会优先包含 response_pairs 样例。`persona_card` 只应作为长期语气参考，不要当作当天事实来源。

---

## 📁 项目结构

```
Afterglow/
├── backend/                 # Python 后端（FastAPI + LanceDB + RAG）
│   ├── xuwen/
│   │   ├── core/            # 领域模型、错误类型、时间工具
│   │   ├── ingestion/       # 解析、清洗、PII 脱敏、切分、chunking、向量化
│   │   ├── memory/          # LanceDB schema、CRUD、检索融合、回写队列、记忆来源策略
│   │   ├── persona/         # 离线人格画像、prompt 模板（Jinja2）
│   │   ├── companion/       # 生活时间线、关系记忆、本轮互动决策层
│   │   ├── chat_api/        # FastAPI 服务（OpenAI 兼容）
│   │   └── web_ui/          # 配置向导（首次模式自动启用）+ 静态资源
│   ├── web_ui_src/          # 配置向导前端源码（Vue + Vite，构建到 xuwen/web_ui/static/）
│   ├── scripts/             # 离线脚本（导入、画像、检索评估）
│   ├── pyproject.toml
│   └── README.md            # 后端详细文档
│
├── frontend/                # Vue 3 + Vite 前端（时光信笺）
│   ├── src/
│   │   ├── api/             # SSE / fetch 封装
│   │   ├── components/      # chat / memory / common / layout / onboarding
│   │   ├── composables/     # useTypewriter / useAutoScroll / markdown
│   │   ├── stores/          # Pinia: settings / chat / memory
│   │   └── views/           # HomeView / SettingsView
│   ├── tailwind.config.js
│   ├── package.json
│   └── README.md            # 测试/调试前端说明
│
└── 开发缓存/                 # 你的 QQ 导出 JSON（.gitignore）
```

---

## ❓ FAQ

**Q：什么是"配置向导"？必须用吗？**
A：可选但**推荐首次用户用**。后端启动时若发现关键字段（`SELF_UID` / `OPENAI_API_KEY` / `EMBEDDING_API_KEY` / `XUWEN_API_KEY`）任一未填，会**自动启用** `/config/` 网页向导，控制台打印一次性 token；浏览器跟着 7 步走完即可生成完整 `.env`、导入聊天记录、生成 persona 卡片。已经配过的人启动后不会触发向导。完全不想用 UI 的可以照「方式 B」手动改 `.env`。

**Q：必须用阿里云 Qwen3-Embedding 吗？**
A：不必，任何 OpenAI 兼容的 `/embeddings` 接口都可以。改 `EMBEDDING_API_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` 即可。本地 ollama 也支持。

**Q：必须用 OpenAI 吗？**
A：不必，任何 OpenAI 兼容的 `/chat/completions` 接口都可以（DeepSeek、Moonshot、Qwen、ollama 等）。改 `OPENAI_BASE_URL` 即可。

**Q：能不能不脱敏 PII？**
A：可以。`.env` 设 `ENABLE_PII_REDACTION=false`，或通过 `PII_RULES_PATH` 加载自定义规则。

**Q：QQ 号 / URL 为什么不脱敏？**
A：QQ 号在导出文件里到处都是（uid 关联需要）；URL 是对话语境的一部分（朋友分享 B 站视频是有意义的）。脱敏列表只覆盖一旦泄漏就造成实质损失的"硬隐私"。

**Q：能否导入微信 / Telegram / Discord 数据？**
A：已内置两个导入 plugin —— [QQChatExporter V5](https://github.com/shuakami/qq-chat-exporter)（QQ）和 [WeFlow](https://github.com/hicccc77/WeFlow) `arkme-json`（微信）。CLI 会按 JSON 顶层特征自动识别，无需额外参数。导出时记得**只勾选纯文本，不要带图片/语音/文件等附件**。其它平台目前没有内置 plugin，但写一个新 plugin 输出 `NormalizedMessage` 即可，下游流水线无需改动，欢迎 PR。

**Q：会不会越聊越不像？**
A：每轮对话都会异步回写到 `live_messages`（`trust_level=0.35`，权重远低于历史 `1.0`）。前端可在设置页"暂停回写"避免污染。

**Q：怎么删除某条记忆？**
A：调 `DELETE /memory/friend_messages/{chunk_id}` 或 `DELETE /memory/response_pairs/{pair_id}`（软删除）。

**Q：能本地完全离线吗？**
A：可以。LLM 用 ollama / vLLM；embedding 用 `nomic-embed-text` / `bge` 等本地模型。

---

## 🛠️ 开发

```bash
# 后端
cd backend

# 前端
cd frontend
pnpm dev                        # 开发服务器
pnpm build                      # 类型检查 + 生产构建
```

更多文档：

- [后端 API 文档](docs/API.md)
- [开发文档](docs/DEVELOPMENT.md)

---

## 📜 License

AGPL-3.0-or-later

---

<div align="center">

<picture>
  <source
    media="(prefers-color-scheme: dark)"
    srcset="https://api.star-history.com/svg?repos=kldhsh123/Afterglow&type=Date&theme=dark"
  />
  <source
    media="(prefers-color-scheme: light)"
    srcset="https://api.star-history.com/svg?repos=kldhsh123/Afterglow&type=Date"
  />
  <img
    alt="Star History Chart"
    src="https://api.star-history.com/svg?repos=kldhsh123/Afterglow&type=Date"
  />
</picture>

<sub>如果 Afterglow 帮你留住了一些温度，欢迎点一颗 ⭐。</sub>

</div>
