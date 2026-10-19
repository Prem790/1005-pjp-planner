# 1005 PJP planner

A complete local proof of concept for November 2026 dealer ranking, focus
selection, scheduling, routing, WhatsApp talking points and evening re-planning.
It runs the supplied simulator on practice seeds 1–3 and packages the required
private deliverables. No commits, uploads or messages are sent by the workflow.

## Run the whole project

On this workstation, the project environment is already set up:

```bash
source .venv/bin/activate
python -m pjp run
```

Run from the repository root. This executes the audit, ranking, focus selection,
calendar/routes, nudges, static/adaptive evaluations, independent verifiers,
synthetic tests, two-page PDF and private submission packaging. It takes a few
minutes and prints progress. It resets adaptive simulation memory per practice
seed; it does not modify the supplied workbook or simulator.

Required local inputs:

```text
data/private/PJP_Assignment_Sample_Data.xlsx
data/private/PJP_Simulator/simulate.py
```

Alternate source locations:

```bash
python -m pjp run --workbook /path/to/PJP_Assignment_Sample_Data.xlsx --simulator /path/to/PJP_Simulator/simulate.py
```

The assignment workbook and simulator are provided separately by the reviewers;
they are confidential and intentionally excluded from Git.

## Clean installation

Use Python **3.11 or newer**. macOS Command Line Tools Python 3.9 is too old.
Check `python3 --version`, or use an installed `python3.11`/`python3.12` explicitly.
For a fresh checkout with a compatible `python3`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pjp run
```

Dependencies are pinned in `pyproject.toml`: NumPy, pandas, openpyxl, ReportLab
and pypdf. No API credentials, database, map service or LLM account is required.
The current workstation's environment reuses the existing Codex runtime; a
reviewer should create their own isolated environment using the commands above.

If `No module named pjp` appears, activate the project environment or run
`.venv/bin/python -m pjp run`. Reactivate it in each new Terminal.

## Submission deliverables

The full run creates `artifacts/submission/` and
`artifacts/submission_bundle.zip`. Both stay local and Git-ignored. The private
bundle contains:

- Per-SO November CSVs and the combined initial plan.
- Adaptive `summary.json` for each practice seed, with static comparisons.
- Twelve WhatsApp examples with their supporting evidence.
- The two-page `approach_and_results.pdf` and an editable text version.
- A comparison report, focus-repair log, verification record and file manifest.

Use the Git repository for source code and documentation. Provide the confidential
bundle privately to the assignment reviewers. The bundle does not contain the
source workbook, source documents or supplied simulator. Nothing is automatically
uploaded. The context for evening execution is generated locally by the full run.

## Practice results and trade-offs

The initial plan is identical in static and adaptive evaluations. Means over the
published practice seeds 1, 2 and 3:

| Metric | Static | Adaptive |
| --- | ---: | ---: |
| Simulated adherence | 94.52% | 97.14% |
| Focus-dealer coverage | 94.41% | 89.83% |
| Club cadence | 80.25% | 100.00% |
| Drops visited within seven days | 19.70% | 62.37% |
| Original-date adherence | 94.52% | 69.63% |
| Completed visits per run | 425.3 | 407.0 |
| Guardrail breaches | 0 | 0 |
| Re-plan failures | Not invoked | 0 |

Adaptive runs exceed the 90% adherence target, with all club cadences met and
better drop response. **Focus coverage is lower.** Forecasts cap next-day plans
at five visits even when actual weather later proves clear. The initial calendar
fills nominal capacity, leaving little recovery space. Club/drop prioritization
and cancellations change which non-club dealers get covered. Higher adherence
partly reflects fewer scheduled visits; completed visits and coverage must be
read alongside it. Original-date adherence falls when visits move dates.

These are practice results, not hidden-seed performance. The approximate 60%
manual adherence reference is contextual, not a simulated control run. No
unofficial combined hiring score or causal improvement over manual plans is
claimed. The two-page write-up discusses the coverage trade-off and limitations.

## How it works

1. **Audit:** inspect all 11 sheets, reconcile totals and source identities, flag
   calls under ten minutes and excessive historical repeat visits.
2. **Rank:** 55% value-weighted loss, 30% supported recent drop, 10% coarse GST peer
   context and 5% scheme opportunity. Use signed returns and matched periods;
   current ownership and explicit club flags determine obligations. Missing data
   does not become invented turnover, coordinates or recoverable revenue.
3. **Select:** protect club outlets, restrict optional owner duplication, fill
   the normal 150 visits per SO and screen optimistic travel bounds. This yields
   142/132/149 distinct focus dealers, above the brief's approximate 100.
4. **Schedule/route:** assign 25 Monday–Saturday days; clubs twice fourteen days
   apart. Optimize geography and commercial earliness; use exact within-day
   open-route travel. Two remote non-club replacements are explicitly logged.
5. **Nudge:** draft 450 messages under 300 characters, dated to the October 15
   snapshot. State scheme assumptions, shared-owner points and GPS gaps. Twelve
   examples include evidence; nothing is sent to WhatsApp.
6. **Re-plan:** persist observed sales, detect supported changes, restore missed
   obligations, pull drop visits forward and defer eligible hike visits. Protect
   club spacing/frequency and use next-day forecasts for capacity/travel. Retain
   future route anchors where possible and log any unplaced obligations.
7. **Evaluate/package:** run the unmodified supplied simulator, independently
   verify all dated plans and scores, compare policies, and build the submission.

## Evening re-plan interface

After the full run creates `artifacts/replanning/context.json`, the simulator can
call the following from the repository root:

```bash
python -m pjp replan --state STATE.json --plan CURRENT_PLAN.csv --out NEXT_PLAN.csv
```

The contract's three flags are supported; output contains the complete remaining
month and required five columns plus route timing/reasons. The supplied state
contains today's sales, completed/missed visits, completed visits month-to-date,
next-day forecasts and rules. It contains no hidden event labels.

By default, memory is `pjp_memory.json` beside `CURRENT_PLAN.csv`; simulator runs
have separate temporary directories, so observations persist across evenings but
remain isolated between runs. For explicit operational state:

```bash
python -m pjp replan --state STATE.json --plan CURRENT_PLAN.csv --out NEXT_PLAN.csv --context artifacts/replanning/context.json --memory artifacts/replanning/my_run/memory.json
```

Use a fresh memory path for a new month/run. Outputs are written atomically.
Decision logs include observed signals, forecast changes, displaced visits,
unplaced obligations and the full future plan. Every future plan is checked for
visit frequency and club spacing. The practice runs completed all 72 evening
calls without timeouts or failures; compute time excludes interpreter startup.

### Sales-change rules

The reference is signed historical sheets per workday (July 16–October 15).
Only actual November evening observations are accumulated. Missing sales entries
are zero-filled for observed working days. Compare 3–5 observations with that
reference using an approximate overdispersed count likelihood (dispersion 2).
A low ratio alone is insufficient to trigger a drop. Extra visits require stronger
evidence than pulling an already-owed visit forward. Hike holds are temporary,
never cancel club obligations, and do not waive initial focus coverage.

Thresholds live in `config/replanning.json`. They are baseline judgement choices,
not ground-truth labels. Quiet low-volume accounts remain hard to classify.
Revealed events are read only by post-run evaluation/verification, never by the
planner. No per-seed plan, event onset date or hidden-world generator is used.

## Run individual stages

With the virtual environment active:

```bash
python -m pjp audit --workbook data/private/PJP_Assignment_Sample_Data.xlsx
python -m pjp rank --workbook data/private/PJP_Assignment_Sample_Data.xlsx
python -m pjp select --workbook data/private/PJP_Assignment_Sample_Data.xlsx
python -m pjp plan --workbook data/private/PJP_Assignment_Sample_Data.xlsx
python -m pjp nudge
python -m pjp evaluate-static
python -m pjp evaluate-adaptive
```

Run `python -m pjp run` first when using adaptive evaluation independently, so its
cached context exists and matches current configuration. The full command is the
authoritative way to regenerate all outputs consistently after changes.

## Verification

```bash
python -W error -m unittest discover -s tests -v
python scripts/verify_rankings.py --workbook data/private/PJP_Assignment_Sample_Data.xlsx
python scripts/verify_focus.py --workbook data/private/PJP_Assignment_Sample_Data.xlsx
python scripts/verify_plan.py --workbook data/private/PJP_Assignment_Sample_Data.xlsx
python scripts/verify_nudges.py --workbook data/private/PJP_Assignment_Sample_Data.xlsx
python scripts/verify_adaptive.py
```

The full workflow runs these automatically. Verifiers use separate arithmetic
and source-reading paths rather than importing the implementation they check.
Adaptive verification examines every evening's entire future plan, as well as
execution logs, focus coverage, club cadence, drop response, hike handling and
score reconciliation. Source files are hashed before/after execution.

Synthetic tests cover sparse/future data, return signs, missing fields,
parent groups, scheme windows, routing, unknown GPS semantics, capacity,
frequency, missed clubs, sales noise and state isolation. They contain no actual
assignment dealer records. `artifacts/pipeline/` contains verifier logs and the
full-run completion manifest. Submission packaging checks completed verification
records before export.

## Output locations

| Directory | Contents |
| --- | --- |
| `artifacts/audit/` | Workbook audit and historical guardrail flags |
| `artifacts/ranking/` | All dealer scores, owner/scheme evidence, sensitivity |
| `artifacts/focus/` | Provisional selection and deferrals |
| `artifacts/plan/` | Initial CSVs, routes, final focus and explicit repairs |
| `artifacts/nudges/` | All drafts, 12 examples and evidence |
| `artifacts/replanning/` | Source-derived context for evening execution |
| `artifacts/simulation/static/` | Fixed-plan practice logs and summaries |
| `artifacts/simulation/adaptive/` | Adaptive logs, memory, comparison and checks |
| `artifacts/submission/` | Required private deliverables |

## Assumptions and limitations

- Six initial visits per day requires more than approximately 100 focus dealers.
- Dealer service is 25 minutes. Reserve two 40-minute DG blocks including travel;
  no DG accounts are supplied. Static blocks are 17:10–17:50 and 17:50–18:30.
- Travel matches the simulator: Haversine x 1.35, 18/30 km/h, no return leg.
  Missing GPS uses fixed 25/45-minute travel without moving the known position.
  Numerical route feasibility does not establish real-world geographic feasibility.
- No separate lunch duration is specified; it is not subtracted from this model.
- Quarterly targets reused as instructed are assumed; verify current eligibility
  and balances. Broad GST proxies cannot yield exact wallet share.
- Static nudges are October 15 snapshots, including later club appointments;
  live implementation should refresh them after re-planning and new sales.
- Weather forecasts can be wrong. Full recovery of all initial obligations is
  not guaranteed in a fixed-capacity month; remaining shortfalls are logged.
- Generic evidence thresholds can miss real drops or trigger on ordinary noise.
  More history, availability, opening hours and calibrated business costs would help.

## Repository layout and methods

`src/pjp/` contains the implementation and CLI; `config/` contains policy choices;
`tests/` has synthetic tests; `scripts/` has independent verifiers. Detailed
methods are in `docs/ranking_rules.txt`, `docs/focus_selection_rules.txt`,
`docs/calendar_and_routes.txt`, `docs/visit_nudges.txt`, `docs/static_evaluation.txt`
and `docs/replanning_and_results.txt`.

Keep raw inputs and all confidential derived outputs out of Git. Do not attach
records to public issues or PRs. See `docs/data_handling.md`; delete assignment
copies after the process as instructed, when authorized. No deletion is automatic.
