# AuditFlow 开发命令
# Windows 未安装 make 时，可直接参考各目标内的命令在 PowerShell 中执行（见 README「如何运行」）。

PY ?= python
COMPOSE := docker compose -f infra/docker-compose.yml
COMPOSE_PROD := docker compose -f infra/docker-compose.prod.yml
ENV_DB := postgresql+asyncpg://auditflow:auditflow@localhost:5433/auditflow
BASE ?= http://localhost:8000
SECRET ?= change-me-before-production

.PHONY: up down ps logs dev web migrate migrate-new seed index-policies fixtures eval eval-compare demo test test-erp test-api test-state ci lint smoke prod-up prod-down clean

## 启动依赖（postgres/redis/minio/mock-erp）
up:
	$(COMPOSE) up -d postgres redis minio mock-erp

## 启动全部服务（含 api 与 web 容器）
up-all:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=80

## 本地启动 API（需先 make up 并 make migrate）
dev:
	$(PY) -m uvicorn apps.api.main:app --reload --host 127.0.0.1 --port 8000

## 本地启动前端
web:
	cd apps/web && npm run dev

## 数据库迁移
migrate:
	$(PY) -m alembic upgrade head

migrate-new:
	$(PY) -m alembic revision --autogenerate -m "$(msg)"

## 生成 5 份制度 + 120 条合成案件，并把制度切分入库
seed:
	$(PY) -m infra.seed.run all

## 只重建制度索引
index-policies:
	$(PY) -m infra.seed.run policies

## 只生成票据图片与文本
fixtures:
	$(PY) -m infra.seed.run fixtures

## 快速评测（默认前 30 条）
eval:
	$(PY) -m packages.evals.runner --limit 30 --out data/eval_report.json

## 全量 120 条 × 4 组对照实验
eval-compare:
	$(PY) -m packages.evals.runner --compare --out data/eval_report_full.json

## 创建一个可演示的案件（基于合成数据）
demo:
	$(PY) -m infra.seed.run demo-case

## 测试
test:
	$(PY) -m pytest -q

## 交付门禁：静态检查 + 全量测试（与 CI 一致）
ci: lint
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

## 部署冒烟（对运行中的 API；经 nginx 时用 BASE=http://localhost:8080/api）
smoke:
	$(PY) infra/smoke_test.py --base $(BASE)

## 生产态编排（自包含镜像 + 启动前迁移）
prod-up:
	AUTH_SECRET=$(SECRET) $(COMPOSE_PROD) up -d --build

prod-down:
	$(COMPOSE_PROD) down

test-state:
	$(PY) -m pytest tests/test_state_machine.py -v

test-erp:
	$(PY) -m pytest apps/mock_erp/tests -v

test-api:
	$(PY) -m pytest tests/test_api.py -v

clean:
	rm -rf data/_storage data/eval_report*.json .pytest_cache
