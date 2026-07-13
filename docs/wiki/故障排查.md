# 故障排查

## 配置修改没有生效

`.env` 只在进程启动时读取。停止并重新启动后端；仅依赖 `uvicorn --reload` 不会重新加载配置。

## 配置向导没有出现

向导只在缺少关键配置时自动启用。可直接启动独立入口：

```bash
cd backend
uv run python -m xuwen.web_ui
```

然后访问 `http://127.0.0.1:8765/config/`。

## Embedding 返回 400 或维度错误

确认 `EMBEDDING_API_URL` 是 OpenAI 兼容 `/embeddings` 接口，并核对 `EMBEDDING_MODEL` 与
`EMBEDDING_DIM`。更换模型或维度后需要重建旧向量表。

## Embedding 返回 429

降低 `EMBEDDING_MAX_CONCURRENCY`，并根据供应商限制设置
`EMBEDDING_MAX_REQUESTS_PER_MINUTE`。已成功入库的批次不会丢失，修正配置后重新执行导入即可。

## `ZoneInfoNotFoundError: No time zone found with key UTC`

确保项目安装了 `tzdata`，并重新同步依赖：

```bash
cd backend
uv sync --extra dev
```

同时确认 `APP_TIMEZONE` 使用有效 IANA 时区，例如 `Asia/Shanghai`。

## LanceDB 写入错误

先确认磁盘空间和 `.data` 目录权限。大批量写入仍失败时，可临时降低
`EMBEDDING_BATCH_SIZE`，然后重新执行导入。

## 联网搜索或 URL 读取没有内容

检查 `WEB_ACCESS_ENABLED`、`WEB_FETCH_ENABLED`、provider URL 与密钥。联网能力只在用户消息明确需要
实时公开信息时触发，不会对每轮对话自动搜索。

## 诊断命令

```bash
cd backend
uv run python -m xuwen.ingestion.cli stats
curl http://127.0.0.1:8000/healthz
curl -H "Authorization: Bearer <XUWEN_API_KEY>" \
  http://127.0.0.1:8000/readyz
```

需要提交 Issue 时，请提供脱敏后的错误日志、版本、系统、Python 版本和最小复现步骤。不要上传真实聊天文件、
`.env` 或 API key。

