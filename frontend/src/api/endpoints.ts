import { del, download, get, patch, post, put, saveBlob } from './client';
import type {
  AnalysisResult,
  AnalysisSpec,
  ChartSpec,
  Conversation,
  DashboardItem,
  DatasetDetail,
  DatasetMeta,
  Dependents,
  Overview,
  Pin,
  ProviderTest,
  QualityReport,
  RowsPage,
  SampleLoadResult,
  StoredResult,
  SystemSettings,
  User,
} from './types';

const id = encodeURIComponent;

/* ---- auth ---- */
export const auth = {
  me: () => get<User>('/api/auth/me', { noSessionRedirect: true }),
  login: (email: string, password: string) =>
    post<User>('/api/auth/login', { email, password }, { noSessionRedirect: true }),
  signup: (email: string, password: string) =>
    post<User>('/api/auth/signup', { email, password }, { noSessionRedirect: true }),
  logout: () => post<void>('/api/auth/logout', {}, { noSessionRedirect: true }),
  changePassword: (current_password: string, new_password: string) =>
    post<void>('/api/auth/change-password', { current_password, new_password }),
};

/* ---- datasets ---- */
export const datasets = {
  list: () => get<DatasetMeta[]>('/api/datasets'),
  detail: (datasetId: string) => get<DatasetDetail>(`/api/datasets/${id(datasetId)}`),
  rows: (datasetId: string, offset: number, limit: number) =>
    get<RowsPage>(`/api/datasets/${id(datasetId)}/rows?offset=${offset}&limit=${limit}`),
  quality: (datasetId: string) => get<QualityReport>(`/api/datasets/${id(datasetId)}/quality`),
  qualityExamples: (datasetId: string, check: string, column: string | null) =>
    get<unknown>(
      `/api/datasets/${id(datasetId)}/quality/examples?check=${id(check)}${column ? `&column=${id(column)}` : ''}`,
    ),
  dependents: (datasetId: string) => get<Dependents>(`/api/datasets/${id(datasetId)}/dependents`),
  rename: (datasetId: string, name: string) => patch<DatasetMeta>(`/api/datasets/${id(datasetId)}`, { name }),
  setCurrency: (datasetId: string, currency: DatasetMeta['currency']) =>
    patch<DatasetMeta>(`/api/datasets/${id(datasetId)}`, { currency }),
  overview: (datasetId: string) => post<Overview>(`/api/datasets/${id(datasetId)}/overview`, {}),
  remove: (datasetId: string) => del<{ deleted: string }>(`/api/datasets/${id(datasetId)}?confirm=true`),
  sample: () => post<SampleLoadResult>('/api/datasets/sample'),
};

/* ---- conversations, results, pins ---- */
export const conversations = {
  list: () => get<Conversation[]>('/api/conversations'),
  create: (title: string, dataset_ids: string[]) => post<Conversation>('/api/conversations', { title, dataset_ids }),
  rename: (convId: string, title: string) => patch<Conversation>(`/api/conversations/${id(convId)}`, { title }),
  setDatasets: (convId: string, dataset_ids: string[]) =>
    patch<Conversation>(`/api/conversations/${id(convId)}`, { dataset_ids }),
  remove: (convId: string) => del<{ deleted: string }>(`/api/conversations/${id(convId)}`),
  results: (convId: string) => get<StoredResult[]>(`/api/conversations/${id(convId)}/results`),
  /** Non-streaming ask: used by the dashboard's "re-run with current data". */
  analyze: (
    convId: string,
    body: { question: string; spec: AnalysisSpec; anomaly_metric?: string; dataset_ids: string[]; request_key: string },
  ) => post<AnalysisResult>(`/api/conversations/${id(convId)}/analyze`, body),
};

export const results = {
  get: (resultId: string) => get<StoredResult>(`/api/results/${id(resultId)}`),
  setChart: (resultId: string, chart: ChartSpec) =>
    patch<{ chart: ChartSpec; figure: unknown }>(`/api/results/${id(resultId)}/chart`, { chart }),
};

export const pins = {
  dashboard: () => get<DashboardItem[]>('/api/dashboard'),
  create: (result_id: string, idempotency_key: string) => post<Pin>('/api/pins', { result_id, idempotency_key }),
  update: (pinId: string, body: { title?: string; position?: number }) => patch<Pin>(`/api/pins/${id(pinId)}`, body),
  remove: (pinId: string) => del<{ deleted: string }>(`/api/pins/${id(pinId)}`),
  /** Re-run the pin's stored calculation on current data (no AI). */
  refresh: (pinId: string) => post<DashboardItem>(`/api/pins/${id(pinId)}/refresh`, {}),
};

/* ---- exports (downloads) ---- */
export async function exportResult(resultId: string, format: 'csv' | 'html'): Promise<string> {
  const { blob, filename } = await download(`/api/results/${id(resultId)}/export?format=${format}`);
  const name = filename ?? (format === 'csv' ? `${resultId}.csv` : `report-${resultId}.html`);
  saveBlob(blob, name);
  return name;
}

export async function exportConversation(convId: string): Promise<string> {
  const { blob, filename } = await download(`/api/conversations/${id(convId)}/export`);
  const name = filename ?? `conversation-${convId}.html`;
  saveBlob(blob, name);
  return name;
}

/* ---- admin ---- */
export const admin = {
  getSettings: () => get<SystemSettings>('/api/admin/settings'),
  putSettings: (settings: SystemSettings) => put<SystemSettings>('/api/admin/settings', settings),
  testProvider: () => post<ProviderTest>('/api/admin/provider-test'),
  ready: () => get<{ status: string; ai_configured?: boolean }>('/api/ready'),
};
