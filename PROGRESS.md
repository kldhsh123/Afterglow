# 文档迁移与品牌接入进度

最后更新：2026-10-19。恢复工作时先检查当前文件和工作树；后端测试修复已由用户交给另一个 AI，本任务不继续修改或运行后端测试。

## 已实现

- 双语 VitePress 文档站：19 对文章，中文源在 `docs/wiki/`，英文源在 `docs/en/`，映射在 `docs/pages.json`；界面文案每种语言独立文件。
- 根入口默认英文；浏览器语言包含简体或繁体中文时进入中文版。语言下拉框保存选择，切换对应文章和章节；两种语言均有导航、目录及本地搜索。
- GitHub Pages 发布工作流已准备，预计 URL 为 https://kldhsh123.github.io/Afterglow/。旧 Wiki 通过生成镜像兼容，应用中的文档入口使用新网站。
- 根 `README.md` 为英文，中文已移到 `readme/README.zh-CN.md`；相互入口及中文相对路径已更新，两个 README 均链接七种聊天来源的导出教程。
- 用户最终选择不带文字的 Logo：两份 README 使用品牌 mark 深浅色变体；文档站导航、入口及 favicon 使用 `assets/brand/` 的原始资源。
- 环境变量参考中英文独立维护；英文注释模板 `backend/.env.example`、中文注释模板 `backend/.env.example.zh-CN`。
- 文档站配置编辑器支持双语、分组搜索、本地导入/预览/下载、隐藏密钥、字段校验；草稿仅存在浏览器内存，不持久化或上传配置。schema 由 Settings、双语参考及模板生成，构建核验字段和值的一致性。
- `website/` 官网导航和页脚使用不带文字的 `afterglow-icon-light.svg`；favicon 从品牌源内联导入。此前普通和单文件构建及浏览器检查已通过。
- `website/` 目前被原有 `.gitignore` 忽略，未擅自更改其跟踪方式。

## 当前验收

- 本轮重新运行文档测试：8 项 Node 测试和 5 项 Python 生成测试通过。
- 两份 README 本地链接、Logo 路径及七种来源教程已核验。
- 追加导出要求前，生产构建通过，38 本地化页面、1565 处内部链接/锚点通过；官网普通构建和单文件构建均通过。
- 官网 Logo、favicon、桌面/手机布局及 JS 错误浏览器检查通过。
- 浏览器 10 组检查已通过：五种浏览器语言、手动偏好/文章章节切换/无 JS 入口、两种语言搜索、配置导入下载/校验/隐私/手机布局、流程图/主题/Logo/导航。脚本和结果位于 `/tmp/afterglow-docs-browser/audit.mjs`、`audit.log`。
- 最新文档修改已通过 19 文章对/语言键/链接生成一致性检查，Wiki 38 页生成通过。
- 含 Discord 和新版导出说明的最终生产构建通过（331.61 秒，只有 Mermaid 分包体积提示），38 页、1571 处内部链接/锚点通过；日志 `/tmp/afterglow-docs-final-build.log`。
- 上次英文/中文页面浏览器检查通过：导航及首页 Discord 链接、WhatsApp 桌面流程、当时的 Telegram 导出说明，未出现 JS 错误。该次 Telegram JSON 要求现已按用户更正撤回，不能将旧检查视为此次 HTML 文案验收。相关变更 diff 空白检查通过，Git 无未解决冲突。
- 此前 `/tmp` 下的检查记录已不在当前环境；不将旧日志路径当成本轮验收证据。

## 发布与本地查看

- 本地文档：`cd docs && npm install && npm run dev -- --host 0.0.0.0`，访问终端给出的 `/Afterglow/` 地址。
- 仓库 Settings → Pages → Source 选择 GitHub Actions，合并/推送 main 后工作流发布；也可手动运行 docs-pages。
- 本任务不提交、不推送、不修改远端设置，不修改实际 `.env` 或其他 AI 的后端工作。

## 最新导出与社区要求

