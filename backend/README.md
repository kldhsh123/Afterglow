# Afterglow（续温）后端

> 把曾经对你好的话，续成往后的陪伴。

基于真实聊天记录的 RAG（检索增强生成）陪伴系统后端服务，使用 FastAPI + LanceDB + OpenAI 兼容 Embedding API 实现。

后端是 Afterglow 的项目主体：导入、清洗、向量化、LanceDB 存储、检索融合、persona、
生活状态、联网检索、网页读取、OpenAI 兼容 API 和诊断链路都在这里实现。`frontend/`
主要用于本地测试、调试和体验这些后端能力。

## 🔒 数据隐私（重要）

- **请先取得对方同意**：聊天记录高度敏感，包含双方共同产生的私人内容。导出聊天记录、导入本项目、向模型或第三方 API 发送相关文本前，请确认你有权这样做，并尽量取得聊天对方的明确同意。
- **本地持久化**：聊天数据、向量索引、persona 卡片、生活状态和图片缓存默认存储在本机 `.data/`。项目不会自带远程上传逻辑。
- **不是默认零外发**：如果你配置云端模型/API，相关文本会发送给这些服务。要做到完全离线，需要把主聊天、Embedding、打标、生活状态、视觉等模型全部指向本地服务，并关闭联网搜索 / 网页读取。
- **可能外发的数据**：导入阶段会把文本发送给你配置的 Embedding API；开启语义打标会把朋友单条 chunk 发送给 Label API；聊天阶段会把检索上下文发送给主 LLM API；生活状态 / 裸域名确认会调用 `LIFE_*` 小模型；开启联网检索会把搜索查询发送给 Tavily 或 SearXNG；开启 URL 读取会请求用户给出的公开网页。这些接口由你选用并自付费。
- **PII 默认脱敏**：手机号 / 邮箱 / 身份证 / 银行卡 / IP 在入库前自动替换为占位符；**QQ 号、URL、域名按设计保留**，因为模型需要 uid 匹配且 URL 是对话语境的一部分。如果你打算分享导出 JSON 或分享 `.data` 目录，请自行检查残留信息。
- **`.env` 切勿提交**：仓库 `.gitignore` 已忽略 `backend/.env` 和 `backend/.data/`。
- **API 默认需要鉴权**：除 `/healthz` 外，所有后端接口默认都要求 `XUWEN_API_KEY`，避免模型额度、记忆数据和调试信息被滥用。

## 部署需要准备什么

必需：

- **主聊天模型**：OpenAI 兼容 `/chat/completions`，负责最终回复。配置 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`CHAT_MODEL`。
- **Embedding / 向量模型**：OpenAI 兼容 `/embeddings`，负责历史导入、检索和 live memory 回写。配置 `EMBEDDING_API_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_DIM`，需要时可用 `EMBEDDING_MAX_CONCURRENCY` / `EMBEDDING_MAX_REQUESTS_PER_MINUTE` 控制导入请求压力。
- **本地磁盘目录**：默认 `.data/lancedb`、`.data/persona`、`.data/images`。

可选：

- **打标签小模型**：OpenAI 兼容 `/chat/completions`。开启 `LABELING_ENABLED=true` 后用于 mood / topic / importance，首次导入后会同步跑，也可用 `xuwen label` 续跑。
- **生活状态 / 网页意图小模型**：OpenAI 兼容 `/chat/completions`。通过 `LIFE_API_URL`、`LIFE_API_KEY`、`LIFE_MODEL` 配置；维护 AI 当前生活状态，也在用户只写裸域名时确认是否访问网页。这两项可以共用一个便宜小模型；留空则复用主聊天模型。
- **联网搜索服务**：Tavily 或自建 SearXNG。只有开启 `WEB_ACCESS_ENABLED=true` 且用户明确要求实时公开信息时才会调用。
- **视觉模型**：主聊天模型不支持图片但你想收图时配置 `VISION_API_URL`、`VISION_API_KEY`、`VISION_MODEL`。

## 快速开始

有两种配置方式，**首次使用强烈推荐方式 A**（配置向导）。

### 方式 A：配置向导（推荐）

