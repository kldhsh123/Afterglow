# Docker 部署与运维

如果你不想装 Python / uv / 一堆依赖，直接用 Docker 镜像一行起服。和源码部署**共享 `.env` 与 `.data/`**，任意时候切换互不影响。

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

浏览器打开 `http://localhost:8000/config/`，把 token 粘进向导第 1 步，8 步走完即生效。向导写入的 `.env` 直接落到 `~/afterglow/.env`，备份在 `.env-backups/`，重启即用。

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

# 可选：离线导入历史图片（目录内可包含多个 JSON/JSONL 和任意层级图片目录）
docker compose run --rm backend \
  python -m xuwen.ingestion.cli import-images .imports/export-dir --plugin afterglow_v1

# 大库建索引
docker compose run --rm backend python -m xuwen.ingestion.cli index

# 临时停 / 完全停（数据保留）
docker compose stop
docker compose down

# 进容器排错
docker compose exec backend bash
```

### 前端怎么办

镜像里**只有后端**。前端按[快速开始](Getting-Started.md#启动前端可选)运行 `pnpm dev`，
或自行用 Nginx 托管 `frontend/dist`。后端容器对外暴露 8000，前端请求发送到该端口。

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
