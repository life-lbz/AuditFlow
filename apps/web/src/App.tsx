import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { EmptyState } from './components/EmptyState';
import { RoleProvider } from './role';
import { CaseList } from './pages/CaseList';
import { CaseCreate } from './pages/CaseCreate';
import { CaseDetailPage } from './pages/CaseDetail';
import { Review } from './pages/Review';
import { TracePage } from './pages/TracePage';
import { EvalPanel } from './pages/EvalPanel';

function NotFound() {
  return (
    <div className="panel">
      <EmptyState
        title="页面不存在"
        description="请检查地址是否正确，或返回案件列表继续操作。"
        action={
          <Link className="button button--primary" to="/cases">
            返回案件列表
          </Link>
        }
      />
    </div>
  );
}

/** 路由入口：所有业务页面共享 Layout 与角色上下文 */
export function App() {
  return (
    <RoleProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Navigate to="/cases" replace />} />
            <Route path="/cases" element={<CaseList />} />
            <Route path="/cases/new" element={<CaseCreate />} />
            <Route path="/cases/:id" element={<CaseDetailPage />} />
            <Route path="/cases/:id/trace" element={<TracePage />} />
            <Route path="/review" element={<Review />} />
            <Route path="/eval" element={<EvalPanel />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </RoleProvider>
  );
}