直接装依赖跑起来，**不需要先准备 `.env`**：

```bash
cd backend
uv sync --extra dev
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

后端启动时检测到关键配置不全（`SELF_UID` / `FRIEND_UID` / `OPENAI_API_KEY` / `EMBEDDING_API_KEY` / `XUWEN_API_KEY` 任一缺失），会**自动启用配置向导**，控制台打印：

```
========================================
  检测到首次配置（缺少 ...）
  已自动启用配置 UI（仅本次会话）

  浏览器访问：http://127.0.0.1:8000/config/
  访问 token（generated）：xxxxxxxxxxxx
========================================
```

浏览器打开链接 + 粘 token，跟着 7 步走完（身份 → 关系 → 聊天 AI → 向量服务+打标 → 可选功能 → 导入聊天记录 → 设置 `XUWEN_API_KEY`）。向导自动完成 `.env` 写入、聊天记录导入、persona 卡片 + 作息画像生成。

完成后 `Ctrl+C` 重启后端，向导自动关闭，进入正常聊天 API 服务模式。

**单独跑配置 UI**（不启动主服务、不连 LanceDB，启动 < 1 秒）：

```bash
uv run python -m xuwen.web_ui            # 默认 127.0.0.1:8765
uv run python -m xuwen.web_ui --port 9000
```

适合升级后改配置 / 临时改 key 的场景。

如果想修改配置向导本身，源码在 `backend/web_ui_src/`，构建产物在 `backend/xuwen/web_ui/static/`：

```bash
cd backend/web_ui_src
npm install
npm run dev    # 开发模式 http://localhost:5174
npm run build  # 重新生成 ../xuwen/web_ui/static/
```

构建产物随 git 提交，普通用户无需 Node.js。

### 方式 B：手动配置 `.env` + CLI

适合 Docker / CI / 完全脚本化的场景。

```bash
# 1. 安装依赖（使用 uv）
cd backend
uv sync --extra dev

# 2. 配置环境变量
cp .env.example .env
# 用编辑器打开 .env，按文件内注释填写：
#   - SELF_NAME / SELF_UID：你自己的名字和账号 UID（QQ 是 u_xxx，微信是 wxid_xxx）
#   - FRIEND_NAME / FRIEND_UID：你想让 AI 模仿的那个人的名字和账号 UID（同上格式）
#   - OPENAI_API_KEY / EMBEDDING_API_KEY：主聊天模型和 Embedding 模型的 API key
#   - XUWEN_API_KEY：访问后端 API 的本地密钥，建议使用长随机字符串
# 如何获取 SELF_UID / FRIEND_UID 见下方"如何找到 UID"。
#
# 可选：Embedding 导入限流。最大并发限制同时在飞的 HTTP 请求数；
# 每分钟请求数按 HTTP 请求计，不按 batch 内文本条数计。0 表示不主动限速。
#   EMBEDDING_MAX_CONCURRENCY=4
#   EMBEDDING_MAX_REQUESTS_PER_MINUTE=0
#
# 可选：生活时间线 / 网页意图小模型。留空会复用主 LLM；单独配置可用更便宜的小模型。
# 它维护 AI 的当前生活状态、下一次更新时间和回复延迟；
# 当用户只写裸域名时，也用于确认是否真的要访问该网站。
# 主模型每次调用都会收到 APP_TIMEZONE 对应的真实当前时间。
#   APP_TIMEZONE=Asia/Shanghai
#   LIFE_API_URL=
#   LIFE_API_KEY=
#   LIFE_MODEL=glm-4-flash
#   LIFE_UPDATE_INTERVAL_MINUTES=60 # 最长每小时让小模型重新判断当前状态
#   LIFE_MAX_REPLY_DELAY_SECONDS=45
#
# 可选：联网检索。默认关闭；默认 provider 是 Tavily（有月度免费额度）。
# 只有明确“查一下/搜索/最新/新闻/天气/价格”等请求才会触发。
#   WEB_ACCESS_ENABLED=true
#   WEB_SEARCH_PROVIDER=tavily
#   WEB_SEARCH_API_KEY=tvly-...
#   WEB_SEARCH_MAX_RESULTS=5
#   WEB_FETCH_ENABLED=true
#
# 可选：开启语义打标（mood / topic / importance）
#   LABELING_ENABLED=true
#   LABEL_API_KEY=你的智谱或其它 OpenAI 兼容 key
#   LABEL_MODEL=glm-4-flash
#   LABEL_BATCH_SIZE=8
#   LABEL_MAX_CONCURRENCY=19       # 账号并发上限 20 时建议先设 19
#   LABEL_REQUEST_INTERVAL_SECONDS=0 # 遇到 429 再调成 0.2 / 0.5 / 1
# 首次导入会在向量入库后同步打标并显示进度；中断或限流失败后可用 cli label 续跑。

