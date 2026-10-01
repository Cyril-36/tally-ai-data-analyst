# Decisions

## 2026-10-01: plan v3 after two cross-review rounds
1. **No free-form LLM SQL in v1.** "SELECT-only + read-only connection" was wrong as a safety claim: DuckDB can read local files from a SELECT (`read_csv('/etc/passwd')`). v1 is structured spec → compiled SQL. The executor is still sandboxed (external access off, extensions off, config locked, worker process, timeouts), because filter values are user-influenced.
2. **Claims, not number-presence.** "Every number appears in the table" passes "East earned 100" when West earned 100. Claims bind entity + metric + period + filters + value to a row/column or an explicit calc. Prose numbers are rendered by the backend. A failed narrative is suppressed, not shown labelled "unverified".
3. **The cache key is the resolved spec** (after conversation context), plus dataset versions; permission is rechecked on every read. v2's owner + dataset + question would have mixed up "monthly trends" for West vs East.
4. **Distinct anomaly outcomes**; caveats come from execution metadata, never `[]` by fallback.
5. **Explicit sharing matrix.** v2's tests contradicted v2's sharing rule. Refusals never reveal the owner's identity; another user's private resource returns 404.
6. **One chart library (Plotly), one anomaly method (robust z / MAD), one LLM provider** after a smoke test. Precision as well as recall for anomalies; chart validity rather than an exact type match.
7. **pandas code is emitted by the deterministic compiler** and ships only if equivalence tests pass.
8. **Fixture dates are fixed (2026-09-30); demo dates are configurable**; the reference date is recorded in eval reports.
9. **Coverage % is not a gate.** Meaningful tests for permissions, joins, consistency, failures and concurrency are.
10. **Postponed past the gate:** forecasting, semantic search, PDF, sharing, eval UI, admin trace view, LLM SQL.
11. **This file and `docs/PLAN.md` in the repo are authoritative**; the vault holds a pointer.

Kept from v2: per-field owner/check/failure table, monitoring → response per failure class (refined: permission failures terminate, provider failures may fall back, narrative failures suppress), adaptive bounded loop, single-image Docker deploy, early contracts.

## Open
- LLM provider: decide after the tool-calling smoke test.
- Hosting: does not block Day 1.

## 2026-10-01: v3.1 implementation rules (third review round, targeted edits only)
- **Claims render whole clauses.** The model selects claims; backend templates write the entity, metric, period, unit and comparative word. "Highest" and "declining" need rank or trend evidence. Free model text in v1 = suggested questions only. *Why:* placeholders alone allowed "East earned {{c1}}" with c1 = West's value.
- **Isolation is named by tier.** Tier A (subprocess, empty env, rlimits, SIGKILL + cleanup, DuckDB lockdown) always; Tier B (separate container, `network_mode: none`, read-only rootfs, cgroup limits, cap_drop ALL) in compose. The README states the tier per deploy target. *Why:* DuckDB settings + a subprocess are not an OS sandbox, and the earlier summary overclaimed.
- **Fan-out joins are refused**, not warned about. Only 1:1 / many:1 to a unique dimension key, or an explicit `pre_aggregate` to the join key.
- **Anomaly reference population is explicit** (own history per group, or peers within a period); no pooling across scales.
- **Answer text streams only after validation**; progress events stream immediately.
- **Eval reports record** model id, prompt hash, fixture sha256s, reference date and git SHA; a smoke test is not evidence of quota.
- **Auth mechanics fixed:** Argon2id, server-side sessions (8h idle / 24h absolute), logout deletes the session, CLI/env-only admin provisioning.
- **Forecasting comes before sharing** post-gate. *Why:* it's a listed bonus; sharing adds authorization lifecycle work.
- **Planning is closed.** Further changes are made as edits alongside the code that needs them.

## 2026-10-02: integration findings (live verification of tracks A–C)
- **Join rule extended:** when a metric comes from the *joined* (right) dataset, the left key must be unique too. Live data had one duplicated order whose payments (£134.64) were silently double-counted even with `pre_aggregate`.
- **Upload limit comes from the admin setting**, not env config (cross-area rule).
- **Period-over-period is not in the `AnalysisSpec` grammar.** Trend and comparison claims use time-bucketed results instead. Add it only if a golden question needs it.
- **Open:** `AnomalyReport` lacks an `excluded_values` count (it currently goes in `reason`); add it when the agent turns it into a caveat.

## 2026-10-02: findings from live verification of the backend gaps (fake provider, HTTP)
- **Open (correctness): a partial final period.** Demo data ends today (2026-10-02), so the 2026-10 month bucket holds 2 days. The trend sentence ("revenue decreased by 22,980.8 … to 2026-10-01") and three anomaly flags (South/North 2026-10) are artifacts of that partial month. Fix: mark a final time bucket as `partial_period` when the data's max date falls before the bucket's end; add a caveat, and exclude it from trend claims and anomaly series.
- **Open (wording):** sentences read "region=West had the highest revenue: 135,306.2", with key=value entities and no currency. Render "West had the highest revenue: £135,306.20" using the metric unit.
- **Resolved: partial final period.** The executor runs one extra query (max date of the `time_bucket` column in the filtered data). If it falls before the last bucket's end, `ExecutionMeta.partial_period` holds the bucket start (ISO date; optional field, defaults to `None`) and a `partial_period` caveat is added. Trend and period-over-period claims, and the validator that re-derives them, span complete periods only. Anomaly detection skips that bucket and counts it in `excluded_values`. Rows stay in the table.
- **Resolved: wording.** Entities render as values ("West", "West, web"). The claim `unit` is now derived from the spec by `agent/validator.metric_unit`, never chosen by the model: `GBP` when the metric name or its source columns read as money (£1,234.50), `count` for count aggregates (integers), `%` for percentage changes, otherwise a plain number. The validator rejects any other unit.
- The fake provider references datasets by their sample names. After a rename it falls back to a dataset clarification, which is correct behaviour, but the e2e specs must not rename sample datasets.
