#!/usr/bin/env bash
# PostgreSQL 逻辑备份（生产环境请在宿主机 cron 中每日执行）
#
# 用法：
#   bash infra/ops/backup.sh
#   BACKUP_DIR=/var/backups/auditflow RETENTION_DAYS=30 bash infra/ops/backup.sh
#
# 说明：
# - 使用 pg_dump 自定义格式（-Fc），可用 pg_restore 精确恢复单表/单库
# - 对象存储（MinIO/S3）建议使用独立的对象存储备份策略或云厂商快照，脚本不处理
# - 备份目录默认是仓库下的 backups/，已在 .gitignore 中排除

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/infra/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.prod}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "缺少环境文件：$ENV_FILE（可从 .env.prod.example 复制）" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="$BACKUP_DIR/postgres_${STAMP}.dump"

echo "[backup] 导出数据库到 $TARGET"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$TARGET"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$TARGET" > "$TARGET.sha256"
fi

echo "[backup] 清理超过 ${RETENTION_DAYS} 天的旧备份"
find "$BACKUP_DIR" -name 'postgres_*.dump*' -type f -mtime +"$RETENTION_DAYS" -delete

echo "[backup] 完成：$(du -h "$TARGET" | cut -f1)"
