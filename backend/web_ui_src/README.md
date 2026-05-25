# Afterglow 配置 UI 源码

后端自带的配置向导。构建产物输出到 `../xuwen/web_ui/static/`，由后端 FastAPI 直接 serve。

## 重要：构建产物必须随源码一起提交

`../xuwen/web_ui/static/` 下的所有文件**会随 git 提交**，这样小白用户 `git clone` 后无需 Node.js 环境就能用配置 UI（启动后端就能用）。

**修改本目录源码后必须重新 build 并提交构建产物：**

```bash
npm install            # 首次或依赖变更时
npm run build          # 重新生成 ../xuwen/web_ui/static/

```

> 没有 CI 强制校验，**请贡献者自觉**：改完源码忘记 build 会导致用户拉到旧 UI 但报 API 不匹配的错。
> Code review 时也请检查是否同时更新了 `static/`。

## 使用

```bash
# 安装依赖（pnpm 在 WSL2 + Windows 文件系统上可能失败，推荐 npm）
npm install

# 开发模式：边改边看
npm run dev            # → http://localhost:5174
# 需要后端已起在 http://127.0.0.1:8000；dev server 会把 /config 反代过去

# 生产构建（产物 → ../xuwen/web_ui/static/）
npm run build
```

## 项目结构

```
web_ui_src/
├── package.json
├── vite.config.ts        # base=/config/, outDir=../xuwen/web_ui/static
├── tailwind.config.js
├── index.html
└── src/
    ├── main.ts
    ├── App.vue           # 6 步向导主组件
    ├── api.ts            # 后端 API 客户端
    └── styles.css
```
