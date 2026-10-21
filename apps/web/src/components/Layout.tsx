import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { API_BASE, api, getAccessToken, setAccessToken } from '../api';
import { ROLE_LABELS, useRole } from '../role';
import type { Role } from '../types';

const ROLES: Role[] = ['employee', 'reviewer', 'admin'];

interface PageMeta {
  title: string;
  subtitle: string;
}

function pageMeta(pathname: string): PageMeta {
  if (pathname === '/cases/new') {
    return { title: '新建案件', subtitle: '登记报销信息，上传材料后启动智能审核' };
  }
  if (/^\/cases\/[^/]+\/trace\/?$/.test(pathname)) {
    return { title: 'Trace 回放', subtitle: '按事件序列还原案件处理过程与决策依据' };
  }
  if (/^\/cases\/[^/]+\/?$/.test(pathname)) {
    return { title: '案件详情', subtitle: '材料、字段、证据、规则与决策全景视图' };
  }
  if (pathname.startsWith('/review')) {
    return { title: '审核中心', subtitle: '处理待审案件，执行人工复核与 ERP 提交' };
  }
  if (pathname.startsWith('/eval')) {
    return { title: '评测面板', subtitle: '对照实验、质量指标与失败归因分析' };
  }
  return { title: '案件列表', subtitle: '智能费用审核工作台 · 全量案件与运行状态' };
}

function CaseIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <rect x="3" y="2.5" width="14" height="15" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M6.5 6.5h7M6.5 10h7M6.5 13.5h4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <circle cx="10" cy="10" r="7.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10 6.5v7M6.5 10h7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function ReviewIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path d="M3 5.5h9M3 10h6M3 14.5h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="m12 13 2.2 2.2L18 11.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function EvalIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path d="M3.5 16.5V11M8 16.5V6.5M12.5 16.5V9M17 16.5V3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: '/cases', label: '案件列表', icon: <CaseIcon />, end: true },
  { to: '/cases/new', label: '新建案件', icon: <PlusIcon /> },
  { to: '/review', label: '审核中心', icon: <ReviewIcon /> },
  { to: '/eval', label: '评测面板', icon: <EvalIcon /> },
];

/** 全局布局：深色侧边栏 + 顶部角色切换 + 内容区 */
export function Layout() {
  const { role, userId, setRole } = useRole();
  const location = useLocation();
  const meta = pageMeta(location.pathname);
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [tokenDraft, setTokenDraft] = useState(getAccessToken());
  const [tokenSaved, setTokenSaved] = useState(false);

  // 轻量健康检查，仅用于侧边栏连接状态指示
  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then(() => {
        if (!cancelled) setApiOnline(true);
      })
      .catch(() => {
        if (!cancelled) setApiOnline(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand__mark" aria-hidden="true">
            AF
          </div>
          <div className="brand__text">
            <span className="brand__name">AuditFlow</span>
            <span className="brand__sub">智能费用审核</span>
          </div>
        </div>

        <div className="sidebar__section-label">工作台</div>
        <nav className="nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav__item${isActive ? ' nav__item--active' : ''}`}
            >
              <span className="nav__icon">{item.icon}</span>
              <span className="nav__label">{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar__spacer" />

        <div className="sidebar__token">
          <label className="sidebar__token-label" htmlFor="access-token">
            访问令牌（token 模式）
          </label>
          <input
            id="access-token"
            className="sidebar__token-input"
            type="password"
            value={tokenDraft}
            placeholder="粘贴 Bearer Token"
            onChange={(event) => {
              setTokenDraft(event.target.value);
              setTokenSaved(false);
            }}
          />
          <button
            type="button"
            className="sidebar__token-save"
            onClick={() => {
              setAccessToken(tokenDraft);
              setTokenSaved(true);
              api
                .health()
                .then(() => setApiOnline(true))
                .catch(() => setApiOnline(false));
            }}
          >
            {tokenSaved ? '已保存' : '保存令牌'}
          </button>
        </div>

        <div className="sidebar__status">
          <div className="sidebar__status-row">
            <span className={`status-dot status-dot--${apiOnline === null ? 'idle' : apiOnline ? 'ok' : 'bad'}`} />
            <span>
              {apiOnline === null ? '检测 API 连接…' : apiOnline ? 'API 服务在线' : 'API 服务不可达'}
            </span>
          </div>
          <div className="sidebar__api" title={API_BASE}>
            {API_BASE}
          </div>
          <div className="sidebar__version">AuditFlow Web · v0.1</div>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div className="topbar__title">
            <h1>{meta.title}</h1>
            <p>{meta.subtitle}</p>
          </div>
          <div className="topbar__actions">
            <div className="identity">
              <span className="identity__caption">当前身份</span>
              <span className="identity__value">
                {ROLE_LABELS[role]}
                <code>{userId}</code>
              </span>
            </div>
            <div className="role-switch" role="group" aria-label="切换角色">
              {ROLES.map((item) => (
                <button
                  key={item}
                  type="button"
                  className={`role-switch__btn${item === role ? ' role-switch__btn--active' : ''}`}
                  aria-pressed={item === role}
                  onClick={() => {
                    if (item !== role) setRole(item);
                  }}
                >
                  {ROLE_LABELS[item]}
                </button>
              ))}
            </div>
          </div>
        </header>

        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
