"""配置 WebUI 子应用。

由 `config_ui_enabled` 开关挂载到主 app 的 `config_ui_path_prefix` 路径下。
对外提供一组面向小白的配置接口：
- GET /schema      —— 字段元数据（分组、说明、是否 secret），从 .env.example 解析
- GET /values      —— 当前 .env 生效值，secret 字段仅返回 set/preview
- PUT /values      —— 校验后写回 .env，自动备份 + 原子写
- POST /test/{provider} —— 连通性测试
- POST /uploads    —— 上传聊天记录（占位，后续实现）
- GET /tasks/{id}/stream —— 导入进度 SSE（占位，后续实现）

鉴权独立于主 API：使用 `config_ui_setup_token`，首次配置无需 XUWEN_API_KEY。
"""

from __future__ import annotations

from xuwen.web_ui.app import create_config_app

__all__ = ["create_config_app"]
