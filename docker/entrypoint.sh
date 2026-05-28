#!/usr/bin/env bash
# ============================================================
# Afterglow 容器入口脚本
# 职责：把"空目录冷启动"打磨到丝滑——
#   1) 挂载目录里没有 .env，从镜像内拷一份 .env.example 出来作为模板，
#      让首次模式向导走完写出来的 .env 仍然带完整注释结构。
#   2) 冷启动场景默认关闭 CONFIG_UI_LOCALHOST_ONLY，否则容器外浏览器
#      访问 /config/ 会被首次模式拒绝（请求源 IP 是 docker 网桥地址，
#      不算 localhost）。已有 .env 的场景完全不动用户配置。
#   3) 预创建 .data 子目录，避免首次启动时各服务并发 mkdir 抢锁。
#   4) 把容器内 afterglow 用户的 uid/gid 动态对齐到挂载目录所有者。
#      不然 WSL drvfs / NFS / 不同 Linux 主机上跨 uid 时，
#      shutil.copy2 在 utime() 步骤会被内核拒为 EPERM —— 表现为
#      配置向导 PUT /config/values 报 500，备份机制也写不下去。
# 全程不修改任何应用源码，纯运行时编排逻辑。
#
# 该脚本以 root 启动；最后 exec runuser 切到 afterglow 跑应用。
# ============================================================

set -euo pipefail

DATA_ROOT="${AFTERGLOW_DATA_ROOT:-/host-backend}"
ENV_FILE="$DATA_ROOT/.env"
ENV_EXAMPLE_SRC="/app/backend/.env.example"
ENV_EXAMPLE_DST="$DATA_ROOT/.env.example"
RUN_USER="afterglow"

# 挂载点本身必须存在；compose 已经保证，这里只是兜底防御性检查
if [ ! -d "$DATA_ROOT" ]; then
  echo "[entrypoint] FATAL: 挂载目录 $DATA_ROOT 不存在；请检查 compose 的 volumes 配置" >&2
  exit 1
fi

# ------------------------------------------------------------
# (4) UID/GID 对齐 —— 必须在所有写文件操作之前完成
# ------------------------------------------------------------
MOUNT_UID="$(stat -c '%u' "$DATA_ROOT")"
MOUNT_GID="$(stat -c '%g' "$DATA_ROOT")"
CURRENT_UID="$(id -u "$RUN_USER")"
CURRENT_GID="$(id -g "$RUN_USER")"

# uid=0 (root) 的挂载点直接跳过对齐（用户显式以 root 跑的场景）
if [ "$MOUNT_UID" != "0" ] && [ "$MOUNT_UID" != "$CURRENT_UID" ]; then
  echo "[entrypoint] 挂载目录 uid=$MOUNT_UID gid=$MOUNT_GID，调整 $RUN_USER 用户匹配（原 uid=$CURRENT_UID gid=$CURRENT_GID）"
  # groupmod 在 gid 冲突时会失败；先确保目标 gid 不被占用
  if getent group "$MOUNT_GID" >/dev/null && [ "$(getent group "$MOUNT_GID" | cut -d: -f1)" != "$RUN_USER" ]; then
    echo "[entrypoint] gid $MOUNT_GID 已被其他组占用，跳过 gid 调整" >&2
  else
    groupmod -g "$MOUNT_GID" "$RUN_USER" 2>/dev/null || true
  fi
  usermod -u "$MOUNT_UID" -g "$MOUNT_GID" "$RUN_USER" 2>/dev/null || {
    echo "[entrypoint] 警告: 无法调整 uid/gid，配置向导写入可能因权限失败" >&2
  }
  # venv 文件权限默认 644 + 目录 755，afterglow 即使不是 owner 也能读 + import；
  # 只需要把 home 目录归还给新 uid，避免 shell 启动告警
  chown -R "$MOUNT_UID:$MOUNT_GID" "/home/$RUN_USER" 2>/dev/null || true
fi

# ------------------------------------------------------------
# (1) .env.example 模板兜底
# ------------------------------------------------------------
if [ ! -f "$ENV_EXAMPLE_DST" ] && [ -f "$ENV_EXAMPLE_SRC" ]; then
  cp "$ENV_EXAMPLE_SRC" "$ENV_EXAMPLE_DST"
  chown "$MOUNT_UID:$MOUNT_GID" "$ENV_EXAMPLE_DST" 2>/dev/null || true
  echo "[entrypoint] 已拷贝 .env.example 到挂载目录（首次冷启动）"
fi

# ------------------------------------------------------------
# (2) 冷启动：完全没有 .env
# ------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  echo "[entrypoint] 检测到挂载目录无 .env —— 进入冷启动模式"
  if [ -f "$ENV_EXAMPLE_DST" ]; then
    cp "$ENV_EXAMPLE_DST" "$ENV_FILE"
    echo "[entrypoint] 已基于 .env.example 创建空模板 .env"
  else
    : > "$ENV_FILE"
    echo "[entrypoint] 未找到 .env.example，创建了空 .env（向导仍可工作但无注释结构）"
  fi
  chown "$MOUNT_UID:$MOUNT_GID" "$ENV_FILE" 2>/dev/null || true
  if ! grep -qE '^\s*CONFIG_UI_LOCALHOST_ONLY=' "$ENV_FILE"; then
    {
      echo ""
      echo "# 由容器 entrypoint 在冷启动时注入：容器环境下放开访问限制，"
      echo "# 完成首次配置后可改回 true。已经存在则保留你的设置。"
      echo "CONFIG_UI_LOCALHOST_ONLY=false"
    } >> "$ENV_FILE"
    echo "[entrypoint] 已在 .env 末尾注入 CONFIG_UI_LOCALHOST_ONLY=false"
  fi
fi

# ------------------------------------------------------------
# (3) 预创建 .data 子目录
# ------------------------------------------------------------
mkdir -p "$DATA_ROOT/.data/lancedb" \
         "$DATA_ROOT/.data/persona" \
         "$DATA_ROOT/.data/stickers" \
         "$DATA_ROOT/.data/images" \
         "$DATA_ROOT/.data/uploads"

# 切到 afterglow 跑应用
exec runuser -u "$RUN_USER" -- "$@"
