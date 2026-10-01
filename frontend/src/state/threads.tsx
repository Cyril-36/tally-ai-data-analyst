import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { conversations as convApi, results as resultsApi } from '../api/endpoints';
import { streamAsk } from '../api/sse';
import type { AnalysisResult, ChartSpec, Outcome, ResultStatus, StreamEvent } from '../api/types';
import { truncate } from '../lib/format';
import { useToast } from './toast';
import { useWorkspace } from './workspace';

export interface Step {
  id: string;
  label: string;
  detail: string;
  state: 'active' | 'done';
}

export interface EntryError {
  outcome: Outcome | 'network';
  message: string;
}

export interface ThreadEntry {
  id: string;
  question: string;
  requestKey: string;
  datasetIds: string[];
  status: 'running' | 'done';
  steps: Step[];
  result?: AnalysisResult;
  apiStatus?: ResultStatus;
  error?: EntryError;
  startedAt: number;
  finishedAt?: number;
  /** The clarification option (or typed answer) that followed this entry. */
  chosen?: string;
  ui: { details?: boolean; allRows?: boolean; copied?: boolean };
}

interface ThreadsApi {
  threads: Record<string, ThreadEntry[]>;
  threadErrors: Record<string, string>;
  loadThread: (conversationId: string) => Promise<void>;
  /** True while any question is streaming. */
  running: boolean;
  needSelect: boolean;
  clearNeedSelect: () => void;
  ask: (question: string, conversationId: string | null, retryOf?: ThreadEntry) => Promise<void>;
  patchEntry: (conversationId: string, entryId: string, fn: (e: ThreadEntry) => ThreadEntry) => void;
  changeChart: (conversationId: string, entry: ThreadEntry, chart: ChartSpec) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
}

const Ctx = createContext<ThreadsApi | null>(null);

export function useThreads(): ThreadsApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useThreads outside ThreadsProvider');
  return ctx;
}

const TOOL_LABELS: Record<string, string> = {
  list_datasets: 'Looking at your datasets',
  inspect_dataset: 'Inspecting columns',
  check_data_quality: 'Checking data quality',
  check_join: 'Checking the relationship between files',
  run_analysis: 'Running the analysis',
  detect_anomalies: 'Looking for unusual values',
  make_chart: 'Choosing a chart',
  finish: 'Selecting the result',
  clarify: 'Asking for clarification',
  unsupported: 'Checking what is supported',
};

function stepFromEvent(event: StreamEvent, index: number): Omit<Step, 'state'> | null {
  const d = event.data as Record<string, unknown>;
  const id = `s${index}`;
  switch (event.type) {
    case 'planning':
      return { id, label: 'Planning', detail: String(d.step ?? '') };
    case 'tool_call': {
      const tool = String(d.tool ?? '');
      const args = d.args as Record<string, unknown> | undefined;
      const dataset = args && typeof args.dataset === 'string' ? args.dataset : '';
      return { id, label: TOOL_LABELS[tool] ?? tool.replace(/_/g, ' '), detail: dataset };
    }
    case 'executing':
      return { id, label: 'Running the calculation', detail: typeof d.sql === 'string' ? d.sql.replace(/\s+/g, ' ') : String(d.step ?? '') };
    case 'validating':
      return {
        id,
        label: 'Validating the answer',
        detail: typeof d.claims === 'number' ? `${d.claims} claim${d.claims === 1 ? '' : 's'}` : String(d.step ?? ''),
      };
    default:
      return null;
  }
}

function newKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

