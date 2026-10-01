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
