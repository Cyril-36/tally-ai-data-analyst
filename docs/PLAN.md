# AI Data Analyst — Plan (v3, authoritative)

> **This file is the single source of truth.** If the vault copy (`Brain/02-Projects/dbo-ai-data-analyst/PLAN.md`) disagrees, this file wins. Module names below are *planned* responsibilities; the acceptance checks are what define correctness. Update the mapping when the implementation moves.
>

## 1. Principles
1. **The LLM chooses the route; deterministic code computes every number.** Numbers in prose are rendered from validated claims, never typed by the model.
2. **One `AnalysisResult` is the source of truth.** The answer, table, chart, SQL, dashboard card and export are all views of the same stored result.
3. **Authorization is enforced in the backend on every path:** HTTP routes, agent tool calls, cache reads, chart/report/download endpoints.
4. **Failure outcomes are explicit and never disguised as success:** `permission_denied | not_found | needs_clarification | unsupported | insufficient_data | execution_failed | provider_failed | narrative_unverified`.
5. **Verification beats feature count.** An optional feature only ships if it has a passing acceptance check.

## 2. Stack (decided)
FastAPI + Pydantic v2 · DuckDB · pandas · SQLite (users, sessions, results, cache) · React + TypeScript + Vite + Tailwind + shadcn/ui · **Plotly** (the only chart library) · pytest + Playwright · Docker for the backend; the frontend is integrated when it is ready · `uv.lock` committed and installed with `--frozen` (the frontend will own its lockfile).
**LLM: one provider**, chosen only after a tool-calling smoke test proves access and quota. The adapter interface exists, but there is no second provider in the acceptance gate.

## 3. Query path: structured only (v1)
`question → planner (LLM, tool calling) → AnalysisSpec (Pydantic) → validator (schema + permissions) → compiler (sqlglot AST) → sandboxed executor → AnalysisResult`

- **The `AnalysisSpec` grammar covers:**
  - metric = aggregate (`sum|avg|count|count_distinct|min|max|median`) over a column or a safe arithmetic expression of columns
  - filters (`=, !=, in, between, >, <`, date ranges)
  - up to 2 group-by dimensions
  - a time bucket (`day|week|month|quarter|year`)
  - sort + limit (top-k)
  - joins along approved relationships only (see Join rule)
  - period-over-period comparison derived from complete time-bucket results
- **Identifiers** are validated against the dataset schema and quoted by the compiler. **Values** are always bound parameters, never interpolated.
- **Unsupported questions** → `needs_clarification` or `unsupported`, with what's missing. No free-form SQL in v1. LLM-written SQL is a post-v1 extension and needs its own sandbox and test suite.
- **Join rule (refusal, not a warning).** `check_join` measures cardinality on the actual data: key uniqueness on each side, plus null and orphan rates.
  - **Allowed:** one-to-one, or many-to-one where the *dimension* side's key is unique (e.g. `orders.customer_id → customers.customer_id`). The metric grain is the fact table's.
  - **Refused (`unsupported`, with an explanation):** one-to-many or many-to-many from the metric's table, e.g. one £100 order joined to 2 payment rows would sum to £200.
  - **The only exception:** the spec uses an explicit `pre_aggregate` that rolls the many side up to the join key first (e.g. `sum(payments.amount) by order_id`), so the grain is preserved.
  - Orphan or null keys become caveats with counts.
  - Tests: a fixture with a duplicated dimension key is refused; the payments fan-out fixture is refused without `pre_aggregate` and correct with it.
- **Displayed SQL** is the exact SQL that ran, with parameters shown.
- **pandas code** is emitted by the *same deterministic compiler* from the spec. It ships only if a test executes it on the fixtures and matches the DuckDB result for every golden spec; otherwise it's omitted (SQL alone satisfies the brief).

### Executor isolation (applies even to compiled SQL)
Threat model: v1 runs **only compiler-generated SQL from validated specs** (identifiers checked against the schema, values bound as parameters). Isolation is therefore *defense in depth*. We name the tier we actually achieve and never claim more.

**Tier A, always on (any deployment, incl. single-image demo):**
- The worker is a fresh subprocess per request, started with an **empty environment** (only `PATH` and `TZ`; no API keys, no `DATABASE_URL`), `cwd` set to an empty temp dir, and a non-root uid in the container.
- The parent copies only the authorized datasets (by id + version) into the request's temp dir as Parquet. The worker loads them into in-memory DuckDB, then runs:
  - `SET enable_external_access=false`
  - `SET autoinstall_known_extensions=false`
  - `SET autoload_known_extensions=false`
  - `SET lock_configuration=true`
  - `memory_limit`, `threads=2`
