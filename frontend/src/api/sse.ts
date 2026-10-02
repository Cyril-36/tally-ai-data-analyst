import { ApiError, errorFromResponse, rawFetch } from './client';
import type { AnalysisResult, ErrorBody, StreamEvent } from './types';

export interface SSEMessage {
  event: string;
  data: string;
}

/**
 * Incremental Server-Sent-Events parser. Feed it arbitrary text chunks (a chunk may end
 * mid-line or mid-event); it returns the events that completed. Comment lines (": keep-alive")
 * are ignored, CRLF / LF / CR line endings are accepted, and multi-line `data:` is joined.
 */
export class SSEParser {
  private buffer = '';
  private event = '';
  private data: string[] = [];
  private sawCR = false;

  push(chunk: string): SSEMessage[] {
    const out: SSEMessage[] = [];
    let text = this.buffer + chunk;
    this.buffer = '';
    // A "\r" at the very end of the previous chunk may be the first half of "\r\n".
    if (this.sawCR && text.startsWith('\n')) text = text.slice(1);
    this.sawCR = false;
    let start = 0;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch !== '\n' && ch !== '\r') continue;
      const line = text.slice(start, i);
      if (ch === '\r') {
        if (text[i + 1] === '\n') i++;
        else if (i === text.length - 1) this.sawCR = true;
      }
      start = i + 1;
      const done = this.line(line);
      if (done) out.push(done);
    }
    this.buffer = text.slice(start);
    return out;
  }

  private line(line: string): SSEMessage | null {
    if (line === '') {
      if (!this.data.length && !this.event) return null;
      const message = { event: this.event || 'message', data: this.data.join('\n') };
      this.event = '';
      this.data = [];
      return message;
    }
    if (line.startsWith(':')) return null;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.event = value;
    else if (field === 'data') this.data.push(value);
    return null;
  }
}

const PROGRESS = new Set(['planning', 'tool_call', 'tool_result', 'executing', 'validating']);

export function toStreamEvent(message: SSEMessage): StreamEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(message.data);
  } catch {
    return null;
  }
  if (message.event === 'result') return { type: 'result', data: data as AnalysisResult };
  if (message.event === 'error') return { type: 'error', data: data as ErrorBody };
  if (PROGRESS.has(message.event)) {
    return { type: message.event as 'planning', data: data as Record<string, unknown> };
  }
  return null;
}

export interface AskBody {
  question: string;
  dataset_ids: string[];
  request_key: string;
}

/**
 * POST /api/conversations/{id}/ask as an SSE stream (EventSource cannot POST).
 * Calls `onProgress` for each progress event and resolves with the terminal event.
 * HTTP-level refusals that arrive before the stream starts (404, 409, 429, 401, 422)
 * reject with an ApiError.
 */
export async function streamAsk(
  conversationId: string,
  body: AskBody,
  onProgress: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<StreamEvent & { type: 'result' | 'error' }> {
  const res = await rawFetch(
    'POST',
    `/api/conversations/${encodeURIComponent(conversationId)}/ask`,
    { json: body, signal },
    'text/event-stream',
  );
  if (!res.ok) throw await errorFromResponse(res);
  const type = res.headers.get('Content-Type') ?? '';
  if (!type.includes('text/event-stream') || !res.body) {
    // Some deployments reply with plain JSON (a replayed result): treat it as the terminal event.
    const json = (await res.json()) as AnalysisResult;
    return { type: 'result', data: json };
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  for (;;) {
    const { value, done } = await reader.read();
    const chunk = done ? decoder.decode() : decoder.decode(value, { stream: true });
    for (const message of parser.push(done ? chunk + '\n\n' : chunk)) {
      const event = toStreamEvent(message);
      if (!event) continue;
      if (event.type === 'result' || event.type === 'error') {
        void reader.cancel().catch(() => undefined);
        return event;
      }
      onProgress(event);
    }
    if (done) break;
  }
  throw new ApiError(0, 'network', 'The connection closed before the answer finished. Retry the question.');
}
