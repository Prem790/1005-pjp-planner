"""Adaptive practice evaluation and an honest comparison with the fixed baseline."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import statistics
import subprocess
import sys

import pandas as pd

from .evaluation import RATE_FIELDS, digest


def run_adaptive_evaluation(workbook,plan,context,out,seeds=(1,2,3),simulator=Path('data/private/PJP_Simulator/simulate.py'),static_dir=Path('artifacts/simulation/static')):
    workbook,plan,context,out,simulator,static_dir = map(Path,[workbook,plan,context,out,simulator,static_dir])
    if not seeds or len(set(seeds))!=len(seeds) or not set(seeds)<={1,2,3}:
        raise ValueError('Use unique published practice seeds 1–3')
    hashes = {str(p):digest(p) for p in [workbook,plan,context,simulator]}
    def run(seed):
        folder = out/f'seed_{seed}'
        folder.mkdir(parents=True,exist_ok=True)
        memory = folder/'memory.json'
        memory.write_text('{}')
        command = shlex.join([sys.executable,'-m','pjp','replan','--context',str(context.resolve()),'--memory',str(memory.resolve())])
        args = [sys.executable,str(simulator.resolve()),'--workbook',str(workbook.resolve()),
                '--plan',str(plan.resolve()),'--replan-cmd',command,'--seed',str(seed),'--out',str(folder.resolve())]
        print(f'Running adaptive practice seed {seed}...',flush=True)
        result = subprocess.run(args,capture_output=True,text=True,timeout=600)
        (folder/'run.log').write_text(result.stdout+'\n'+result.stderr)
        if result.returncode:
            raise RuntimeError(f'Adaptive simulation {seed} failed: {result.stderr[-1000:]}')
        summary = json.loads((folder/'summary.json').read_text())
        print(f"Seed {seed}: adherence {summary['simulated_adherence']:.2%}; focus {summary['focus_coverage']:.2%}; club {summary['club_cadence_met']:.2%}; drops {summary['drop_response_rate']:.2%}",flush=True)
        return summary
    with ThreadPoolExecutor(max_workers=min(3,len(seeds))) as pool:
        summaries = list(pool.map(run,seeds))
    for name,value in hashes.items():
        if digest(name)!=value:
            raise ValueError(f'Evaluation input changed: {name}')
    means = {k:statistics.mean(s[k] for s in summaries if s[k] is not None) for k in RATE_FIELDS}
    comparisons = []
    for s in summaries:
        baseline = json.loads((static_dir/f"seed_{s['seed']}"/'summary.json').read_text())
        for field in RATE_FIELDS:
            comparisons.append(dict(seed=s['seed'],metric=field,static=baseline[field],adaptive=s[field],
                change_percentage_points=100*(s[field]-baseline[field]) if s[field] is not None and baseline[field] is not None else None))
    comparison = pd.DataFrame(comparisons)
    comparison.to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([{k:v for k,v in s.items() if not isinstance(v,dict)} for s in summaries]).to_csv(out/'seed_metrics.csv',index=False)
    logs = [json.loads(p.read_text()) for seed in seeds for p in (out/f'seed_{seed}'/'replan_logs').glob('*.json')]
    report = dict(mode='adaptive_observation_only',seeds=list(seeds),input_sha256=hashes,mean_rates=means,
        per_seed=summaries,replan_calls=len(logs),max_replan_compute_seconds=max(l['runtime_seconds'] for l in logs),
        total_guardrail_breaches=sum(sum(s['guardrail_breaches'].values()) for s in summaries),
        replan_failures=sum(s['replan_failures'] for s in summaries),
        caveat='Practice seeds only. Forecast caps reduce scheduled visits and can lower focus coverage; adherence alone is not the business objective.')
    (out/'adaptive_summary.json').write_text(json.dumps(report,indent=2)+'\n')
    lines = ['November 2026: adaptive versus static practice evaluation','',
             'Metric | static mean | adaptive mean | change (percentage points)']
    for field,rows in comparison.groupby('metric',sort=False):
        lines.append(f"{field} | {rows['static'].mean():.2%} | {rows.adaptive.mean():.2%} | {rows.change_percentage_points.mean():+.2f}")
    lines += ['',f"Guardrail breaches: {report['total_guardrail_breaches']}; re-plan failures: {report['replan_failures']}",
              f"Evening calls: {report['replan_calls']}; maximum planner compute time: {report['max_replan_compute_seconds']:.2f} seconds.",
              'The 120-second interface timeout includes Python startup; successful runs confirm no timeouts.',
              'Higher adherence partly reflects fewer planned calls on forecast disruption days.',
              'Report focus coverage and completed-call totals beside adherence; do not hide the trade-off.',
              'Club and drop recovery improve; forecast false alarms and conservative reservations can reduce broad coverage.',
              'Initial-plan adherence counts original dates, so moving a successful visit still reduces that metric.',
              'No hidden events are read by the planner; revealed events are used only in post-run verification.']
    (out/'comparison_report.txt').write_text('\n'.join(lines)+'\n')
    return report
