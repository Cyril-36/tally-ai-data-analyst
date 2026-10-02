# Assignment coverage check

Checked against the supplied one-page AI Engineer assignment PDF. It has feature requirements and deliverables, but no numerical selection or marking rubric. This table records what is implemented and verified locally; it is not a claim that the app has been submitted.

| Assignment item | Backend evidence | Status |
|---|---|---|
| Upload and validate one or more CSVs | Multipart upload, atomic multi-file handling, profile/quality checks; upload and invalid-file tests | Verified |
| Natural-language questions | Bounded tool-calling provider adapter, structured spec, clarification, conversation state; fake-provider HTTP tests and two live suites | Implemented; retail 30/31 answer-correct, 24/31 exact (26–30 across runs), transfer 10/10; model errors remain |
| Insights and summaries | Deterministic value, rank, trend and period comparison claims rechecked against result rows; tampering tests | Verified offline |
| Bar, line, pie, scatter charts | Plotly builder validates encodings and plots stored result values; chart tests and HTTP chart route | Verified offline |
| SQL and/or Pandas code | The exact compiled SQL and bound parameters are in `AnalysisResult.meta`; SQL goldens execute in DuckDB | Verified; Pandas omitted by design |
| Anomalies and explanations | Robust z-score with explicit population, threshold, median, MAD, excluded values and insufficient-data status; anomaly tests | Verified offline |
| Explain reasoning | Real tool trace, exact SQL/params, source versions, claim sources, caveats and anomaly evidence in result/export | Verified offline |
| Session context | Durable conversation state, last resolved spec, follow-up prompt context, selected-dataset reset; HTTP tests and live follow-up cases | Verified offline and in the latest retail run; an earlier live run failed one follow-up |
| Multi-file, joins, quality | Authorized catalog, cardinality checks, refused fan-out, quality endpoint | Verified offline |
| Dashboard, auth, cache, export, streaming, logs | Automatic schema-driven overview dashboard (no AI) plus owner-only pins/results, admin-only settings, versioned owner-scoped cache, HTML/CSV reports, SSE, structured logs | Verified offline and in local HTTP smoke |
| Production engineering | Input validation, bounded turns, login/signup throttle, idempotent retries, secure cookies, no-store headers, isolated query worker, two-worker Docker run | Verified locally; live model reliability remains a blocker |
| Evaluation framework | Revised 31-question retail suite plus a 10-question non-retail transfer suite, deterministic expected specs, one-command checks and live reports with query/table diagnostics | 31/31 and 10/10 offline baselines; live 30/31 and 10/10, 0 skipped |
| README, architecture diagram, Docker, sample datasets | Present in repository | Verified |
| Frontend screens and integration | React frontend in the combined image; 37 component tests and 29 browser flows against both dev servers and the hardened Docker image of the final commit | Verified locally with fake provider |
| Screenshots, demo video, GitHub submission | Six README screenshots (`docs/screenshots/`), a 23-second demo video (`docs/demo.mp4`) and an inline GIF, all recorded from the running app with the scripted provider | Screenshots and video done; GitHub link pending |

The Docker smoke used a disposable Compose project and volume. It checked two-worker startup, readiness, sign-up, upload, calculation, and cross-user denial. The host HTTP smoke was repeated after the agent changes and checked admin separation, quality and export. The combined image built from the final commit passed all 29 browser flows locally with the fake provider. Live model results are in the final [retail](../evals/reports/20261002T143918134331Z.md) report (30/31 answer-correct, 24/31 exact-spec, clean tree at `8b28f8d`) and the [transfer](../evals/reports/20261002T143146031020Z.md) report (10/10, clean tree; the later attempt-cap fix only acts on a fourth analysis request, which no transfer case reached); the live model was not run through the browser. The backend uses Tier A executor isolation; the separate network-isolated Tier B service described as future hardening in the plan is not shipped.

Before final submission, address the remaining model errors and ambiguous answer, broaden transfer evaluation beyond two small business domains, then prepare screenshots and a 10–30 second demo video. A deployment and its public URL have not been tested, as requested. The assignment requires a GitHub repository link and that video.
