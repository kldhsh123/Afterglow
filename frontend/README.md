# Afterglow（续温）前端

> 把曾经对你好的话，续成往后的陪伴。

基于 Vue 3 + Vite + TypeScript + TailwindCSS 的"时光信笺"风格聊天界面。

前端主要用于本地测试、调试和体验后端能力。Afterglow 的主体能力在 `backend/`：
导入、向量库、检索、persona、生活状态、联网能力和 OpenAI 兼容 API 都由后端负责。
第三方程序接入时应优先调用后端 API，而不是依赖前端状态。

## 快速开始

```bash
cd frontend
pnpm install        # 或 npm install
pnpm dev            # 开发服务器，默认 http://localhost:5173
                    # 会把 /v1, /memory, /debug, /info 等代理到后端

# 若后端跑在别处：
VITE_BACKEND_URL=http://127.0.0.1:9000 pnpm dev

# 生产构建
pnpm build
pnpm preview
```

## 设计

- **时光信笺**：米色信笺 (#F5F1E4) + 黛蓝墨痕 (#1A2F4B) + 思源宋体 + 暖光粒子背景
- **拟人化打字**：SSE chunk 进队列，按真人打字节奏（每字 22-60ms + 标点停顿）逐字渲染
- **记忆溯源**：朋友的每条回复可点开右下角波纹图标，看到这条回复的灵感来自哪段历史聊天
- **诗化占位符**：`[图片]` → 模糊的胶片框 + "这张图片已随时间斑驳"
- **暗色模式**：深墨绿背景（避开纯黑），适合夜里翻看
- **首次引导**：让用户填名字、关系
- **设置页**：API key、字号、主题、模板、回写暂停

## 项目结构

```
src/
├── api/             # 后端调用封装（fetch + SSE）
├── components/      # 业务组件
│   ├── chat/        # 聊天 UI
│   ├── common/      # 通用：空状态、占位符消息、错误条
│   ├── layout/      # AppShell + AmbientCanvas
│   ├── memory/      # 记忆溯源 / 锚点
│   └── onboarding/  # 首次引导
├── composables/     # 组合式函数（打字机、自动滚动、markdown 渲染）
├── router/          # vue-router 配置
├── stores/          # Pinia stores（settings / chat / memory）
├── styles/          # tailwind.css
├── types/           # 与后端对应的类型定义
└── views/           # 路由页面（HomeView / SettingsView）
```

## 多语言维护

聊天前端使用 `src/locales/zh-CN.ts` 和 `src/locales/en.ts`；配置向导使用
`backend/web_ui_src/src/locales/` 下的同名文件，每种语言一个文件。
组件通过各自的 `useLocale()` 调用 `t(key, params)`；列表文案使用 `list(key)`。
切换时需要更新的翻译映射放进 `computed`，避免在组件初始化时固定语言。

首次访问时，只要浏览器语言列表包含 `zh`（简体或繁体）就使用中文，其他语言使用英文。
手动选择优先于浏览器语言，分别保存在 `afterglow.locale` 和 `afterglow.config.locale`。
顶部、首次引导、配置完成页均提供语言下拉框。中文配置向导显示服务商预设；英文隐藏预设和申请链接，
并在读取已有配置后为未填写的服务填充 Google 聊天、OpenAI 向量及辅助模型默认值。
切换语言不会替换已有服务配置，不会翻译聊天记录、用户名称、自定义文本或后端原始错误详情。

安装聊天前端依赖后，可在仓库根目录执行：

```sh
node scripts/check_locales.cjs
```

该检查覆盖翻译键与插值参数一致性、中文模板硬编码、浏览器语言检测、手动选择保存和存储不可用时的行为。
发布前分别运行 `frontend` 的 `pnpm build` 和 `backend/web_ui_src` 的 `npm run build`。
配置向导产物会自动写入 `backend/xuwen/web_ui/static/`。

英文默认值维护位置：`backend/web_ui_src/src/englishDefaults.ts`。
地址必须包含服务商要求的完整基础路径：OpenAI 是 `https://api.openai.com/v1`，Google 是
`https://generativelanguage.googleapis.com/v1beta/openai`；应用只追加业务端点，不会自动补 `/v1`。
Google 模型优先使用后端 Gemini 预设，向量默认为 `text-embedding-3-small`（1536 维），辅助模型使用 `gpt-4.1-mini`。
可选服务不会因填充默认值而自动开启，API key 始终由用户填写。

参考：[OpenAI 向量文档](https://developers.openai.com/api/docs/guides/embeddings)、
[GPT-4.1 Mini 接口能力](https://developers.openai.com/api/docs/models/gpt-4.1-mini)。

## License

AGPL-3.0-or-later（继承仓库根 LICENSE）。
