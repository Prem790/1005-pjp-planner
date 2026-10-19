# Project completion status

All five assignment stages are implemented: rank, schedule, route, nudge and
re-plan. The complete workflow is `python -m pjp run` from the repository root.

## Implemented and verified

- Audit all 11 workbook sheets; preserve source inputs and signed returns.
- Rank all 677 dealers with explainable loss/drop/GST/scheme evidence.
- Select provisional focus sets, protect clubs, and log all deferrals.
- Generate 450 initial appointments and 75 daily routes; record two explicit
  remote non-club replacements without altering original selection outputs.
- Generate 450 dated WhatsApp drafts and 12 examples with supporting data.
- Support the simulator's evening --state/--plan/--out contract, persistent
  observations, noisy-sales detection, forecast routing and obligation recovery.
- Run static and adaptive practice seeds 1–3 with isolated per-run memory.
- Independently verify source-derived calculations, all message evidence,
  initial routes, adaptive scores and every evening's full future plan.
- Package per-SO plans, practice summaries, nudge examples, comparisons,
  verification and a two-page PDF write-up in a private submission bundle.
- Keep source documents, simulator and all confidential outputs Git-ignored.

## Results and limitations

Static / adaptive means: adherence 94.52% / 97.14%; focus coverage
94.41% / 89.83%; club cadence 80.25% / 100%; drop response 19.70% / 62.37%.
All 72 adaptive evening calls succeeded with zero guardrail breaches. The lower
adaptive focus coverage and completed-call volume are disclosed in the write-up;
the adaptive policy does not dominate the static plan on every business metric.
No claim is made about unpublished seeds or guaranteed real-world delivery.

## Review and submission

Inspect `artifacts/submission/approach_and_results.pdf` and the private bundle
`artifacts/submission_bundle.zip`. Full-run completion is recorded in
`artifacts/pipeline/run_summary.json` after all required stages pass.
The code is ready for user review/commit after that run completes. No automatic
commit, push, email, message delivery or data deletion is performed.