# 3. 跑测试（可选，确认依赖安装无误）
uv run pytest

# 4. 导入历史聊天记录
uv run python -m xuwen.ingestion.cli import 路径/到/你的_导出.json
# 多文件批量导入（QQ + 微信 / 多账号场景）：
# uv run python -m xuwen.ingestion.cli import qq_导出.json wechat_导出.json 小号_导出.json
# 自动识别格式：QQChatExporter V5（chatInfo.selfUid）/ WeFlow 微信（weflow.format=arkme-json）。
# 也可显式指定：--plugin qqexporter_v5 或 --plugin wechat_weflow。
# 导出时请**只勾选纯文本**，不要带图片/语音/视频/文件等附件——Afterglow 只用文本语料。
# 多文件场景下：
#   - circadian_profile.json 仅基于最后一个文件生成（把最近 / 最具代表性的对话放最后）
#   - scripts/analyze_persona.py 当前也只接受单个 JSON，挑代表性最强的一份单独跑
# 若已开启 LABELING_ENABLED=true，导入完成后会继续跑打标阶段。
# 未打标 chunk 仍正常参与向量召回，只是不享受后续标签加权。

# 4b. 手动续跑打标（可选，仅 LABELING_ENABLED=true 时需要）
# uv run python -m xuwen.ingestion.cli label

# 5. 查看向量库统计
uv run python -m xuwen.ingestion.cli stats

# 6. 生成 persona 卡片与场景风格画像（建议做，否则 prompt 缺画像，回答会偏通用）
#    persona 是离线统计画像，只提供长期语气参考；当天状态由 life_state.json 决定。
#    会生成 persona_card.md / persona_report.json / persona_style_profile.json。
uv run python scripts/analyze_persona.py 路径/到/你的聊天记录.json

# 7. 启动 chat API（OpenAI 兼容）
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
# → http://127.0.0.1:8000
# 端点：
#   POST /v1/chat/completions   OpenAI 兼容（支持流式 + 非流式）
#   GET  /healthz               存活检查（唯一默认免鉴权端点）
#   GET  /readyz /info          需要 XUWEN_API_KEY
#   GET  /memory/stats
#   POST /memory/search                 调试用，直接看检索结果
#   POST /memory/writeback/{pause,resume}
#   DELETE /memory/{table}/{id}         软删除某条记忆

