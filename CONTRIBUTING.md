# 贡献指南

感谢你考虑为 Afterglow 出力。这个文档帮你最快地把改动以一种**容易被合并**的形式提上来。

如果只是有个建议或发现 bug，**不需要读完整篇** —— 直接去 [Issues](https://github.com/kldhsh123/Afterglow/issues) 用对应模板开一个就行。

## 在动手之前

- 项目主要是中文用户，文档、Issue、PR、commit、注释**优先中文**；变量名 / 标识符仍用英文。Ruff 已经把全角标点告警关掉。
- 不要在 Issue / PR 里贴**真实聊天记录**或 API key。所有截图和日志请先脱敏，特别是 `u_xxx` / `wxid_xxx` / `sk-xxx` / `tvly-xxx` 这种东西。
- 项目在 `dev` 分支推进，发布前 squash 到 `main`。**请把 PR 提到 `dev`**。
- 如果是大改动（新增模型适配、改检索策略、动 prompt 模板等），**建议先开 Issue 讨论**，避免实现完发现方向不被接受。

## 找事做

| 想做什么 | 入口 |
|---|---|
| 修小 bug / 改文档错别字 | 直接发 PR |
| 新增导入插件（其它聊天平台） | 看 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) 的 plugin 章节 |
| 改进配置向导 UI | 改 `backend/web_ui_src/`，详见 [`backend/web_ui_src/README.md`](backend/web_ui_src/README.md) |
| 改主聊天前端（时光信笺） | 改 `frontend/`，详见 [`frontend/README.md`](frontend/README.md) |
| 新增 / 改 prompt 模板 | `backend/xuwen/persona/templates/*.md.j2` |
| 调检索 / 决策策略 | `backend/xuwen/memory/` 和 `backend/xuwen/companion/` |

## 开发环境

最快上手：

```bash
git clone https://github.com/<your-fork>/Afterglow.git
cd Afterglow
git remote add upstream https://github.com/kldhsh123/Afterglow.git

# 后端
cd backend
uv sync --extra dev

# 主聊天前端（可选，跑 web_ui 不需要）
cd ../frontend
pnpm install

# 配置 UI 源码（改向导才需要，WSL 上建议用 npm）
cd ../backend/web_ui_src
npm install
```

更详细的环境配置 / 模型选型 / 数据导入流程见根 [`README.md`](README.md) 和 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)，本文不重复。

## 项目结构（贡献者视角）

```
Afterglow/
├── backend/
│   ├── xuwen/
│   │   ├── core/        # 领域模型 / 错误 / 时间工具
│   │   ├── ingestion/   # 解析 / 清洗 / 切分 / 向量化 / plugin 注册
│   │   ├── memory/      # LanceDB / 检索融合 / 回写
│   │   ├── persona/     # 离线画像 / Prompt 模板（Jinja2）
│   │   ├── companion/   # 生活时间线 / 关系记忆 / 互动决策
│   │   ├── chat_api/    # FastAPI（OpenAI 兼容）
│   │   └── web_ui/      # 配置向导子应用 + 构建产物
│   ├── web_ui_src/      # 配置向导前端源码（构建到 ../xuwen/web_ui/static/）
│   ├── scripts/         # 离线脚本（import / analyze_persona / eval_retrieval）
│   └── tests/           # pytest 套件，分 unit / integration
├── frontend/            # Vue 3 时光信笺主聊天 UI（测试调试用）
├── docs/                # API.md / DEVELOPMENT.md
└── .github/             # Issue 模板 / FUNDING
```

## 工作流

1. **Fork + 克隆**，给你的分支起个含义清楚的名字：
   - `feat/import-discord` / `fix/embedding-dim-mismatch` / `docs/api-cleanup`
   - 不接受 `patch-1` / `update` / `temp` 这种名字
2. **每个 PR 聚焦一件事**。"顺手改了下别的 bug"请拆成两个 PR。
3. **提交前自检**（见下方"PR 提交清单"）。
4. **PR 标题用中文也行，但要点明范围**：
   - 好：`feat(ingestion): 支持 Discord 导出格式`
   - 不好：`update`、`修了几个 bug`
5. **目标分支 `dev`，不是 `main`**。

## 代码规范

### Python（后端）

- Python 3.12+
- 用 `ruff` 做 lint，配置在 `backend/pyproject.toml`
- 用 `mypy` 做严格类型检查（`strict = true`）
- 优先用 `from __future__ import annotations`
- 类型注解必须写，特别是公共函数 / 模型 / 配置

```bash
cd backend
uv run ruff check xuwen tests
uv run ruff format xuwen tests   # 可选，本项目不强制 format
uv run mypy xuwen
```

新加的 `FastAPI` 路由如果有 `Depends() / File()` 默认参数触发 B008，把对应文件名加进 `pyproject.toml` 的 `[tool.ruff.lint.per-file-ignores]`，**不要在代码里加 `# noqa`**。

### TypeScript（前端 / 配置 UI）

- 主聊天前端在 `frontend/`，配置向导在 `backend/web_ui_src/`
- 都用 `vue-tsc` 做类型检查
- 没 ESLint，但 PR 里如果引入明显的类型逃逸（`any` 满天飞、`as unknown as`）会被指出

