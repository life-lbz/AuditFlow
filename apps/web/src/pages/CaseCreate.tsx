import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, errorMessage } from '../api';
import { Loading } from '../components/Loading';
import { Notice } from '../components/Notice';
import { categoryLabel } from '../format';
import { useRole } from '../role';

const CATEGORIES = ['travel', 'meal', 'office_supplies'];

const REQUIRED_DOCS: Record<string, string[]> = {
  travel: ['发票', '银行流水', '审批邮件'],
  meal: ['发票', '收据'],
  office_supplies: ['发票'],
};

/** 新建案件：登记报销要素，创建后跳转详情继续上传与运行 */
export function CaseCreate() {
  const navigate = useNavigate();
  const { role, userId } = useRole();
  const [employeeId, setEmployeeId] = useState(role === 'employee' ? userId : '');
  const [category, setCategory] = useState('travel');
  const [amount, setAmount] = useState('');
  const [description, setDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // 切换为员工角色时自动带入当前用户 ID
  useEffect(() => {
    if (role === 'employee' && !employeeId) setEmployeeId(userId);
  }, [role, userId, employeeId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');

    const parsedAmount = Number(amount);
    if (!employeeId.trim()) {
      setError('请填写报销人 ID');
      return;
    }
    if (!Number.isFinite(parsedAmount) || parsedAmount <= 0) {
      setError('报销金额必须大于 0');
      return;
    }

    setSubmitting(true);
    try {
      const created = await api.createCase({
        employee_id: employeeId.trim(),
        category,
        amount: parsedAmount,
        description: description.trim(),
      });
      navigate(`/cases/${created.id}`, {
        state: { flash: `案件 ${created.id} 创建成功，请上传材料后运行工作流。` },
      });
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page page--narrow">
      <div className="page-toolbar">
        <div className="page-toolbar__meta">
          <span className="eyebrow">登记</span>
          <h2>新建报销案件</h2>
          <p>创建案件后可上传发票、收据、流水与审批邮件，再启动多智能体审核。</p>
        </div>
      </div>

      {role !== 'employee' && (
        <Notice tone="info" title="当前角色不可创建案件">
          审核员与管理员仅可查看、审核案件。请使用顶部角色切换为「员工」后再提交新案件。
        </Notice>
      )}

      {error && (
        <Notice tone="error" title="创建失败" onClose={() => setError('')}>
          {error}
        </Notice>
      )}

      <div className="split-layout">
        <form className="panel form" onSubmit={handleSubmit}>
          <div className="panel__head">
            <div>
              <h3>案件要素</h3>
              <p className="panel__sub">带 * 的字段为必填项</p>
            </div>
          </div>

          <div className="form-grid">
            <label className="field">
              <span>报销人 ID *</span>
              <input
                value={employeeId}
                onChange={(event) => setEmployeeId(event.target.value)}
                placeholder="如 u_1002"
                autoComplete="off"
                required
              />
              <small>员工角色默认使用当前登录 ID：{userId}</small>
            </label>

            <label className="field">
              <span>报销类型 *</span>
              <select value={category} onChange={(event) => setCategory(event.target.value)}>
                {CATEGORIES.map((item) => (
                  <option key={item} value={item}>
                    {categoryLabel(item)}
                  </option>
                ))}
              </select>
              <small>不同类型对应不同的必需材料与额度规则</small>
            </label>

            <label className="field">
              <span>报销金额（元）*</span>
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="0.00"
                required
              />
            </label>

            <label className="field field--full">
              <span>报销事由</span>
              <textarea
                rows={3}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="简要描述费用发生背景，如：上海出差住宿 2 晚"
              />
            </label>
          </div>

          <div className="form__actions">
            <button type="button" className="button" onClick={() => navigate('/cases')} disabled={submitting}>
              取消
            </button>
            <button type="submit" className="button button--primary" disabled={submitting || role !== 'employee'}>
              {submitting ? '创建中…' : '创建案件'}
            </button>
          </div>
          {submitting && <Loading compact label="正在创建案件…" />}
        </form>

        <aside className="panel panel--side">
          <div className="panel__head">
            <div>
              <h3>材料清单</h3>
              <p className="panel__sub">当前类型建议上传</p>
            </div>
          </div>
          <ul className="checklist">
            {(REQUIRED_DOCS[category] ?? []).map((item) => (
              <li key={item}>
                <span className="checklist__mark">□</span>
                {item}
              </li>
            ))}
          </ul>
          <div className="side-note">
            <strong>审核流程</strong>
            <ol>
              <li>上传材料并完成 OCR 字段抽取</li>
              <li>运行工作流：规则校验 → 证据检索 → 决策</li>
              <li>低风险自动通过，其余进入人工审核</li>
            </ol>
          </div>
          <div className="side-note side-note--muted">
            支持格式：.txt / .md / .pdf / .png / .jpg / .jpeg / .webp / .bmp / .tiff
          </div>
        </aside>
      </div>
    </div>
  );
}