- Linux `setrlimit`: `RLIMIT_AS` (hard memory cap), `RLIMIT_CPU`, `RLIMIT_NOFILE`, `RLIMIT_FSIZE=0`.
- The parent enforces a wall-clock timeout with `SIGKILL`, then deletes the temp dir in `finally`. One worker per request means no state is shared between sessions.

**Tier B, after the v1 backend gate (not currently shipped):**
- The executor is its own service with `network_mode: none`, `read_only: true` rootfs, a `tmpfs` scratch, `mem_limit`/`cpus`/`pids_limit`, `cap_drop: [ALL]`, `no-new-privileges`, and no secrets in its env.
- It receives Parquet + spec from the API over a Unix socket on a shared volume.

**Tests:**
- `read_csv('/etc/passwd')`, `COPY … TO`, `INSTALL`/`LOAD`, `ATTACH` and `SET enable_external_access=true` fail in the worker.
- `os.environ` in the worker contains no secret keys.
- A memory-bomb query is killed and the temp dir is gone afterwards.
- A timeout kills the worker and returns `execution_failed`.
- Two concurrent sessions can't see each other's tables.
- Tier B only, in CI: an outbound connect from the executor container fails.

The current Docker Compose deployment uses Tier A in a non-root container with a read-only root filesystem, dropped capabilities, process and memory limits. The README states the achieved tier per deployment target. A separate network-isolated executor is future hardening.

## 4. Agent loop (adaptive, bounded)
- **Steps:** interpret → inspect schema → decide (clarify / derive metric / check join / check quality / analyze / stop) → tool → inspect result → refine or stop.
- **Bounds:** ≤ 6 tool calls, ≤ 2 repair attempts, wall-clock cap, token cap.
- **Tools:** `list_datasets`, `inspect_dataset`, `check_data_quality`, `check_join`, `run_analysis(spec)`, `detect_anomalies(spec)`, `make_chart(result_id, chart_spec)`.
- **Conversation context** is stored as resolved state (active datasets, last spec, active filters), not just chat text. A follow-up like "only for West" edits the previous spec.

## 5. Claims, not free prose
- The composer *selects and orders* `claims[]`: `{id, kind, entity, metric, filters, period, value, unit, source}`.
  - `kind ∈ value | rank | comparison | share | trend`.
  - `source = {result_id, row_key, column}`, or `{calc: op, inputs: [claim ids]}` for percentages, differences and ratios.
- The validator re-derives every claim from the `result_table`: entity, metric, period, filters *and* value, not just the number. It also recomputes the calcs.
- **Wording is part of the claim.** Every factual clause is rendered by a backend template per `kind`, from the validated fields: "**West** had the highest **revenue** in **Q3 2026**: **£100,000** (vs **£80,000** for East)". The model never writes an entity, metric, period, number, unit or comparative word that the user sees.
- **Evidence for qualitative words:**
  - `rank` ("highest", "top 5") needs the full ordering in the result.
  - `trend` ("declining", "growing") needs a computed change over ≥ 3 stated periods, with sign and size.
  - `comparison` needs both sides present.
  - Words without evidence aren't rendered.
- **Free model text in v1** is limited to suggested follow-up questions. These are checked against the schema and contain no numbers.
- If validation fails after one repair: return the verified table and chart plus "The written summary couldn't be validated against the results", with outcome `narrative_unverified`. **The failed narrative is not shown.**
- **Streaming:** progress events (`planning`, `tool_call`, `executing`, `validating`) stream immediately. **Answer text is emitted only after validation passes**, so unchecked text never flashes on screen and then gets withdrawn.
- **Caveats** come from *execution metadata*: excluded null rows, coerced types, row cap hit, join fan-out, derived-metric assumptions. They can't be dropped by the composer.

