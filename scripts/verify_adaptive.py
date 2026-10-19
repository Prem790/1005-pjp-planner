"""Independent replay checks for all future plans and adaptive simulator scores."""
from collections import Counter
import argparse
import hashlib
import json
import math
from pathlib import Path

import pandas as pd


def km(a,b):
    lat1,lon1,lat2,lon2 = map(math.radians,(*a,*b))
    h = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742*math.asin(min(1,math.sqrt(h)))


def verify(context_path,folder,plan_path):
    context = json.loads(context_path.read_text())
    records,origins = context['records'],context['officers']
    initial = pd.read_csv(plan_path)
    focus = set(initial.account_id)
    expected_focus = {k for k,r in records.items() if r['in_focus']}
    assert focus==expected_focus
    report = json.loads((folder/'adaptive_summary.json').read_text())
    for name,digest in report['input_sha256'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
    checks = []
    for seed in report['seeds']:
        seed_dir = folder/f'seed_{seed}'
        summary = json.loads((seed_dir/'summary.json').read_text())
        visits = pd.read_csv(seed_dir/'visits.csv')
        done = visits.loc[visits.status.eq('completed')]
        counts = done.account_id.value_counts().to_dict()
        assert not visits.duplicated(['date','so_id','account_id']).any()
        assert max(counts.values())<=2
        assert visits.so_id.eq(visits.account_id.map({k:r['so_id'] for k,r in records.items()})).all()
        daily = pd.read_csv(seed_dir/'daily.csv')
        assert daily.planned.le(6).all()
        assert not daily.duplicated(['date','so_id']).any()
        assert daily.planned.sum()==len(visits)
        assert daily.completed.sum()==len(done)
        assert summary['visits_completed']==len(done)
        assert summary['simulated_adherence']==round(len(done)/len(visits),4)
        covered = sum(counts.get(k,0)>=(2 if records[k]['is_club'] else 1) for k in focus)
        assert summary['focus_coverage']==round(covered/len(focus),4)
        clubs = [k for k in focus if records[k]['is_club']]
        cadence = 0
        for k in clubs:
            dates = sorted(pd.to_datetime(done.loc[done.account_id.eq(k),'date']))
            cadence += len(dates)>=2 and (dates[1]-dates[0]).days>=10
        assert summary['club_cadence_met']==round(cadence/len(clubs),4)
        init_done = done.in_initial_plan.astype(str).str.lower().eq('true').sum()
        assert summary['initial_plan_adherence']==round(int(init_done)/len(initial),4)
        missed = visits.loc[~visits.status.eq('completed'),'status'].value_counts().to_dict()
        assert summary['missed_by_reason']==missed
        events = pd.read_csv(seed_dir/'events_revealed.csv')
        responded,hike_visits = 0,0
        for e in events.itertuples():
            dates = pd.to_datetime(done.loc[done.account_id.eq(e.dealer_id),'date'])
            if e.type=='drop':
                responded += any(0<=(d-pd.Timestamp(e.onset)).days<=7 for d in dates)
            else:
                hike_visits += sum(pd.Timestamp(e.onset)<d<pd.Timestamp(e.end) for d in dates)
        ndrops = sum(events.type.eq('drop'))
        assert summary['drops_visited_within_7_days']==responded
        assert summary['drop_response_rate']==(round(responded/ndrops,4) if ndrops else None)
        assert summary['visits_made_during_hikes']==hike_visits
        cancellations = pd.read_csv(seed_dir/'cancellations.csv')
        hike_cancelled = 0
        hikes = events.loc[events.type.eq('hike')].set_index('dealer_id')
        for r in cancellations.itertuples():
            if r.account_id in hikes.index:
                e = hikes.loc[r.account_id]
                hike_cancelled += pd.Timestamp(e.onset)<=pd.Timestamp(r.decided_on)<pd.Timestamp(e.end)
        assert summary['visits_cancelled_during_hikes']==hike_cancelled
        assert not summary['guardrail_breaches'] and summary['replan_failures']==0
        future_rows,club_shortfalls = 0,0
        logs = sorted((seed_dir/'replan_logs').glob('*.json'))
        assert len(logs)==24
        for path in logs:
            audit = json.loads(path.read_text())
            assert audit['uses_hidden_events'] is False
            assert audit['runtime_seconds']<120
            future = pd.DataFrame(audit['output_plan'])
            completed = Counter(v['account_id'] for v in audit['completed_visits_month_to_date'])
            if future.empty:
                assert audit['output_future_rows']==0
                continue
            assert not future.duplicated(['date','so_id','account_id']).any()
            assert future.date.gt(audit['today']).all()
            assert set(future.date)<=set(context['workdays'])
            scheduled = future.account_id.value_counts().to_dict()
            for k,n in scheduled.items():
                assert completed[k]+n<=2
            for k in clubs:
                total = completed[k]+scheduled.get(k,0)
                if total!=2:
                    club_shortfalls += 1
                    assert any(x['account_id']==k for x in audit['protected_club_shortfalls'])
                dates = sorted([v['date'] for v in audit['completed_visits_month_to_date'] if v['account_id']==k]+
                               list(future.loc[future.account_id.eq(k),'date']))
                assert all((pd.Timestamp(b)-pd.Timestamp(a)).days>=10 for a,b in zip(dates,dates[1:]))
            tomorrow = next(d for d in context['workdays'] if d>audit['today'])
            for (date,so),stops in future.groupby(['date','so_id']):
                weather = audit['weather_forecast_next_working_day'][so] if date==tomorrow else 'clear'
                assert len(stops)<=(6 if weather=='clear' else 5)
                origin = origins[so]
                position = origin['start']
                used = 0
                assert sorted(stops.sequence)==list(range(1,len(stops)+1))
                for v in stops.sort_values('sequence').itertuples():
                    dealer = records[v.account_id]
                    assert dealer['so_id']==so
                    kind = origin['kind']
                    if dealer['has_location']:
                        nxt = (dealer['lat'],dealer['lon'])
                        travel = km(position,nxt)*1.35/(30 if kind=='upcountry' else 18)*60
                        position = nxt
                    else:
                        travel = 45 if kind=='upcountry' else 25
                    travel *= {'clear':1,'rain':1.5,'traffic':1.3}[weather]
                    assert abs(v.travel_min_from_previous-travel)<1e-6
                    used += travel+25
                assert used<=460+1e-6
            future_rows += len(future)
        checks.append(dict(seed=seed,status='passed',evening_calls=len(logs),future_rows_checked=future_rows,
                           logged_club_shortfalls=club_shortfalls,executed_visits_checked=len(visits)))
    result = dict(status='passed',runs=checks,source_context_sha256=hashlib.sha256(context_path.read_bytes()).hexdigest())
    (folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--context',type=Path,default=Path('artifacts/replanning/context.json'))
    parser.add_argument('--out',type=Path,default=Path('artifacts/simulation/adaptive'))
    parser.add_argument('--plan',type=Path,default=Path('artifacts/plan/plan_november.csv'))
    a = parser.parse_args()
    verify(a.context,a.out,a.plan)
