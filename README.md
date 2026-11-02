# AuditFlow

面向企业财务报销场景的 **多模态审核智能体**：单 Decision Agent + 确定性状态机 + Verifier 两层校验 + 人工审批 + 副作用幂等网关。

> 核心问题不是「让多个 Agent 互相讨论」，而是：在高风险副作用、合规要求和不确定输入下，如何让 Agent 可靠完成业务流程，并留下可审计、可回放、可评测的证据链。

---

## 流程一览

```
员工提交报销
    │
    ▼
文档上传 → 多模态抽取（字段/置信度/OCR）
    │
    ▼
证据就绪 → 混合检索（BM25 + 向量 + RRF + rerank）命中制度条款
    │
    ▼
规则引擎（8 条确定性规则：金额匹配、抬头税号、重复报销、超额、票据完整性…）
    │
    ├── 全过 + 低风险 ──→ 固定工作流直接给出结论
    │
    └── 异常路径 ──→ Decision Agent（工具调用循环，上限 8 步）
                        │ 可选工具：extract_document / search_policy /
                        │ match_bank_transaction / check_duplicate /
                        │ get_erp_record / request_human / preview_expense
                        │ ⚠️ submit_expense 不在工具集内
                        ▼
                    Verifier 两层校验
                    第一层：确定性（证据存在性 / 制度引用 / 规则覆盖）
                    第二层：模型（结论-证据一致性 / 遗漏反证 / 自相矛盾）
                        │
                        ▼
                    Action Gateway（幂等键 + 先 preview 再 submit + 提交后查询校验）
                        │
                        ▼
                    Mock ERP（6 种故障注入，进程内 ASGI 调用）
```

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI · SQLAlchemy 2.0 async · Alembic · Pydantic Settings |
| 前端 | React 18 · TypeScript · Vite |
| 模型 | ModelGateway 抽象：Stub（确定性启发式）/ Remote（OpenAI 兼容） |
| 检索 | BM25 + hashing 向量 + RRF 融合 + rerank |
| 鉴权 | HMAC-SHA256 Token（dev/token 双模式） + RBAC 权限矩阵 |
| 观测 | Prometheus /metrics（可选 Bearer）+ 结构化 JSON 日志 + 请求 ID |
| 可靠性 | 固定窗口限流 + 案件级互斥锁（Redis/内存降级） + liveness/readiness 探针 |
| 安全 | 生产配置自检 + 安全响应头 + 上传魔数校验 + 请求体上限 |
| 部署 | 多阶段非 root 镜像 + nginx + docker-compose.prod + 备份恢复 + smoke_test |
| CI | ruff + pytest（含覆盖率）+ pip-audit + 前端 build + 镜像构建 |

## 工程结构

```
packages/
  core/          数据模型 · 状态机(29 迁移) · 鉴权 · 指标 · 配置
                 日志 · 限流 · 案件锁 · Redis 客户端 · 健康检查
  gateway/       ModelGateway 抽象 + Stub + Remote
  documents/     解析 · OCR · 字段抽取
  retrieval/     制度切分 · BM25+向量混合检索 · RRF · rerank
  rules/         8 条确定性规则 + 可解释风险评分
  tools/         8 个工具 + 权限/超时/重试/Schema 校验
  erp/           Action Gateway（幂等/补偿） + Mock ERP 客户端
  agent/         Decision Agent 运行时
  verifier/      确定性 + 模型 两层验证
  orchestrator/  案件编排器（4 种实验模式）
  evals/         评测执行器 + 指标 + dataset_hash + bootstrap CI95
apps/
  api/           FastAPI 服务
  web/           React 前端（案件/审核/Trace/评测）
  mock_erp/      Mock ERP（6 种故障注入）
infra/           Dockerfile · nginx · compose · Alembic · 种子生成 · ops 脚本
tests/           123 项 · 内存 SQLite · 0 外部依赖 · 含生产基线用例
```

## 快速开始

```powershell
# 后端
conda create -n auditflow python=3.12 -y
conda activate auditflow
cd auditflow
pip install -e ".[dev]"

$env:DATABASE_URL="sqlite+aiosqlite:///./data/auditflow_dev.db"
$env:STORAGE_BACKEND="local"
$env:MOCK_ERP_INPROCESS="true"
$env:MODEL_PROVIDER="stub"
$env:AUTH_MODE="dev"

python -c "import asyncio; from packages.core.db import init_db; asyncio.run(init_db())"
python -m infra.seed.run all
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000

# 前端（另开终端）
cd apps/web
npm ci
npm run dev
```

打开 http://localhost:5173 ，后端 API 文档 http://127.0.0.1:8000/docs 。

## 评测

```powershell
# 四组对照实验
python -m packages.evals.runner --compare

# 噪声压力（验证 Verifier 拦截能力）
$env:STUB_NOISE_RATE="0.2"
python -m packages.evals.runner --mode single_agent --out data/report_b.json
python -m packages.evals.runner --mode single_agent_verifier --out data/report_c.json
```

