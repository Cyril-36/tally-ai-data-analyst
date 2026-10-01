# Assignment coverage check

Checked against the supplied one-page AI Engineer assignment PDF. It has feature requirements and deliverables, but no numerical selection or marking rubric. This table records what is actually implemented and verified in the backend; it is not a claim that the full app has been submitted.

| Assignment item | Backend evidence | Status |
|---|---|---|
| Upload and validate one or more CSVs | Multipart upload, atomic multi-file handling, profile/quality checks; upload and invalid-file tests | Verified |
| Natural-language questions | Bounded tool-calling provider adapter, structured spec, clarification, conversation state; fake-provider HTTP tests | Implemented; live provider unverified |
| Insights and summaries | Deterministic value, rank, trend and period comparison claims rechecked against result rows; tampering tests | Verified offline |
| Bar, line, pie, scatter charts | Plotly builder validates encodings and plots stored result values; chart tests and HTTP chart route | Verified offline |
| SQL and/or Pandas code | The exact compiled SQL and bound parameters are in `AnalysisResult.meta`; SQL goldens execute in DuckDB | Verified; Pandas omitted by design |
| Anomalies and explanations | Robust z-score with explicit population, threshold, median, MAD, excluded values and insufficient-data status; anomaly tests | Verified offline |
| Explain reasoning | Real tool trace, exact SQL/params, source versions, claim sources, caveats and anomaly evidence in result/export | Verified offline |
| Session context | Durable conversation state, last resolved spec, follow-up prompt context, selected-dataset reset; HTTP tests | Verified offline; live model follow-ups unverified |
| Multi-file, joins, quality | Authorized catalog, cardinality checks, refused fan-out, quality endpoint | Verified offline |
| Dashboard, auth, cache, export, streaming, logs | Owner-only pins/results, admin-only settings, versioned owner-scoped cache, HTML/CSV reports, SSE, structured logs | Verified offline and in local HTTP smoke |
| Production engineering | Input validation, bounded turns, login/signup throttle, idempotent retries, secure cookies, no-store headers, isolated query worker, two-worker Docker run | Verified in tests and Docker smoke |
| Evaluation framework | Frozen 30-question set, deterministic expected specs, one-command baseline check and live scorer with provenance | Offline baselines verified; live model unverified |
| README, architecture diagram, Docker, sample datasets | Present in repository | Verified |
| Frontend screens, screenshots, live model run, demo video, GitHub submission | Frontend is assigned separately; provider key and final submission artifacts are not in this checkout | Pending |

The Docker smoke used a disposable Compose project and volume. It checked two-worker startup, readiness, sign-up, upload, calculation, and cross-user denial. The host HTTP smoke additionally checked admin separation, quality and export. Neither exercises a paid model. The backend uses Tier A executor isolation; the separate network-isolated Tier B service described as future hardening in the plan is not shipped.

Before final submission, integrate the frontend without weakening API ownership rules, run one real provider tool-calling smoke test and a frozen live question set with every case reported, check the deployed URL as an unauthenticated and authenticated user, then add screenshots and a 10–30 second demo video to the README. The assignment requires both a GitHub repository link and that video.
