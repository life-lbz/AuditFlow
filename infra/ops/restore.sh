#!/usr/bin/env bash
# 从 pg_dump 备份恢复 PostgreSQL（会覆盖当前库，务必先停写并确认）
#
# 用法：
#   bash infra/ops/restore.sh backups/postgres_20261102_030000.dump
#   FORCE=1 bash infra/ops/restore.sh backups/xxx.dump   # 跳过交互确认（CI/演练）
#
# 建议流程：
#   1. 停止 api：docker compose ... stop api
#   2. 执行恢复
#   3. 启动 api：docker compose ... start api
#   4. 冒烟：python infra/smoke_test.py --base http://localhost:8080/api --token <token>

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/infra/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.prod}"
DUMP_FILE="${1:-}"

if [[ -z "$DUMP_FILE" || ! -f "$DUMP_FILE" ]]; then
  echo "用法：bash infra/ops/restore.sh <备份文件>" >&2
  exit 1
fi

if [[ "${FORCE:-0}" != "1" ]]; then
  read -r -p "恢复会覆盖当前数据库，确认继续？输入 yes： " answer
  [[ "$answer" == "yes" ]] || { echo "已取消"; exit 1; }
fi

echo "[restore] 校验备份文件"
if [[ -f "$DUMP_FILE.sha256" ]] && command -v sha256sum >/dev/null 2>&1; then
  (cd "$(dirname "$DUMP_FILE")" && sha256sum -c "$(basename "$DUMP_FILE.sha256")")
fi

echo "[restore] 恢复 $DUMP_FILE"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < "$DUMP_FILE"

echo "[restore] 完成，请启动 api 并执行冒烟检查"