- Discord 服务器 https://discord.gg/CKrvgCyK4y 已加入两份 README 的头部/反馈/社区入口、双语文档首页、文档导航与 docs/README.md。
- WhatsApp 导出教程已移除手机端流程，只介绍桌面浏览器 WhatsApp Web，并给出当前 TXT/Markdown 导入器实际要求的格式。用户未指定具体工具，因此采用通用桌面流程；未把某个未知扩展宣传为已验证兼容。
- 用户最新更正：撤回 Telegram JSON 要求，恢复 HTML 导出。两份 README、双语导出教程/导入说明/FAQ 已恢复为 Telegram Desktop HTML，上传 messages.html 及所有分页，无需额外格式转换。
- 撤销此前关于新增原生 Telegram JSON 支持的询问；不再将其列为后续任务。Discord 与 WhatsApp 桌面流程保留，后端未修改。

文档迁移、README 整理、官网品牌接入已完成。尚未提交、推送或发布。Telegram HTML 文案恢复后的定向校验结果见后续记录；旧生产构建仍为上次版本，运行 dev 会重新生成当前源文档，发布工作流也会重新构建。

- Telegram 撤回后的定向校验完成：8 个相关源文件恢复 HTML；Discord 入口及 WhatsApp 桌面流程保留；`npm --prefix docs run check` 成功生成 38 页并通过 19 文章对/语言键校验，相关文件 `git diff --check` 通过。本次纯文案恢复未重复执行生产构建或后端测试。

## 文档命令的语言参数补齐（2026-10-19）

- 用户要求：中文文档中的启动、导入、分析等示例应显式选择中文，并说明多语言参数；英文页面对应选择英文。
- 已同步快速开始、导入/导出、专用格式、开发、Docker、API、故障排查、高级功能及架构/环境变量内联命令，以及两份主 README 和后端 README。
- Uvicorn 使用 `XUWEN_LANGUAGE=zh-CN` / `en` 的 Bash 进程前缀；主 CLI 和独立配置入口使用 `--language zh-CN` / `en`；鉴权 curl 示例增加对应 `Accept-Language`。
- 快速开始解释默认英文、CLI 参数优先级、持久化 `.env`、完整重启、界面与后端独立，以及 Windows PowerShell 写法。中文手动配置强调中文注释模板的默认运行语言仍为英文，需修改 `XUWEN_LANGUAGE`。
- 独立 Docker 的第一次启动先安全创建宿主 `.env`（已有文件不覆盖），包含本页语言及 `CONFIG_UI_LOCALHOST_ONLY=false`。预建 `.env` 会跳过入口脚本的冷启动分支，所以必须同时提供容器访问设置；已有配置要求手动核对。说明宿主 Shell 的语言变量不会自动传入容器。
- 独立辅助脚本 `analyze_persona.py`、`check_proactive_quality.py` 暂不支持 `--language`，文档已明确标注，未追加无效参数；未修改后端代码或实际 `.env`。
- 本轮定向审计通过：43 个 Markdown 源文件中，110 条 CLI、14 条服务启动、18 条鉴权 HTTP、8 条内联 CLI 语言设置正确，118 个 Bash 代码块通过语法检查；只检查语法，没有实际执行导入、分析、网络请求或模型调用。
- 文档生成检查与空白检查通过，19 对文章生成 38 页。本次未运行后端测试或重复耗时生产构建；生产 dist 需在发布前重新构建，dev 和发布工作流会从当前源文件生成。

## 文档 QQ 群入口补齐（2026-10-19）

- 从现有 README 核实 QQ 群号为 `330316577`，沿用其中的 `qm.qq.com` 入群链接。
- 已加入中英文文档首页、所有本地化文章的顶部导航及 `docs/README.md`，均同时展示群号和可点击链接；导航标签在两种语言文件中分别维护。
- `npm --prefix docs run check` 通过，生成 38 页并通过 19 对文章/语言键检查；VitePress 配置语法与相关 diff 空白检查通过。未提交或发布，线上站点需推送后由 Pages 工作流更新。
