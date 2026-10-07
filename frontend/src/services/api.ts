import { requestScope } from './performance';

// Shared demo access code lives only in memory, never in browser storage.
let accessToken = '';
export function setAccessToken(value: string) {
  accessToken = value;
}
export function hasAccessToken() {
  return Boolean(accessToken);
}
export class APIError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
    public retryAfter?: number,
  ) {
    super(message);
    this.name = 'APIError';
  }
}
export async function api<T>(path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch('/api/' + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Pulse-Session': requestScope,
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(500_000),
    });
  } catch {
    throw new Error(
      'Сервер недоступен или запрос занял слишком долго. Повторите попытку чуть позже.',
    );
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new APIError(
      'Сервер вернул неожиданный ответ. Повторите попытку чуть позже.',
      response.status,
    );
  }
  if (!response.ok) {
    if (response.status === 401 && data.code === 'ACCESS_REQUIRED' && path !== 'access/check')
      window.dispatchEvent(new Event('pulse:access-required'));
    throw new APIError(
      typeof data.human_message === 'string'
        ? data.human_message
        : typeof data.detail === 'string'
          ? data.detail
          : 'Не удалось обработать запрос.',
      response.status,
      data.code,
      Number(response.headers.get('Retry-After')) || undefined,
    );
  }
  return data as T;
}