## 6. Output contract
| Field | Planned owner | Acceptance check | Failure outcome |
|---|---|---|---|
| `result_table` | `analysis/executor.py` | Executes in the sandbox; row count + timing recorded | `execution_failed` (no claimed numbers) |
| `sql` (+ params) | `analysis/compiler.py` | The string that ran; compiler golden tests | — |
| `pandas_code` | `analysis/compiler.py` | Equivalence test vs DuckDB on fixtures | Omitted |
| `claims` / `answer` | `agent/composer.py` + `agent/validator.py` | Every claim re-derived (entity, metric, period, filters, value) | `narrative_unverified` |
| `chart_spec` | `charts/builder.py` | Columns exist; type is in the allowed set for the data shape; plotted values = table values | Table + reason |
| `anomalies` | `analysis/anomalies.py` | Each flag has method, value, median, MAD, robust-z, threshold, row ref | `completed_none` / `completed_found` / `insufficient_data` / `execution_failed` (distinct) |
| `caveats` | executor metadata | Present whenever the metadata says so | Never empty by fallback |
| `clarification` | `agent/service.py` | Triggered on ambiguous metric/date (golden cases) | — |
| `export` | `reports/builder.py` | Built from stored `result_id`; permission rechecked | Discloses stale/missing dependencies |

## 7. Authorization matrix
Two rules, plus sharing (sharing is in the matrix so tests and rules agree, but it **ships only if time allows**; until then, every "shared" row behaves as "private").

| Actor / situation | Expected |
|---|---|
| Owner: any action on own dataset/session/result/dashboard/report | Allowed |
| Other user: **private** dataset, any action | `404 "Dataset not found or you don't have access"` (existence is not revealed) |
| Other user: dataset **shared for analysis**: query, chart, export own results | Allowed |
| Other user: shared dataset: rename, replace, delete, reshare | `403 "Only the dataset owner can delete it"` (no owner identity in the message) |
| Share revoked | Next query, saved result, dashboard card, export and download refused |
| Regular user: change system settings | `403 "Only an admin can change system settings"` |
| Admin: other users' data | Same as any other user (admin ≠ data access) |
| Agent tool call on an unauthorized dataset | Refused inside the tool; the loop stops with `permission_denied` (no retry, no fallback) |
| Cached result after access lost | Refused (permission checked before every cache read) |

Each row gets a test. Destructive actions in the UI need a confirmation dialog that lists dependents.

### Authentication mechanics (decided before implementing)
- **Passwords:** Argon2id (`argon2-cffi`), minimum 10 characters, never logged. Login failures return a generic "Invalid email or password". Login is rate-limited per IP + email.
- **Sessions:** a server-side session row in SQLite. The cookie holds a random 256-bit id (`HttpOnly`, `SameSite=Lax`, `Secure` when served over HTTPS).
  - Idle expiry 8h, absolute expiry 24h.
  - Logout deletes the row.
  - Changing a password deletes all of that user's sessions.
- **CSRF:** `SameSite=Lax` plus a required `X-Requested-With` header on state-changing requests.
- **Signup:** open, for the `analyst` role only.
- **Admins:** never self-provisioned. They are created only by the CLI (`uv run app create-admin --email …`, password prompted) or by the startup seed from `ADMIN_EMAIL` + `ADMIN_PASSWORD` env vars, when set. **No API path grants admin.**
- **Demo accounts:** `analyst1@example.com`, `analyst2@example.com`, `admin@example.com`. Passwords live in `.env.example`/seed for local use only and are documented as demo-only.

## 8. Cache
`key = hash(canonical AnalysisSpec after context resolution, owner id, dataset ids + versions, executor version)`. Cache rows are owner-scoped, and permission is **rechecked on every read**. Replacing a dataset bumps its version (old keys stop matching), and pinned cards and reports show "Dataset changed — refresh required". There is no LLM-response cache in v1.

## 9. Anomaly detection (v1 = one method, done well)
- **Method:** robust z-score `|x − median| / (1.4826·MAD) > 3.5`.
- **Reference population** is explicit and recorded:
  - a time series is compared with *its own* history (per group when grouped, e.g. each region against its own months);
  - a cross-sectional comparison (products in a period) is compared with its peers in that period.
  - Groups are never pooled across different scales.
- **Applicability:** a series needs ≥ 8 non-null points and MAD > 0, otherwise `insufficient_data`, with the reason (too few points / constant series). Missing values are excluded and counted in caveats.
- **Explanations** are built from the stored evidence fields.
- **Tests:** constant series → `insufficient_data` (zero MAD); < 8 points → `insufficient_data`; NaNs excluded and counted; multi-group data where one group's normal level would be an outlier in the pooled data is *not* flagged; planted spikes are flagged.
- IQR, STL and IsolationForest are post-v1, each only with its own applicability test.