# 8. （可选）检索质量自检
uv run python scripts/eval_retrieval.py             # 健康自检
uv run python scripts/eval_retrieval.py --eval dataset.jsonl  # 带 ground truth 评估
```

## 如何找到 UID

Afterglow 用 `SELF_UID` / `FRIEND_UID` 在导入时区分"哪条消息是你说的、哪条是对方说的"。
**`FRIEND_*` 永远填你想让 AI 模仿的那个人，不是你自己。**
两个 ID 都直接写进 `.env`，无需引号。

> **跨平台 / 多账号**：同一个朋友可能既在 QQ 又在微信上聊过，或者你自己有多个账号。
> 这种情况下**直接在 `SELF_UID` / `FRIEND_UID` 里用逗号分隔**列出所有 UID：
>
> ```env
> SELF_UID=u_qq_main,wxid_me_main,wxid_me_alt
> FRIEND_UID=wxid_friend_main,u_friend_qq,wxid_friend_alt
> ```
>
> CLI 会把逗号分隔的 UID 全部视为同一个人。
> （历史上还有一对兼容字段 `SELF_UIDS` / `FRIEND_UIDS`，效果完全等价，新配置无需用到。）

> **导出前先确认：只勾选纯文本**。Afterglow 不消费图片 / 语音 / 视频 / 文件，导出工具里
> 这些选项请全部关掉——JSON 更小、导入更快、也更不容易意外泄漏附件链接。

### QQ（QQChatExporter V5）

导出的 JSON 顶部有 `chatInfo` 字段：

```json
{
  "chatInfo": {
    "name": "对方备注/昵称",
    "type": "private",
    "selfUid": "u_xxxxxxx",   // ← 这是你的 SELF_UID
    "selfUin": "1111111111",
    "selfName": "你的昵称"
  },
  "messages": [
    {
      "sender": {
        "uid": "u_yyyyyyy",    // ← 这里非 selfUid 的就是 FRIEND_UID
        "name": "对方"
      },
      ...
    }
  ]
}
```

把 `selfUid` 填到 `SELF_UID`，第一条 `sender.uid` 中非 self 的填到 `FRIEND_UID`。
`SELF_NAME` / `FRIEND_NAME` 用易读的名字即可。

### 微信（WeFlow `arkme-json`）

> **微信导入提醒**：WeFlow 是 Afterglow 所支持的微信导入适配器，Afterglow 的默认微信导入插件依赖此项目。
> 我注意到 WeFlow 不再开源，所以我无法保证 WeFlow 将来的安全性。
> 所以在不久的将来我需要 WeFlow 的替代方案来确保用户隐私安全。
> 在此期间，我不会建议使用 WeFlow，但这是目前唯一可用的方案。

WeFlow 导出 JSON 顶部有 `weflow.format = "arkme-json"`，结构如下：

```json
{
  "weflow": {"format": "arkme-json", ...},
  "senders": [
    {"senderID": 1, "wxid": "wxid_xxx", "displayName": "对方名称"},
    {"senderID": 2, "wxid": "wxid_yyy", "displayName": "你的名称"}
  ],
  "messages": [
    {"isSend": 1, "senderID": 2, ...},   // ← isSend=1 的 senderID 指向"你自己"
    {"isSend": 0, "senderID": 1, ...}    // ← isSend=0 的 senderID 指向"对方"
  ]
}
```

定位步骤：

1. 在 `messages` 里随便找一条 `"isSend": 1` 的消息，记下它的 `senderID`。
2. 回到 `senders` 数组，按这个 `senderID` 找到对应那一项 —— 它的 `wxid` 就是你的 `SELF_UID`，`displayName` 可作为 `SELF_NAME`。
3. `senders` 里另外那个人就是 `FRIEND_*`。

以上面的 JSON 为例（`isSend=1` 对应 `senderID=2`）：

```env
SELF_NAME=开朗的火山河123
SELF_UID=wxid_yyy
FRIEND_NAME=MC
FRIEND_UID=wxid_xxx
```

> **群聊提醒**：当前 WeFlow plugin 在 `wxid` 都不匹配时会按 `isSend` 字段兜底（`1` → self、`0` → friend）。
> 这只在私聊里语义正确；群聊有多人发言，**必须**显式填 `FRIEND_UID` 才能正确区分。

> **快速取值（命令行）**：
> ```bash
> # 看 senders 列表
> grep -A2 '"senderID"' 你的.json | grep -E 'wxid|displayName' | head
> # 看你的 senderID（isSend=1 的）
> grep -B1 '"isSend": 1' 你的.json | grep senderID | head -3
> ```

## 项目结构

```
xuwen/
├── core/        # 领域模型、错误类型、时间工具
├── ingestion/   # JSON 解析、清洗、PII 脱敏、切分、chunking、向量化
├── memory/      # LanceDB schema、CRUD、检索融合、回写
├── persona/     # 离线人格画像、prompt 模板（Jinja2）、PII 规则
├── companion/   # 生活时间线、关系记忆、本轮互动决策层
├── chat_api/    # FastAPI 服务（OpenAI 兼容）
└── web_ui/      # 配置向导子应用 + 构建后的静态资源（首次模式自动挂载）

