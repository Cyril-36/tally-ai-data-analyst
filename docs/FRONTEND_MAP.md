# Frontend: screen → API map (Tally prototype → real backend)

Visual reference is `Tally design system.zip` (`Tally.dc.html`). **Design fidelity wins:** keep every prototype control, and where the backend lacks support, extend the backend (decided 2026-10-02). Behaviour still comes from the real API: no fake toasts, invented numbers, or client-side business math. The prototype's `mock-api.js`, `support.js`, DC runtime and CDN React/Babel are **not** shipped.

All calls use the HttpOnly `sid` cookie (`credentials: "include"`). Every POST/PUT/PATCH/DELETE sends `X-Requested-With: fetch`. A 401 anywhere returns to sign-in, and the typed question is kept in memory (not localStorage). Admin status comes only from `GET /api/auth/me` → `role`.

| Screen (prototype `view`) | Prototype control | Backend | Status |
|---|---|---|---|
| Auth | sign in / sign up / sign out | `POST /api/auth/login`, `/signup`, `/logout`, `GET /api/auth/me` | exists |
| Ask | conversation list, new, rename, delete | `GET/POST /api/conversations`, `PATCH/DELETE /api/conversations/{id}` | exists |
| Ask | ask question, progress steps | `POST /api/conversations/{id}/ask` with `Accept: text/event-stream` (events: planning, tool_call, tool_result, executing, validating, then terminal `result` or `error`) | exists |
| Ask | answer: sentences, table, chart, SQL+params, caveats, source versions, anomalies, clarification | the stored `AnalysisResult` (`GET /api/results/{id}`) | exists |
| Ask | switch chart type | `GET/PATCH /api/results/{id}/chart` (same stored rows) | exists |
| Ask | export result (HTML/CSV), export conversation | `GET /api/results/{id}/export?format=html\|csv`, `GET /api/conversations/{id}/export` | exists |
| Ask | pin to dashboard | `POST /api/pins` with an `idempotency_key` | exists |
| Ask | suggested questions | `AnalysisResult.suggested_questions` only (no invented list) | exists |
| Dashboard | pinned cards, rename, reorder, remove, stale label, refresh | `GET /api/dashboard`, `PATCH/DELETE /api/pins/{id}`; refresh = re-ask the stored question in its conversation | exists |
| Data | list, upload (multi), replace, delete with dependents | `GET/POST /api/datasets`, `PUT /api/datasets/{id}`, `GET /{id}/dependents`, `DELETE /{id}?confirm=true` | exists |
| Data | **rename dataset** | `PATCH /api/datasets/{id}` `{name}` → dataset meta; 422 on clash/invalid | **gap → add** |
| Data | **preview pagination** | `GET /api/datasets/{id}/rows?offset=0&limit=50` → `{columns, rows, offset, limit, total}` (limit ≤ 200) | **gap → add** |
| Data | **load sample data** | `POST /api/datasets/sample` → list of created datasets (orders, customers, products, payments); reports existing ones instead of duplicating | **gap → add** |
| Quality | report per dataset | `GET /api/datasets/{id}/quality` | exists |
| Quality | **example rows per issue** | `GET /api/datasets/{id}/quality/examples?check=<nulls\|duplicates\|numeric_text\|variants\|future_dates\|negatives>&column=<col>` → up to 5 rows | **gap → add** |
| Anomalies | explorer (metric, grouping, run) | `POST /api/conversations/{id}/analyze` with `spec` + `anomaly_metric` → `AnalysisResult.anomalies` | exists |
| Anomalies | "expected range" (prototype rolling method) | **not shown.** Show median, MAD, robust z, threshold, reference population, `insufficient_data` reason, `excluded_values` | adapt |
| Settings (admin) | upload limit, **AI enable/disable switch** | `GET/PUT /api/admin/settings` | exists |
| Settings (admin) | **test AI provider** | `POST /api/admin/provider-test` → `{ok, latency_ms, model}` or `provider_failed`; admin only | **gap → add** |
| Settings (admin) | **allow sample data** toggle | `sample_data_enabled` in `/api/admin/settings`; `POST /api/datasets/sample` returns 403 when off | **gap → add** |
| Settings (admin) | **retention** (30/90/365 days, never) | `retention_days` in `/api/admin/settings`; conversations and results older than that are purged on startup and when the setting is saved (pins on them go too). Datasets are not purged | **gap → add** |
| Prototype only | mock scenarios panel, latency switch | none | **not shipped** (testing tool of the prototype) |
| Settings (any user) | change password | `POST /api/auth/change-password` | exists |

## Backend gaps (surgical; no change to authorization or calculation semantics)
1. `PATCH /api/datasets/{id}`: rename, using `require_owned` (404 for others), unique per owner, 1–128 chars.
2. `GET /api/datasets/{id}/rows`: owner-only. Paged read of the stored Parquet file, with `total`.
3. `POST /api/datasets/sample`: copies `data/sample/{orders,customers,products,payments}.csv` through the normal ingest path for the current user.
4. `GET /api/datasets/{id}/quality/examples`: owner-only, ≤ 5 example rows per check/column.
5. Admin settings: add `sample_data_enabled` (default true) and `retention_days` (default 90; `null` = never), and make both have real effects as above.
6. `POST /api/admin/provider-test`: admin-only, one minimal provider call; never echoes provider errors or keys.
7. **Server-side fake provider for E2E:** `LLM_PROVIDER=fake` selects a scripted provider that answers a fixed set of demo questions with real `AnalysisSpec`s (so the whole validated path runs) and asks a clarification for "underperforming". Enabled only by that env var; the server logs a warning at startup when it's on. It is never the default.