## 10. Feature scope
**Acceptance gate (must pass):**
- All 8 core requirements
- Auth + ownership (matrix above, minus sharing)
- Adaptive tool calling with clarification
- Multi-file analysis with checked joins (key uniqueness, fan-out warning)
- Data-quality checks
- Dashboard pinning
- HTML report + CSV export
- SSE progress streaming
- Correct caching
- Structured logs
- Reproducible evaluation

**Post-gate, in order:** forecasting (one series, explicit frequency, missing-period handling, naive baseline, backtest; bands only with a justified method) → sharing → pandas code if not already passing → PDF export → semantic column search → LLM-written SQL (sandboxed) → eval UI / admin trace view.

## 11. Evaluation
- **Deterministic suite (pytest, no LLM), in CI:** compiler goldens, executor sandbox escapes (`read_csv('/etc/passwd')`, `COPY`, `INSTALL`, `ATTACH` all refused), the authorization matrix, cache invalidation, join fan-out, anomaly outcomes, export consistency, idempotent retries, concurrent sessions.
- **Live-model eval (`make eval`):** frozen config, 25–30 golden questions including all six from the brief, every case reported. Output goes to `evals/reports/<date>.{json,md}`.
  - **Each report records:** model identifier, temperature, prompt/config hash, fixture file hashes (sha256), `REFERENCE_DATE`, app git SHA (+ dirty flag), and run timestamp.
  - A run with a dirty tree is labelled as such and can't feed the README table.
- **Provider smoke test ≠ quota.** It proves one tool-calling request and one structured-output request succeeded. Quota and rate limits are read from the provider's dashboard or headers and recorded separately, or stated as unknown.
- **Metrics:**
  - spec correctness
  - answer correctness (claims match ground truth)
  - clarification precision/recall on ambiguous cases
  - chart **validity** (allowed set + values match), not exact type match
  - anomaly **precision and recall** against planted anomalies
  - narrative-validation pass rate
  - latency, tokens, cost
- **The README numbers are generated from the latest report.** Coverage % is reported, not used as a gate.

## 12. Data and dates
- **Fixtures** (tests and eval) are generated with a fixed seed and a fixed `REFERENCE_DATE=2026-09-30`; they are reproducible byte-for-byte.
- **Demo data** uses the same generator with today's date by default, or `--reference-date` to pin a walkthrough, so "last month" stays meaningful. The reference date is recorded in `ANOMALIES.md` and every eval report.
- Fictional names only, `@example.com` emails, GBP, documented metric definitions (revenue = quantity × unit_price − discount_amount).
- Planted anomalies are listed in `data/fixtures/ANOMALIES.md` (ground truth).

## 13. Monitoring → response (per failure class)
| Signal | Response |
|---|---|
| Provider errors / timeouts | Bounded retry, then `provider_failed`. A model fallback only if a second provider is verified. The admin kill switch disables LLM calls; profiling, quality checks, anomalies and dashboards keep working |
| Permission denials | Terminate the action; log; never retried or rerouted |
| Narrative validation failures | Suppress the narrative; show the verified table; track the rate |
| Execution failures / timeouts | `execution_failed` with the reason; track per spec shape |
| p95 latency, cost/answer, cache hit rate | Dashboard in the logs; alert thresholds documented |

## 14. Schedule
- **Day 1:**
  - LLM smoke test
  - contracts frozen (`AnalysisSpec`, `AnalysisResult`, claims, SSE events, outcomes, authorization matrix)
  - scaffold + CI
  - fixtures + golden questions
  - **auth + ownership enforcement + matrix tests**
  - sandboxed executor
  - **Exit:** one end-to-end flow (upload → question → validated answer + SQL + chart), and `make eval` runs, even if scores are low.
- **Day 2:**
  - remaining core features
  - adaptive loop + clarification
  - joins, quality, anomalies, dashboard pinning, export, streaming, cache
  - **eval re-run after each feature merge**
  - **Exit:** acceptance gate passes locally.
- **Day 3:**
  - **feature freeze at start**
  - interaction, security and concurrency tests; Playwright flow
  - review agents; Docker; deploy
  - README, diagram, screenshots, 10–30s video
  - Last 6h reserved for defects and delivery. Post-gate features only if all of this is done.

## 15. Development evidence
- **The original is kept private** and never edited.
- Other tools' export support is unverified; check before relying on it, and disclose any gaps rather than reconstructing.