数据集 120 条（80 正常 / 38 异常 / 2 对抗），报告带 `dataset_hash` 指纹可复现。

## 测试

```powershell
ruff check .
pytest -q       # 123 passed, 0 skip, 不需要 Docker
```

测试覆盖状态机、四组评测、API 端到端、Action Gateway、Mock ERP、规则、检索、
权限与本次新增的生产基线（配置自检 / 探针 / 限流 / 上传校验 / 案件锁 / 指标鉴权）。

## 生产部署

生产形态由 `infra/docker-compose.prod.yml` 编排：PostgreSQL、Redis、MinIO 只在内部
`backend` 网络可达，仅 nginx 通过 `edge` 网络发布 `WEB_PORT`；API 容器以非 root 运行、
只读根文件系统，启动前自动执行 `alembic upgrade head`。

```powershell
# 1. 准备生产变量（切勿提交）
Copy-Item .env.prod.example .env.prod
# 按注释填写 POSTGRES_PASSWORD / MINIO_SECRET_KEY / AUTH_SECRET / CORS_ORIGINS
# 生成密钥：openssl rand -hex 24（数据库/对象存储）、openssl rand -hex 32（AUTH_SECRET）

# 2. 启动（Makefile 已带 --env-file）
make prod-up            # 等价于 docker compose --env-file .env.prod -f infra/docker-compose.prod.yml up -d --build
make prod-ps            # 查看容器与健康状态
make prod-logs          # 跟踪日志
make prod-smoke         # 冒烟：liveness/readiness/安全头/指标/系统信息
make prod-down
```

单副本部署可使用 `RATE_LIMIT_BACKEND=memory` / `CASE_LOCK_BACKEND=memory`；**多副本必须
改为 `redis`**，否则限流与案件串行只在单进程内生效。

### 健康探针

| 路径 | 用途 | 说明 |
|---|---|---|
| `/health` | 兼容旧探针 | 固定返回 `{status: ok, version}` |
| `/health/live` | liveness | 仅进程存活，不触达依赖 |
| `/health/ready` | readiness | 并行检查 database/storage/redis/erp；数据库不可用返回 503，其余降级为 `degraded` 但仍 200 |

`/health/ready` 同时返回 `production_warnings()`，例如 stub 模型、local 存储、默认密钥。

### 安全基线

`ENVIRONMENT=production` 时启动前会做硬性自检，任一条不满足直接拒绝启动：

- `AUTH_MODE` 必须为 `token`（`dev` 会信任可伪造的 `X-Role`）；
- `AUTH_SECRET` 不得为默认值且长度 ≥ 32；
- `CORS_ORIGINS` 不得为 `*`。

运行时另有：固定窗口限流（读 120/min、写 30/min，返回 429 与 `Retry-After`）、
请求体上限 413、扩展名白名单 + 文件头魔数校验、`X-Content-Type-Options` /
`X-Frame-Options` / `Referrer-Policy` / `Permissions-Policy` 安全头、
`/metrics` 可选 Bearer 鉴权、nginx CSP 与 `server_tokens off`。

### 日志与指标

- `LOG_FORMAT=json` 输出结构化日志，字段含 `request_id/method/path/status/duration_ms`，
  可用 `request_id`（响应头 `X-Request-ID`）串起单次请求；
- `/metrics` 为 Prometheus 文本格式，生产可通过 `METRICS_TOKEN` 限制抓取；
- 容器日志按 10MB / 3 份轮转，`restart: unless-stopped`。

### 备份与恢复

```powershell
# 数据库（pg_dump -Fc + sha256，默认保留 14 天）
bash infra/ops/backup.sh
# 恢复（--clean --if-exists，FORCE=1 跳过确认）
bash infra/ops/restore.sh backups/postgres_20261102_030000.dump
```

MinIO 证据文件需按对象存储自身策略单独备份，脚本注释中已说明。

### 上线前检查清单

- [ ] `.env.prod` 四项必填值已替换，且未提交到版本库
- [ ] `make prod-up` 后 `make prod-ps` 全部 `healthy`
- [ ] `make prod-smoke` 全绿
- [ ] `GET /health/ready` 无 `production_warnings`
- [ ] 备份脚本已加入定时任务并验证过一次恢复

## 设计取舍

| 决策 | 原因 |
|---|---|
| **只在异常路径用 Agent** | 报销审核顺序明确，大部分步骤不需要自由规划；固定工作流不调模型，省钱且可预测 |
| **submit_expense 不在 Agent 工具集** | Agent 只能 *提出* 提交建议，真正写入由编排层在 Verifier 通过 + 人工批准后经 Action Gateway 执行 |
| **结论必须绑定 evidence_id** | 所有可审计结论引用证据来源（文档/制度 ID、页码、原文引用、bbox） |
| **Verifier 两层不修改结论** | 只能否决，不能改——审计场景要求决策链可追溯 |
| **评测用 STUB_NOISE_RATE 确定性噪声** | 模拟真实模型偶发乐观误判，让 Verifier 拦截率在 stub 环境下也能量化出来 |
