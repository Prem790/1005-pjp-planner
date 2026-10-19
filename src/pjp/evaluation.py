"""Run the unmodified supplied simulator; reconcile a static practice baseline."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

import pandas as pd


RATE_FIELDS = ['simulated_adherence','initial_plan_adherence','focus_coverage',
               'club_cadence_met','drop_response_rate']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ratio(a,b):
    return round(a/b,4) if b else None


def reconcile_static(plan,focus,visits,daily,events,summary):
    """Recompute core scores from execution logs, without simulator imports."""
    expected = Counter(zip(plan.date,plan.so_id,plan.account_id,plan.sequence))
    actual = Counter(zip(visits.date,visits.so_id,visits.account_id,visits.sequence))
    if expected!=actual:
        raise ValueError('Simulator execution rows differ from initial static plan')
    done = visits.loc[visits.status.eq('completed')]
    counts = done.account_id.value_counts().to_dict()
    requirements = {r.dealer_id: 2 if str(r.is_club).lower()=='true' else 1 for r in focus.itertuples()}
    if set(plan.account_id)!=set(requirements):
        raise ValueError('Focus IDs and initial-plan IDs differ')
    club = [k for k,n in requirements.items() if n==2]
    club_ok = 0
    for k in club:
        dates = sorted(pd.to_datetime(done.loc[done.account_id.eq(k),'date']))
        club_ok += len(dates)>=2 and (dates[1]-dates[0]).days>=10
    daily_plan = plan.groupby(['date','so_id']).size().to_dict()
    daily_done = done.groupby(['date','so_id']).size().to_dict()
    if len(daily)!=len(daily_plan) or daily.duplicated(['date','so_id']).any():
        raise ValueError('Daily simulator log has missing or duplicate officer-days')
    for r in daily.itertuples():
        if r.planned!=daily_plan.get((r.date,r.so_id),0) or r.completed!=daily_done.get((r.date,r.so_id),0):
            raise ValueError('Daily totals disagree with visit logs')
    drop_rows = events.loc[events.type.eq('drop')]
    responded = 0
    event_analysis = []
    for e in events.itertuples():
        dates = pd.to_datetime(done.loc[done.account_id.eq(e.dealer_id),'date'])
        after = dates.loc[dates.ge(pd.Timestamp(e.onset))]
        lag = int((after.min()-pd.Timestamp(e.onset)).days) if len(after) else None
        responded += e.type=='drop' and lag is not None and lag<=7
        event_analysis.append(dict(dealer_id=e.dealer_id,event_type=e.type,onset=e.onset,
            in_initial_focus=e.dealer_id in requirements,days_to_first_visit=lag,
            drop_visited_within_7_days=e.type=='drop' and lag is not None and lag<=7))
    recomputed = dict(simulated_adherence=ratio(len(done),len(visits)),
        initial_plan_adherence=ratio(len(done),len(plan)),
        focus_coverage=ratio(sum(counts.get(k,0)>=n for k,n in requirements.items()),len(requirements)),
        club_cadence_met=ratio(club_ok,len(club)),drop_response_rate=ratio(responded,len(drop_rows)),
        visits_completed=len(done),visits_planned_day_of=len(plan),focus_dealers=len(requirements),
        club_dealers_in_focus=len(club),drop_events=len(drop_rows),drops_visited_within_7_days=responded)
    for field,value in recomputed.items():
        if summary[field]!=value:
            raise ValueError(f'{field}: reported {summary[field]}, recomputed {value}')
    missed = visits.loc[~visits.status.eq('completed'),'status'].value_counts().to_dict()
    if missed!=summary['missed_by_reason']:
        raise ValueError('Missed-visit breakdown differs from logs')
    if summary['replan_failures']!=0:
        raise ValueError('A static run should not invoke the re-plan command')
    return recomputed,pd.DataFrame(event_analysis)


def run_static_evaluation(workbook,plan_path,focus_path,simulator,out,seeds=(1,2,3)):
    workbook,plan_path,focus_path,simulator,out = map(Path,[workbook,plan_path,focus_path,simulator,out])
    if not seeds or len(set(seeds))!=len(seeds) or not set(seeds)<={1,2,3}:
        raise ValueError('Use unique published practice seeds 1, 2, and 3')
    hashes = {str(p):digest(p) for p in [workbook,plan_path,focus_path,simulator]}
    plan,focus = pd.read_csv(plan_path),pd.read_csv(focus_path)
    if plan.empty or plan.duplicated(['date','so_id','account_id']).any():
        raise ValueError('Static plan is empty or has duplicate appointments')
    master = pd.read_excel(workbook,sheet_name='Dealers').set_index('dealer_id')
    if not set(plan.account_id)<=set(master.index):
        raise ValueError('Unknown dealer would be silently ignored by the simulator')
    if not plan.so_id.eq(plan.account_id.map(master.so_id)).all():
        raise ValueError('Plan includes another officers dealer')
    dates = pd.to_datetime(plan.date,errors='raise')
    if not dates.dt.strftime('%Y-%m').eq('2026-11').all() or dates.dt.dayofweek.eq(6).any():
        raise ValueError('Static plan has dates outside November working days')
    if not focus.is_club.astype(str).str.lower().isin(['true','false']).all():
        raise ValueError('Focus membership must be explicit')
    if not focus.is_club.astype(str).str.lower().eq(focus.dealer_id.map(master.loyalty_club_member.eq('Yes')).map({True:'true',False:'false'})).all():
        raise ValueError('Focus club flags differ from the workbook')
    out.mkdir(parents=True,exist_ok=True)
    summaries,territories,weather_rows,events_all,checks = [],[],[],[],[]
    for seed in seeds:
        seed_dir = out/f'seed_{seed}'
        command = [sys.executable,str(simulator.resolve()),'--workbook',str(workbook.resolve()),
                   '--plan',str(plan_path.resolve()),'--seed',str(seed),'--out',str(seed_dir.resolve())]
        print(f'Running static practice seed {seed} (no re-planner)...',flush=True)
        result = subprocess.run(command,capture_output=True,text=True,timeout=300)
        seed_dir.mkdir(parents=True,exist_ok=True)
        (seed_dir/'run.log').write_text(result.stdout+'\n'+result.stderr)
        if result.returncode:
            raise RuntimeError(f'Simulator seed {seed} failed: {result.stderr[-1000:]}')
        summary = json.loads((seed_dir/'summary.json').read_text())
        visits,daily,events = (pd.read_csv(seed_dir/name) for name in ['visits.csv','daily.csv','events_revealed.csv'])
        recomputed,event_table = reconcile_static(plan,focus,visits,daily,events,summary)
        event_table.insert(0,'seed',seed)
        events_all.append(event_table)
        checks.append(dict(seed=seed,status='passed',metrics_reconciled=list(recomputed),
                           visits_checked=len(visits),officer_days_checked=len(daily)))
        summaries.append(summary)
        for so,rows in visits.groupby('so_id'):
            done = rows.loc[rows.status.eq('completed')]
            coverage = done.account_id.value_counts()
            targets = focus.loc[focus.so_id.eq(so)]
            need = targets.is_club.astype(str).str.lower().eq('true').map({True:2,False:1})
            covered = sum(coverage.get(k,0)>=n for k,n in zip(targets.dealer_id,need))
            counts = rows.status.value_counts().to_dict()
            territories.append(dict(seed=seed,so_id=so,planned=len(rows),completed=len(done),
                adherence=ratio(len(done),len(rows)),focus_dealers=len(targets),focus_covered=covered,
                focus_coverage=ratio(covered,len(targets)),
                missed_weather=counts.get('missed_weather',0),missed_out_of_time=counts.get('missed_out_of_time',0),
                missed_unavailable=counts.get('missed_dealer_unavailable',0)))
        for (so,weather),rows in visits.groupby(['so_id','weather']):
            for status,count in rows.status.value_counts().items():
                weather_rows.append(dict(seed=seed,so_id=so,weather=weather,status=status,visits=int(count)))
        print(f"Seed {seed}: adherence {summary['simulated_adherence']:.2%}; coverage {summary['focus_coverage']:.2%}",flush=True)
    for name,value in hashes.items():
        if digest(name)!=value:
            raise ValueError(f'Input changed during evaluation: {name}')
    means = {field:statistics.mean(s[field] for s in summaries if s[field] is not None)
             if any(s[field] is not None for s in summaries) else None for field in RATE_FIELDS}
    misses = Counter()
    for s in summaries:
        misses.update(s['missed_by_reason'])
    metrics = [{k:v for k,v in s.items() if not isinstance(v,dict)} for s in summaries]
    pd.DataFrame(metrics).to_csv(out/'seed_metrics.csv',index=False)
    pd.DataFrame(territories).to_csv(out/'territory_metrics.csv',index=False)
    pd.DataFrame(weather_rows).to_csv(out/'weather_outcomes.csv',index=False)
    pd.concat(events_all,ignore_index=True).to_csv(out/'event_diagnostics.csv',index=False)
    report = dict(mode='static_no_replanner',seeds=list(seeds),input_sha256=hashes,
        mean_rates=means,misses_across_seeds=dict(misses),
        total_guardrail_breaches=sum(sum(s['guardrail_breaches'].values()) for s in summaries),
        replan_failures=0,replanner_tested=False,
        adherence_manual_reference=.60,adherence_target=.90,
        target_gap_percentage_points=100*(.90-means['simulated_adherence']),
        note='Practice-seed static baseline, not hidden-seed performance. Revealed events used only for diagnostics.',
        per_seed=summaries)
    (out/'baseline_summary.json').write_text(json.dumps(report,indent=2)+'\n')
    (out/'verification.json').write_text(json.dumps(dict(status='passed',runs=checks,inputs_unchanged=True),indent=2)+'\n')
    lines = ['November 2026 — static practice baseline',
        'Supplied simulator unchanged; same fixed plan for seeds 1–3; no evening re-planner.',
        '', 'Seed | adherence | focus coverage | club cadence | drop response']
    lines += [f"{s['seed']} | {s['simulated_adherence']:.2%} | {s['focus_coverage']:.2%} | "
              f"{s['club_cadence_met']:.2%} | {s['drop_response_rate']:.2%}" for s in summaries]
    lines += ['', 'Mean over practice seeds:']+[f'{k}: {v:.2%}' for k,v in means.items() if v is not None]
    lines += [f"Adherence target gap: {report['target_gap_percentage_points']:.2f} percentage points (90% target).",
              f'Total misses across runs: {dict(misses)}',
              f"Guardrail breaches across runs: {report['total_guardrail_breaches']}",
              'No re-plan failures because no re-planner was invoked; adaptive reliability is untested.',
              'The approximate 60% manual benchmark is contextual, not a simulated control run.',
              'Drop response includes events outside the initial focus set, per supplied simulator.',
              'Revealed events are post-run diagnostics and must not inform a seed-specific initial plan.',
              'Next: reschedule missed obligations, react to observed sales, use forecasts, protect club cadence.']
    (out/'baseline_report.txt').write_text('\n'.join(lines)+'\n')
    print(f'Wrote verified static evaluation to {out}')
    return report