```bash
cd frontend && npx vue-tsc --noEmit
cd backend/web_ui_src && npx vue-tsc --noEmit
```

### 注释 / 文档语言

- **中文为主**。仓库本来就是中文项目，PR 里贴英文注释会显得突兀。
- 不要写 trivial 注释（`# 这里加 1` 之类）。要写就写"为什么这样"。
- 函数 docstring 解释**意图**，不解释**做了什么**（代码自己会说）。

### 测试

新功能或者修复BUG必须在本地测试可用并不会影响到其他功能后提交。


## 项目特殊约定（容易踩的坑）

### 1. `.env` 永远不能提交

- 仓库 `.gitignore` 已经忽略 `backend/.env` 和 `backend/.data/`
- PR 里看到 `.env` 改动会立刻被拒
- 配置向导生成的 `.env` 也别 commit
- 备份目录 `backend/.env-backups/` 同样别 commit

### 2. 配置向导构建产物必须随源码一起提交

`backend/xuwen/web_ui/static/` 是 `backend/web_ui_src/` 的构建产物。**两边要同步提交**，否则普通用户拉下来跑后端会看到旧 UI 但 API 已经升级，行为对不上。

```bash
cd backend/web_ui_src
npm install
npm run build      # 生成 ../xuwen/web_ui/static/
cd ../..
git add backend/web_ui_src/ backend/xuwen/web_ui/static/
git commit -m "feat(config-ui): ..."
```

没有 CI 强制校验，**靠贡献者自觉**。Code review 时也请检查 PR 是否同时更新了 `static/`。

### 3. 不要把真实聊天文件提交到仓库

`开发缓存/` 目录已经在 `.gitignore`，放你自己的导出 JSON 用。**不要**把它放到任何其它位置，更不要在 PR 描述里贴片段。

### 4. CLI 行为不能被破坏

后端有两条入口：

- 配置向导（`uvicorn ...` + 首次模式 + 浏览器 UI）
- CLI（`uv run python -m xuwen.ingestion.cli import ...`）

改 `importer.py` / `parser.py` / `cli.py` 时，请确认两条路径都还能跑。`pytest tests/integration/test_import_pipeline.py` 是底层 CLI 流程的回归保护。

### 5. 隐私数据脱敏不要回退

`backend/xuwen/persona/redactor.py` 的 PII 规则有明确边界：手机号 / 邮箱 / 身份证 / 银行卡 / IP 一定脱敏，QQ 号 / URL / 域名按设计保留。如果要改这套边界，**先开 Issue 讨论**，因为它直接关系到用户数据被发到外部 API 的内容。

## Commit message

不强制 Conventional Commits，但推荐：

```
feat(ingestion): 支持 Discord 导出格式

- 新增 discord_export plugin，自动识别 webhook 导出 JSON
- 复用 NormalizedMessage 接口，下游流水线无需改动

Closes #123
```

- 前缀类型：`feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf`
- 范围（括号里）选填，常见值：`ingestion` / `memory` / `chat-api` / `config-ui` / `frontend` / `persona` / `companion` / `docs`
- 标题不超过 70 字符，详情写在正文
- 一个 commit 干一件事；不要"修复 A 顺便重构 B 又加了 C 的测试"

## PR 提交清单

发 PR 前请逐项过一遍：

- [ ] 目标分支是 `dev`
- [ ] `uv run ruff check xuwen tests` 干净
- [ ] `uv run mypy xuwen` 干净（或只剩跟改动无关的旧告警）
- [ ] 改了 `backend/web_ui_src/` 也 `npm run build` 并把 `static/` 一起提交
- [ ] 改了 `frontend/` 跑过 `npx vue-tsc --noEmit`
- [ ] 没有 `.env` / `.data/` / 真实聊天 JSON 跟着进 PR
- [ ] 新功能 / bug fix 带了对应的测试
- [ ] 标题 + 描述讲清楚"做了什么"和"为什么"
- [ ] 如果对应一个 Issue，PR 里写 `Closes #N`

## Code review 期望

- 维护者会在工作日内给反馈，**但请有耐心**（这是个个人维护的项目，不是大公司组件）
- review 评论可能直接（不绕弯），不针对人；如果有争议欢迎讨论
- 通过后维护者会 squash merge 进 `dev`

## 行为准则

- 对其他贡献者保持基本尊重
- 不接受涉及人身攻击 / 性别 / 政治 / 民族 / 性取向的言论
- 与项目无关的话题请去项目 QQ 群（`330316577`）
- 严重违规会直接 ban，不会有第二次警告

## 致谢

每个被合并的 PR，作者都会出现在 git log 和 GitHub 的 Contributors 列表里。如果你的 PR 修了重要 bug 或加了关键功能，欢迎在 PR 描述里提一句"麻烦在 release notes 里挂个名"，发布时会注明。

---

最后 —— 这个项目本来就是为了"把曾经对你好的话续成往后的陪伴"做的。希望你的代码也能让这件事变得更好一点。

谢谢。
