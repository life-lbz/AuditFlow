// 角色上下文：全局维护员工/审核员/管理员身份，并同步到 API 请求头

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { setApiIdentity } from './api';
import type { Role } from './types';

export const ROLE_LABELS: Record<Role, string> = {
  employee: '员工',
  reviewer: '审核员',
  admin: '管理员',
};

export const ROLE_USER_IDS: Record<Role, string> = {
  employee: 'u_1002',
  reviewer: 'reviewer_demo',
  admin: 'admin_demo',
};

interface RoleContextValue {
  role: Role;
  userId: string;
  setRole: (role: Role) => void;
}

const RoleContext = createContext<RoleContextValue | null>(null);

function isRole(value: string | null): value is Role {
  return value === 'employee' || value === 'reviewer' || value === 'admin';
}

function readStoredRole(): Role {
  try {
    const stored = window.localStorage.getItem('auditflow.role');
    if (isRole(stored)) return stored;
  } catch {
    // 忽略隐私模式下的存储异常
  }
  return 'reviewer';
}

export function RoleProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<Role>(() => {
    const initial = readStoredRole();
    setApiIdentity(initial, ROLE_USER_IDS[initial]);
    return initial;
  });

  const setRole = useCallback((next: Role) => {
    setRoleState(next);
    setApiIdentity(next, ROLE_USER_IDS[next]);
    try {
      window.localStorage.setItem('auditflow.role', next);
    } catch {
      // 忽略存储异常
    }
  }, []);

  const value = useMemo<RoleContextValue>(
    () => ({ role, userId: ROLE_USER_IDS[role], setRole }),
    [role, setRole],
  );

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole(): RoleContextValue {
  const context = useContext(RoleContext);
  if (!context) throw new Error('useRole 必须在 RoleProvider 内使用');
  return context;
}
