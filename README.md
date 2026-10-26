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
| 鉴权 | HMAC-SHA256 Token（dev/token 双模式） |
| 观测 | Prometheus /metrics + X-Request-ID + 统一错误结构 |
| 部署 | 生产 Dockerfile + nginx + docker-compose.prod + smoke_test |
| CI | ruff（E9/F）+ pytest + 前端 build |

## 工程结构

```
packages/
  core/          数据模型 · 状态机(29 迁移) · 鉴权 · 指标 · 配置
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
infra/           Dockerfile · nginx · compose · Alembic · 种子生成
tests/           114 项 · 内存 SQLite · 0 外部依赖
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
pytest -q       # 114 passed, 0 skip, 不需要 Docker
```

## 部署

```powershell
docker compose -f infra/docker-compose.prod.yml up -d --build
python infra/smoke_test.py --base http://localhost:8080
```

## 设计取舍

| 决策 | 原因 |
|---|---|
| **只在异常路径用 Agent** | 报销审核顺序明确，大部分步骤不需要自由规划；固定工作流不调模型，省钱且可预测 |
| **submit_expense 不在 Agent 工具集** | Agent 只能 *提出* 提交建议，真正写入由编排层在 Verifier 通过 + 人工批准后经 Action Gateway 执行 |
| **结论必须绑定 evidence_id** | 所有可审计结论引用证据来源（文档/制度 ID、页码、原文引用、bbox） |
| **Verifier 两层不修改结论** | 只能否决，不能改——审计场景要求决策链可追溯 |
| **评测用 STUB_NOISE_RATE 确定性噪声** | 模拟真实模型偶发乐观误判，让 Verifier 拦截率在 stub 环境下也能量化出来 |
