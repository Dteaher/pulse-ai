import { requestScope } from './performance';

export async function api<T>(path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch('/api/' + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Pulse-Session': requestScope },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(500_000),
    });
  } catch {
    throw new Error(
      'Сервер недоступен или запрос занял слишком долго. Проверьте backend и повторите попытку.',
    );
  }
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.human_message === 'string'
        ? data.human_message
        : typeof data.detail === 'string'
          ? data.detail
          : 'Не удалось обработать запрос.',
    );
  return data as T;
}
