import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, setAccessToken, setApiIdentity } from './api';

/** 极简 Response 替身，避免 jsdom 环境依赖真实网络 */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    statusText: status === 400 ? 'Bad Request' : 'OK',
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

beforeEach(() => {
  setAccessToken('');
  setApiIdentity('employee', 'u_1002');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('请求身份', () => {
  it('切换角色后请求头同步更新', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    setApiIdentity('reviewer', 'u_2001');
    await api.listCases({ limit: 5 });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({ 'X-Role': 'reviewer', 'X-User-Id': 'u_2001' });
  });

  it('设置访问令牌后携带 Bearer 头', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);

    setAccessToken('signed-token');
    await api.systemInfo();

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer signed-token');
  });
});

describe('审批错误态', () => {
  it('后端 detail 透传为 ApiError 并保留状态码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: '案件状态不允许审批' }, 400)),
    );

    await expect(api.submitApproval('case_1', 'approve', '同意')).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      message: '案件状态不允许审批',
    });
  });

  it('网络失败转为带地址说明的 ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(api.listCases()).rejects.toMatchObject({ name: 'ApiError', status: 0 });
  });
});
