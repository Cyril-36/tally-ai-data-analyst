import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './client';
import { SSEParser, streamAsk, toStreamEvent } from './sse';
import type { StreamEvent } from './types';
import { okResult } from '../test/fixtures';

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

describe('SSEParser', () => {
  it('parses complete events', () => {
    const p = new SSEParser();
    const out = p.push(frame('planning', { step: 'a' }) + frame('executing', { sql: 'SELECT 1' }));
    expect(out.map((m) => m.event)).toEqual(['planning', 'executing']);
    expect(JSON.parse(out[1].data)).toEqual({ sql: 'SELECT 1' });
  });

  it('reassembles an event split at every possible byte boundary', () => {
    const text = frame('tool_call', { tool: 'inspect_dataset', args: { dataset: 'orders' } }) + frame('result', { id: 'r1' });
    for (let cut = 1; cut < text.length; cut++) {
      const p = new SSEParser();
      const out = [...p.push(text.slice(0, cut)), ...p.push(text.slice(cut))];
      expect(out.map((m) => m.event)).toEqual(['tool_call', 'result']);
      expect(JSON.parse(out[0].data).args.dataset).toBe('orders');
    }
  });

  it('survives one-character chunks', () => {
    const p = new SSEParser();
    const out = [...(frame('planning', { step: 'x' }) + frame('error', { outcome: 'provider_failed', message: 'm' }))].flatMap((ch) => p.push(ch));
    expect(out.map((m) => m.event)).toEqual(['planning', 'error']);
  });

  it('ignores keep-alive comments and accepts CRLF, including a CR LF pair split across chunks', () => {
    const p = new SSEParser();
    expect(p.push(': keep-alive\n\n')).toEqual([]);
    const a = p.push('event: planning\r\ndata: {"step":"s"}\r');
    expect(a).toEqual([]);
    const b = p.push('\n\r\n');
    expect(b).toHaveLength(1);
    expect(b[0]).toEqual({ event: 'planning', data: '{"step":"s"}' });
  });

  it('joins multi-line data and defaults the event name', () => {
    const p = new SSEParser();
    const [m] = p.push('data: one\ndata: two\n\n');
    expect(m).toEqual({ event: 'message', data: 'one\ntwo' });
  });

  it('keeps an incomplete trailing event buffered until it finishes', () => {
    const p = new SSEParser();
    expect(p.push('event: result\ndata: {"id":')).toEqual([]);
    expect(p.push('"r1"}\n')).toEqual([]);
    expect(p.push('\n')).toEqual([{ event: 'result', data: '{"id":"r1"}' }]);
  });
});

describe('toStreamEvent', () => {
  it('maps known events and drops unknown or malformed ones', () => {
    expect(toStreamEvent({ event: 'planning', data: '{"step":"s"}' })).toEqual({ type: 'planning', data: { step: 's' } });
    expect(toStreamEvent({ event: 'result', data: JSON.stringify(okResult) })?.type).toBe('result');
    expect(toStreamEvent({ event: 'mystery', data: '{}' })).toBeNull();
    expect(toStreamEvent({ event: 'planning', data: 'not json' })).toBeNull();
  });
});

function sseResponse(chunks: string[], status = 200, type = 'text/event-stream'): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
  return new Response(stream, { status, headers: { 'Content-Type': type } });
}

describe('streamAsk', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('POSTs with the CSRF header and event-stream accept, streams progress, and resolves with the terminal result', async () => {
    const wire = frame('planning', { step: 'p' }) + frame('tool_call', { tool: 'run_analysis', args: {}, step: 1 }) + ': keep-alive\n\n' + frame('result', okResult);
    // split inside the JSON payload and inside a multi-byte character boundary
    const cut1 = 40;
    const cut2 = wire.length - 30;
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([wire.slice(0, cut1), wire.slice(cut1, cut2), wire.slice(cut2)]));
    vi.stubGlobal('fetch', fetchMock);
    const seen: StreamEvent['type'][] = [];
    const terminal = await streamAsk('c1', { question: 'q', dataset_ids: ['d1'], request_key: 'k1' }, (e) => seen.push(e.type));

    expect(seen).toEqual(['planning', 'tool_call']);
    expect(terminal.type).toBe('result');
    expect((terminal.data as typeof okResult).id).toBe(okResult.id);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/conversations/c1/ask');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(init.headers['X-Requested-With']).toBe('fetch');
    expect(init.headers.Accept).toBe('text/event-stream');
    expect(JSON.parse(init.body)).toEqual({ question: 'q', dataset_ids: ['d1'], request_key: 'k1' });
  });

  it('resolves with the error event as the terminal event', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([frame('planning', { step: 'p' }), frame('error', { outcome: 'provider_failed', message: 'AI is off' })])));
    const terminal = await streamAsk('c1', { question: 'q', dataset_ids: [], request_key: 'k' }, () => undefined);
    expect(terminal).toEqual({ type: 'error', data: { outcome: 'provider_failed', message: 'AI is off' } });
  });

  it('rejects with the API error when the request is refused before streaming', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ outcome: 'not_found', message: 'Conversation not found or you don\'t have access to it.' }), { status: 404, headers: { 'Content-Type': 'application/json' } })),
    );
    await expect(streamAsk('x', { question: 'q', dataset_ids: [], request_key: 'k' }, () => undefined)).rejects.toMatchObject({
      status: 404,
      outcome: 'not_found',
    });
  });

  it('rejects when the stream ends without a terminal event', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([frame('planning', { step: 'p' })])));
    await expect(streamAsk('c', { question: 'q', dataset_ids: [], request_key: 'k' }, () => undefined)).rejects.toBeInstanceOf(ApiError);
  });
});
