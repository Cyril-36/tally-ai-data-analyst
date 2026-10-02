import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { conversations as convApi, datasets as dsApi, pins as pinsApi } from '../api/endpoints';
import type { AnalysisResult, Conversation, DashboardItem, DatasetMeta, QualityReport } from '../api/types';
import { useToast } from './toast';

export type QualityEntry = { state: 'loading' } | { state: 'ready'; report: QualityReport } | { state: 'error'; message: string };

interface WorkspaceApi {
  datasets: DatasetMeta[] | null;
  datasetsError: string | null;
  refreshDatasets: (opts?: { selectNew?: boolean }) => Promise<DatasetMeta[]>;
  addDataset: (meta: DatasetMeta) => void;
  patchDataset: (meta: DatasetMeta) => void;
  dropDataset: (id: string) => void;

  selectedIds: string[];
  toggleSelected: (id: string) => void;
  setSelectedIds: (ids: string[]) => void;

  conversations: Conversation[];
  setConversations: (fn: (c: Conversation[]) => Conversation[]) => void;
  refreshConversations: () => Promise<void>;

  pins: DashboardItem[] | null;
  refreshPins: () => Promise<DashboardItem[]>;
  pinResult: (result: AnalysisResult) => Promise<void>;

  quality: Record<string, QualityEntry>;
  qualityKey: (d: DatasetMeta) => string;
  qualityFindingCount: number;

  loadSample: () => Promise<void>;

  /** The question being typed. Kept in memory so a 401 mid-session never loses it. */
  draft: string;
  setDraft: (text: string) => void;
}

const Ctx = createContext<WorkspaceApi | null>(null);

export function useWorkspace(): WorkspaceApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useWorkspace outside WorkspaceProvider');
  return ctx;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong.');

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { toast } = useToast();
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetMeta[] | null>(null);
  const [datasetsError, setDatasetsError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [conversations, setConversationsState] = useState<Conversation[]>([]);
  const [pins, setPins] = useState<DashboardItem[] | null>(null);
  const [quality, setQuality] = useState<Record<string, QualityEntry>>({});
  const [draft, setDraft] = useState('');
  const known = useRef<Set<string>>(new Set());
  const requested = useRef<Set<string>>(new Set());

  const qualityKey = useCallback((d: DatasetMeta) => `${d.id}@${d.version}`, []);

  const applyDatasets = useCallback((list: DatasetMeta[], selectNew: boolean) => {
    setDatasets(list);
    const ids = new Set(list.map((d) => d.id));
    // Computed before the updater runs: `known` is reassigned right below.
    const fresh = list.filter((d) => !known.current.has(d.id)).map((d) => d.id);
    setSelectedIds((prev) => {
      const kept = prev.filter((id) => ids.has(id));
      return selectNew ? [...kept, ...fresh.filter((id) => !kept.includes(id))] : kept;
    });
    known.current = ids;
  }, []);

  const refreshDatasets = useCallback(
    async (opts: { selectNew?: boolean } = {}) => {
      try {
        const list = await dsApi.list();
        applyDatasets(list, opts.selectNew ?? false);
        setDatasetsError(null);
        return list;
      } catch (e) {
        setDatasetsError(message(e));
        setDatasets((cur) => cur ?? []);
        return [];
      }
    },
    [applyDatasets],
  );

  const refreshConversations = useCallback(async () => {
    try {
      setConversationsState(await convApi.list());
    } catch {
      /* the sidebar simply shows no conversations; a 401 is handled globally */
    }
  }, []);

  const refreshPins = useCallback(async () => {
    try {
      const list = await pinsApi.dashboard();
      setPins(list);
      return list;
    } catch {
      setPins((cur) => cur ?? []);
      return [];
    }
  }, []);

  // Initial load; a first load selects every dataset for analysis.
  useEffect(() => {
    void refreshDatasets({ selectNew: true });
    void refreshConversations();
    void refreshPins();
  }, [refreshDatasets, refreshConversations, refreshPins]);

  // Quality reports are cached per dataset version and feed the sidebar count and Data screen.
  useEffect(() => {
    if (!datasets) return;
    for (const d of datasets) {
      const key = qualityKey(d);
      if (requested.current.has(key)) continue;
      requested.current.add(key);
      setQuality((q) => ({ ...q, [key]: { state: 'loading' } }));
      dsApi
        .quality(d.id)
        .then((report) => setQuality((q) => ({ ...q, [key]: { state: 'ready', report } })))
        .catch((e) => setQuality((q) => ({ ...q, [key]: { state: 'error', message: message(e) } })));
    }
  }, [datasets, qualityKey]);

  const qualityFindingCount = useMemo(
    () =>
      (datasets ?? []).reduce((sum, d) => {
        const entry = quality[qualityKey(d)];
        return sum + (entry?.state === 'ready' ? entry.report.issues.length : 0);
      }, 0),
    [datasets, quality, qualityKey],
  );

  const addDataset = useCallback(
    (meta: DatasetMeta) => {
      known.current.add(meta.id);
      setDatasets((cur) => (cur && cur.some((d) => d.id === meta.id) ? cur : [...(cur ?? []), meta]));
      setSelectedIds((s) => (s.includes(meta.id) ? s : [...s, meta.id]));
    },
    [],
  );
  const patchDataset = useCallback((meta: DatasetMeta) => {
    setDatasets((cur) => (cur ?? []).map((d) => (d.id === meta.id ? meta : d)));
  }, []);
  const dropDataset = useCallback((id: string) => {
    known.current.delete(id);
    setDatasets((cur) => (cur ?? []).filter((d) => d.id !== id));
    setSelectedIds((s) => s.filter((x) => x !== id));
  }, []);

  const toggleSelected = useCallback((id: string) => {
    setSelectedIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }, []);

  const pinResult = useCallback(
    async (result: AnalysisResult) => {
      try {
        await pinsApi.create(result.id, `pin:${result.id}`);
        await refreshPins();
        toast('Pinned to your dashboard.', { action: 'View dashboard', onAction: () => navigate('/dashboard') });
      } catch (e) {
        toast(`Could not pin this result: ${message(e)}`, { tone: 'error', ms: 7000 });
      }
    },
    [navigate, refreshPins, toast],
  );

  const loadSample = useCallback(async () => {
    try {
      const { created } = await dsApi.sample();
      await refreshDatasets({ selectNew: true });
      toast(created.length ? `Sample data loaded: ${created.map((d) => d.filename ?? d.name).join(', ')}.` : 'Sample data is already loaded.');
    } catch (e) {
      toast(`Could not load the sample data: ${message(e)}`, { tone: 'error', ms: 7000 });
    }
  }, [refreshDatasets, toast]);

  const setConversations = useCallback((fn: (c: Conversation[]) => Conversation[]) => setConversationsState(fn), []);

  const value = useMemo<WorkspaceApi>(
    () => ({
      datasets,
      datasetsError,
      refreshDatasets,
      addDataset,
      patchDataset,
      dropDataset,
      selectedIds,
      toggleSelected,
      setSelectedIds,
      conversations,
      setConversations,
      refreshConversations,
      pins,
      refreshPins,
      pinResult,
      quality,
      qualityKey,
      qualityFindingCount,
      loadSample,
      draft,
      setDraft,
    }),
    [
      datasets,
      datasetsError,
      refreshDatasets,
      addDataset,
      patchDataset,
      dropDataset,
      selectedIds,
      toggleSelected,
      conversations,
      setConversations,
      refreshConversations,
      pins,
      refreshPins,
      pinResult,
      quality,
      qualityKey,
      qualityFindingCount,
      loadSample,
      draft,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