export function ThreadsProvider({ children }: { children: ReactNode }) {
  const ws = useWorkspace();
  const { toast } = useToast();
  const navigate = useNavigate();
  const [threads, setThreads] = useState<Record<string, ThreadEntry[]>>({});
  const [threadErrors, setThreadErrors] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [needSelect, setNeedSelect] = useState(false);
  const loading = useRef(new Set<string>());
  const stepCounter = useRef(0);
  const wsRef = useRef(ws);
  wsRef.current = ws;

  const patchEntry = useCallback((cid: string, eid: string, fn: (e: ThreadEntry) => ThreadEntry) => {
    setThreads((t) => ({ ...t, [cid]: (t[cid] ?? []).map((e) => (e.id === eid ? fn(e) : e)) }));
  }, []);

  const loadThread = useCallback(async (cid: string) => {
    if (loading.current.has(cid)) return;
    loading.current.add(cid);
    try {
      const stored = await convApi.results(cid);
      setThreadErrors((m) => {
        const { [cid]: _drop, ...rest } = m;
        return rest;
      });
      setThreads((t) => {
        if (t[cid]) return t; // a live (in-memory) thread is never overwritten by a reload
        const entries: ThreadEntry[] = stored.map(({ result, status }, i) => ({
          id: `h${i}-${result.id}`,
          question: result.question,
          requestKey: result.id,
          datasetIds: [],
          status: 'done',
          steps: [],
          result,
          apiStatus: status,
          startedAt: Date.parse(result.created_at) || Date.now(),
          finishedAt: Date.parse(result.created_at) || Date.now(),
          ui: {},
        }));
        // A clarification that a later question already answered is shown as answered.
        entries.forEach((e, i) => {
          if (e.result?.outcome === 'needs_clarification' && entries[i + 1]) e.chosen = entries[i + 1].question;
        });
        return { ...t, [cid]: entries };
      });
      const conv = wsRef.current.conversations.find((c) => c.id === cid);
      const ids = conv?.state.dataset_ids;
      if (ids?.length && wsRef.current.datasets) {
        const present = new Set(wsRef.current.datasets.map((d) => d.id));
        wsRef.current.setSelectedIds(ids.filter((id) => present.has(id)));
      }
    } catch (e) {
      const text = e instanceof ApiError ? e.message : 'This conversation could not be loaded.';
      setThreadErrors((m) => ({ ...m, [cid]: text }));
    } finally {
      loading.current.delete(cid);
    }
  }, []);

  const ask = useCallback(
    async (question: string, conversationId: string | null, retryOf?: ThreadEntry) => {
      const text = question.trim();
      const current = wsRef.current;
      if (!text) return;
      const datasetIds = retryOf?.datasetIds.length ? retryOf.datasetIds : current.selectedIds;
      if (!datasetIds.length) {
        current.setDraft(text);
        setNeedSelect(true);
        return;
      }
      setNeedSelect(false);
      setRunning(true);

      let cid = conversationId;
      const entryId = retryOf?.id ?? `q${++stepCounter.current}-${Date.now()}`;
      const requestKey = retryOf?.requestKey ?? newKey();
      const fresh: ThreadEntry = {
        id: entryId,
        question: text,
        requestKey,
        datasetIds,
        status: 'running',
        steps: [],
        startedAt: Date.now(),
        ui: {},
      };

      try {
        if (!cid) {
          const created = await convApi.create(truncate(text, 64), datasetIds);
          cid = created.id;
          current.setConversations((list) => [created, ...list]);
          setThreads((t) => ({ ...t, [created.id]: [] }));
          navigate(`/ask/${created.id}`, { replace: true });
        }
        const convId = cid;
        setThreads((t) => {
          const list = t[convId] ?? [];
          const answered = list.map((e, i) =>
            i === list.length - 1 && e.result?.outcome === 'needs_clarification' && !e.chosen && !retryOf ? { ...e, chosen: text } : e,
          );
          const next = retryOf ? answered.map((e) => (e.id === entryId ? fresh : e)) : [...answered, fresh];
          return { ...t, [convId]: next };
        });
        current.setDraft('');

        const onProgress = (event: StreamEvent) => {
          const step = stepFromEvent(event, ++stepCounter.current);
          if (event.type === 'tool_result') {
            const d = event.data as { ok?: boolean; summary?: string };
            patchEntry(convId, entryId, (e) => ({
              ...e,
              steps: e.steps.map((s, i) => (i === e.steps.length - 1 && d.ok === false ? { ...s, detail: String(d.summary ?? s.detail) } : s)),
            }));
            return;
          }
          if (!step) return;
          patchEntry(convId, entryId, (e) => ({
            ...e,
            steps: [...e.steps.map((s) => ({ ...s, state: 'done' as const })), { ...step, state: 'active' as const }],
          }));
        };

        const terminal = await streamAsk(convId, { question: text, dataset_ids: datasetIds, request_key: requestKey }, onProgress);
        if (terminal.type === 'result') {
          patchEntry(convId, entryId, (e) => ({
            ...e,
            status: 'done',
            result: terminal.data,
            apiStatus: 'current',
            finishedAt: Date.now(),
            steps: e.steps.map((s) => ({ ...s, state: 'done' as const })),
          }));
        } else {
          patchEntry(convId, entryId, (e) => ({
            ...e,
            status: 'done',
            error: { outcome: terminal.data.outcome, message: terminal.data.message },
            finishedAt: Date.now(),
            steps: e.steps.map((s) => ({ ...s, state: 'done' as const })),
          }));
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          // Session expired: the sign-in overlay is already showing. Keep the question.
          current.setDraft(text);
          if (cid) {
            const convId = cid;
            setThreads((t) => ({ ...t, [convId]: (t[convId] ?? []).filter((x) => x.id !== entryId) }));
          }
        } else {
          const err: EntryError =
            e instanceof ApiError ? { outcome: e.outcome, message: e.message } : { outcome: 'execution_failed', message: 'Something went wrong.' };
          if (cid) {
            const convId = cid;
            setThreads((t) => {
              const list = t[convId] ?? [];
              const failed: ThreadEntry = { ...fresh, status: 'done', error: err, finishedAt: Date.now() };
              return { ...t, [convId]: list.some((x) => x.id === entryId) ? list.map((x) => (x.id === entryId ? { ...x, status: 'done', error: err, finishedAt: Date.now(), steps: x.steps.map((s) => ({ ...s, state: 'done' as const })) } : x)) : [...list, failed] };
            });
          } else {
            toast(err.message, { tone: 'error', ms: 7000 });
            current.setDraft(text);
          }
        }
      } finally {
        setRunning(false);
      }
    },
    [navigate, patchEntry, toast],
  );

  const changeChart = useCallback(
    async (cid: string, entry: ThreadEntry, chart: ChartSpec) => {
      if (!entry.result) return;
      const previous = entry.result;
      try {
        const updated = await resultsApi.setChart(previous.id, chart);
        patchEntry(cid, entry.id, (e) => (e.result ? { ...e, result: { ...e.result, chart: updated.chart } } : e));
      } catch (e) {
        toast(`Could not change the chart: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
      }
    },
    [patchEntry, toast],
  );

  const renameConversation = useCallback(
    async (id: string, title: string) => {
      try {
        const updated = await convApi.rename(id, title);
        wsRef.current.setConversations((list) => list.map((c) => (c.id === id ? updated : c)));
      } catch (e) {
        toast(`Could not rename: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
      }
    },
    [toast],
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      try {
        await convApi.remove(id);
        wsRef.current.setConversations((list) => list.filter((c) => c.id !== id));
        setThreads((t) => {
          const { [id]: _drop, ...rest } = t;
          return rest;
        });
        void wsRef.current.refreshPins();
        toast('Conversation deleted.');
        navigate('/ask');
      } catch (e) {
        toast(`Could not delete: ${e instanceof ApiError ? e.message : 'unexpected error'}`, { tone: 'error', ms: 7000 });
      }
    },
    [navigate, toast],
  );

  const value = useMemo<ThreadsApi>(
    () => ({
      threads,
      threadErrors,
      loadThread,
      running,
      needSelect,
      clearNeedSelect: () => setNeedSelect(false),
      ask,
      patchEntry,
      changeChart,
      renameConversation,
      deleteConversation,
    }),
    [threads, threadErrors, loadThread, running, needSelect, ask, patchEntry, changeChart, renameConversation, deleteConversation],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