web_ui_src/      # 配置向导前端源码（Vue + Vite），构建到 xuwen/web_ui/static/
scripts/         # 离线脚本：analyze_persona、eval_retrieval、import_history
tests/           # 单元 + 集成测试
```

## 设计文档

常用文档：

- 后端 API 文档：`../docs/API.md`
- 开发文档与导入插件说明：`../docs/DEVELOPMENT.md`

## 常见故障

### Embedding 400：`No schema matches`

通常是上游不接受 OpenAI 标准的 `input: string[]`，或不接受 `encoding_format`。

```env
EMBEDDING_INPUT_MODE=single
EMBEDDING_INCLUDE_ENCODING_FORMAT=false
```

后端会在错误日志里打印脱敏后的请求体摘要，方便确认实际发送格式。

### Embedding 429：上游限流

导入默认最多同时发起 4 个 embedding 请求；如果上游 RPM 更低，可以降低并发并设置每分钟请求数：

```env
EMBEDDING_MAX_CONCURRENCY=1
EMBEDDING_MAX_REQUESTS_PER_MINUTE=60
```

`EMBEDDING_MAX_REQUESTS_PER_MINUTE` 统计的是 HTTP 请求次数；一个请求里包含多少条文本由 `EMBEDDING_BATCH_SIZE` 决定。

### LanceDB 写库报 `Spill has sent an error`

这是大批量 `merge_insert` 时的本地临时 IO / 内存压力问题。导入是幂等的，可以降低单批写入后重跑：

```env
LANCE_UPSERT_BATCH_SIZE=64
# 仍失败再降到 32
```

### 首次打标太慢或被限流

首次导入会先完成向量入库，再同步打标。中断不会丢库，之后继续跑：

```bash
uv run python -m xuwen.ingestion.cli label
```

如果账号并发上限是 20，建议：

```env
LABEL_MAX_CONCURRENCY=19
LABEL_REQUEST_INTERVAL_SECONDS=0
```

遇到 429 再把间隔调成 `0.2` / `0.5` / `1`。

### 联网检索没有内容

确认：
- `WEB_ACCESS_ENABLED=true`
- 默认 Tavily 需要 `WEB_SEARCH_PROVIDER=tavily` 和 `WEB_SEARCH_API_KEY`
- 如果使用自建 SearXNG，设置 `WEB_SEARCH_PROVIDER=searxng`，并让 `WEB_SEARCH_BASE_URL` 指向可访问实例
- 用户消息包含明确搜索意图，例如“查一下”“搜索”“最新”“新闻”“天气”“价格”

普通聊天不会自动联网，避免把私人聊天内容无谓发给搜索服务。

### URL 网页读取没有内容

确认：
- `WEB_ACCESS_ENABLED=true`
- `WEB_FETCH_ENABLED=true`
- 用户消息里包含完整的 `http://` / `https://` 链接，或裸域名（如 `example.com`）
- 目标页面是普通文本/HTML 页面，不是需要浏览器执行 JavaScript 才能看到内容的应用

裸域名不会无条件访问。后端会先用本地规则判断“用户是否有打开/看看/这个网站是什么”等访问意图，命中后再调用 `LIFE_*` 小模型确认候选 URL。

后端会拒绝本机、内网、链路本地等地址，并限制跳转次数、响应大小和注入 prompt 的字符数。诊断里看 `/debug/stats` 的 `calls.web.intent`、`calls.web.fetch` 和 `calls.web.fetch.skipped`。

### 调试完整链路

本地访问：

```bash
curl -H "Authorization: Bearer <XUWEN_API_KEY>" http://127.0.0.1:8000/debug/stats
curl -H "Authorization: Bearer <XUWEN_API_KEY>" http://127.0.0.1:8000/debug/config
```

`/debug/stats` 包含数据库性能、模型请求链路、生活状态决策、联网检索指标和 trace id。对外部署时请设置 `XUWEN_API_KEY`，必要时关闭 `DEBUG_ENDPOINTS_ENABLED`。

## License

AGPL-3.0-or-later（继承仓库根 LICENSE）。
