import type { ErrorBody, Outcome } from './types';

/** Thrown for every non-2xx response and for network failures (status 0). */
export class ApiError extends Error {
  status: number;
  outcome: Outcome | 'network';
  constructor(status: number, outcome: Outcome | 'network', message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.outcome = outcome;
  }
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

let unauthorizedHandler: (() => void) | null = null;

/** The auth provider registers this; any 401 on a non-auth call calls it. */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

export function mutatingHeaders(method: string, extra: Record<string, string> = {}): Record<string, string> {
  return MUTATING.has(method) ? { 'X-Requested-With': 'fetch', ...extra } : { ...extra };
}

export async function errorFromResponse(res: Response): Promise<ApiError> {
  let outcome: Outcome = 'execution_failed';
  let message = `Request failed (${res.status}).`;
  try {
    const body = (await res.json()) as Partial<ErrorBody> & { detail?: unknown };
    if (typeof body.message === 'string') message = body.message;
    else if (typeof body.detail === 'string') message = body.detail;
    if (body.outcome) outcome = body.outcome;
  } catch {
    /* non-JSON error body: keep the generic message */
  }
  return new ApiError(res.status, outcome, message);
}

export interface RequestOptions {
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  /** Sign-in and sign-up treat 401 as "wrong credentials", not "session expired". */
  noSessionRedirect?: boolean;
}

export async function rawFetch(method: string, path: string, opts: RequestOptions = {}, accept?: string): Promise<Response> {
  const headers = mutatingHeaders(method, opts.json !== undefined ? { 'Content-Type': 'application/json' } : {});
  if (accept) headers.Accept = accept;
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: 'include',
      headers,
      body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.form,
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, 'network', 'Could not reach the server. Check your connection and try again.');
  }
  if (res.status === 401 && !opts.noSessionRedirect) notifyUnauthorized();
  return res;
}

export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await rawFetch(method, path, opts, 'application/json');
  if (!res.ok) throw await errorFromResponse(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T>(path: string, opts?: RequestOptions) => request<T>('GET', path, opts);
export const post = <T>(path: string, json?: unknown, opts?: RequestOptions) =>
  request<T>('POST', path, { ...opts, json: json ?? {} });
export const put = <T>(path: string, json: unknown, opts?: RequestOptions) => request<T>('PUT', path, { ...opts, json });
export const patch = <T>(path: string, json: unknown, opts?: RequestOptions) => request<T>('PATCH', path, { ...opts, json });
export const del = <T>(path: string, opts?: RequestOptions) => request<T>('DELETE', path, opts);

/** Fetch a file (CSV / HTML export) as a Blob plus the server's filename if given. */
export async function download(path: string): Promise<{ blob: Blob; filename: string | null }> {
  const res = await rawFetch('GET', path);
  if (!res.ok) throw await errorFromResponse(res);
  const disposition = res.headers.get('Content-Disposition') ?? '';
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  return { blob: await res.blob(), filename: match ? match[1] : null };
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 3000);
}
