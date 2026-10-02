import { ApiError, mutatingHeaders, notifyUnauthorized } from './client';
import type { DatasetMeta, ErrorBody } from './types';

/** multipart upload with progress (fetch cannot report upload progress, XHR can). */
function send<T>(method: 'POST' | 'PUT', url: string, file: File, onProgress: (pct: number) => void): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.withCredentials = true;
    xhr.setRequestHeader('Accept', 'application/json');
    for (const [k, v] of Object.entries(mutatingHeaders(method))) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.min(99, Math.round((e.loaded / e.total) * 100)));
    };
    xhr.onerror = () => reject(new ApiError(0, 'network', 'Could not reach the server. Check your connection and try again.'));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100);
        try {
          resolve(JSON.parse(xhr.responseText) as T);
        } catch {
          reject(new ApiError(xhr.status, 'execution_failed', 'The server sent an unreadable response.'));
        }
        return;
      }
      if (xhr.status === 401) notifyUnauthorized();
      let message = `Upload failed (${xhr.status}).`;
      let outcome: ErrorBody['outcome'] = 'execution_failed';
      try {
        const body = JSON.parse(xhr.responseText) as Partial<ErrorBody>;
        if (typeof body.message === 'string') message = body.message;
        if (body.outcome) outcome = body.outcome;
      } catch {
        /* keep generic message */
      }
      reject(new ApiError(xhr.status, outcome, message));
    };
    const form = new FormData();
    form.append(method === 'POST' ? 'files' : 'file', file, file.name);
    xhr.send(form);
  });
}

/** One file per request so a rejected file never discards one that succeeded. */
export async function uploadDataset(file: File, onProgress: (pct: number) => void): Promise<DatasetMeta> {
  const created = await send<DatasetMeta[]>('POST', '/api/datasets', file, onProgress);
  return created[0];
}

export function replaceDataset(datasetId: string, file: File, onProgress: (pct: number) => void): Promise<DatasetMeta> {
  return send<DatasetMeta>('PUT', `/api/datasets/${encodeURIComponent(datasetId)}`, file, onProgress);
}
