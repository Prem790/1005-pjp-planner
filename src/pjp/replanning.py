"""Observation-only evening re-planning. No hidden simulator imports or events."""
from collections import Counter, deque
from datetime import timedelta
import hashlib
import itertools
import math
import time
import json
from pathlib import Path

import pandas as pd

from .routing import RouteSolver


def prepare_context(workbook,ranking_path,focus_path,selection_path,policy_path,out):
    """Cache immutable public inputs so each evening avoids workbook parsing."""
    with pd.ExcelFile(workbook) as book:
        officers = pd.read_excel(book,sheet_name='Officers')
        dealers = pd.read_excel(book,sheet_name='Dealers')
        sales = pd.read_excel(book,sheet_name='Sales_Daily')
        calendar = pd.read_excel(book,sheet_name='Planning_Calendar')
    ranking = pd.read_csv(ranking_path).set_index('dealer_id')
    focus = pd.read_csv(focus_path)
    sales['date'] = pd.to_datetime(sales.date)
    # Same published history window used by the simulator, with signed returns.
    start,end = pd.Timestamp('2026-07-16'),pd.Timestamp('2026-10-15')
    workdays = sum(d.dayofweek!=6 for d in pd.date_range(start,end))
    sums = sales.loc[sales.date.between(start,end)].groupby('dealer_id').sheets.sum()
    records = {}
    for d in dealers.to_dict('records'):
        k = d['dealer_id']
        known = pd.notna(d['lat']) and pd.notna(d['lon'])
        records[k] = dict(so_id=d['so_id'],lat=float(d['lat']) if known else None,
            lon=float(d['lon']) if known else None,has_location=known,
            is_club=d['loyalty_club_member']=='Yes',priority_score=float(ranking.loc[k,'priority_score']),
            baseline_sheets=max(0,float(sums.get(k,0))/workdays),in_focus=k in set(focus.dealer_id))
    origins = {o['so_id']:dict(start=[float(o['approx_day_start_lat']),float(o['approx_day_start_lon'])],
                kind='upcountry' if 'upcountry' in str(o['geography']).lower() else 'metro')
               for o in officers.to_dict('records')}
    selection = json.loads(Path(selection_path).read_text())
    context = dict(version=1,source_sha256=hashlib.sha256(Path(workbook).read_bytes()).hexdigest(),
        history_window=[str(start.date()),str(end.date())],history_workdays=workdays,
        records=records,officers=origins,travel_model=selection['travel_model'],
        policy=json.loads(Path(policy_path).read_text()),
        workdays=[str(pd.Timestamp(d).date()) for d in calendar.loc[calendar.working_day.eq('Yes'),'date']])
    out = Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(context,allow_nan=False))
    return context


def observe(context,state,memory):
    """Zero-fill absent sales for each observed workday; detect persistent shifts."""
    today = state['today']
    if memory.get('last_today','')>today:
        raise ValueError('Memory belongs to a later day; use a separate memory per run')
    sales = {}
    for row in state['sales_today']:
        if row['dealer_id'] not in context['records']:
            raise ValueError('Unknown dealer in observed sales')
        value = float(row['sheets'])
        if not pd.notna(value) or not float('-inf')<value<float('inf'):
            raise ValueError('Observed sales must be finite')
        sales[row['dealer_id']] = sales.get(row['dealer_id'],0)+value
    memory.setdefault('sales',{})[today] = sales
    dates = sorted(d for d in memory['sales'] if d<=today)
    cfg = context['policy']
    window = dates[-cfg['observation_window_workdays']:]
    signals = {}
    episodes = memory.setdefault('drop_episodes',{})
    holds = memory.setdefault('hike_holds',{})
    for k,r in context['records'].items():
        base = r['baseline_sheets']
        if len(window)<cfg['observation_window_workdays'] or base<cfg['minimum_baseline_sheets_per_workday']:
            continue
        values = [memory['sales'][d].get(k,0) for d in window]
        relative = sum(values)/len(values)/base
        shape = cfg['count_dispersion']
        evidence = []
        for length in range(cfg['observation_window_workdays'],
                            min(len(dates),cfg['maximum_observation_window_workdays'])+1):
            observed = [max(0,memory['sales'][d].get(k,0)) for d in dates[-length:]]
            def log_ratio(factor):
                alternative = base*factor
                return (length*shape*math.log((shape+base)/(shape+alternative))+
                        sum(observed)*math.log((alternative/(shape+alternative))/(base/(shape+base))))
            evidence.append((log_ratio(.25),log_ratio(2),length))
        low = max(evidence,key=lambda x:x[0])
        high = max(evidence,key=lambda x:x[1])
        if relative>=cfg['recovery_ratio']:
            episodes.pop(k,None)
        if relative<cfg['drop_ratio'] and low[0]>=cfg['drop_log_evidence_threshold']:
            episode = episodes.setdefault(k,dict(detected_on=today,log_evidence=low[0]))
            episode['log_evidence'] = max(episode['log_evidence'],low[0])
            signals[k] = dict(type='drop',observed_ratio=relative,window=dates[-low[2]:],log_evidence=low[0])
        elif relative>=cfg['hike_ratio'] and high[1]>=cfg['hike_log_evidence_threshold'] and all(v>base for v in values[-2:]):
            if k not in holds:
                hold = min(pd.Timestamp(today)+pd.Timedelta(days=cfg['hike_hold_calendar_days']),
                           pd.Timestamp(context['workdays'][-4]))
                holds[k] = str(hold.date())
            signals[k] = dict(type='hike',observed_ratio=relative,window=dates[-high[2]:],log_evidence=high[1])
        elif relative<1.4:
            holds.pop(k,None)
    memory['last_today'] = today
    return signals


def prioritize_order(solver,keys,weights,multiplier,budget):
    """At most 720 permutations: priority earlier, subject to the actual budget."""
    best = None
    for order in itertools.permutations(sorted(keys)):
        pos = None
        travel = 0
        for k in order:
            if solver.position[k] is None:
                travel += solver.model['unknown_location_travel_minutes'][solver.kind]
            else:
                travel += solver.initial[k] if pos is None else solver.distance[pos,k]
                pos = k
        if travel*multiplier+len(order)*solver.model['dealer_call_minutes']>budget+1e-7:
            continue
        cost = travel*multiplier+8*sum(i*weights[k] for i,k in enumerate(order))
        value = (cost,order)
        if best is None or value<best:
            best = value
    if best is None:
        raise ValueError('No feasible order for selected next-day route')
    return best[1]


def replan(context,state,current,memory):
    cfg = context['policy']
    records = context['records']
    today = state['today']
    if today not in context['workdays']:
        raise ValueError('State today must be a November working day')
    dates = [d for d in context['workdays'] if d>today]
    if not dates or state['next_working_day']!=dates[0]:
        raise ValueError('State next working day differs from planning calendar')
    for field,value in [('dealer_visits_per_day',6),('disrupted_visits_per_day',5),
                        ('max_visits_per_dealer_month',2),('club_min_gap_days',10)]:
        if state['rules'][field]!=value:
            raise ValueError('State rules differ from supported assignment constraints')
    completed = {}
    for v in state['completed_visits_month_to_date']:
        k,d = v['account_id'],v['date']
        if k not in records or d>today:
            raise ValueError('Invalid completed visit in state')
        completed.setdefault(k,[]).append(d)
    if any(len(v)>2 or len(v)!=len(set(v)) for v in completed.values()):
        raise ValueError('Completed state already violates visit limits')
    signals = observe(context,state,memory)
    episodes = memory['drop_episodes']
    pending_drop = {k:e for k,e in episodes.items()
                    if not any(d>=e['detected_on'] for d in completed.get(k,[])) and len(completed.get(k,[]))<2}
    desired = {}
    for k,r in records.items():
        need = 2 if r['is_club'] and r['in_focus'] else 1 if r['in_focus'] else 0
        left = max(0,need-len(completed.get(k,[])))
        if k in pending_drop:
            if left or pending_drop[k]['log_evidence']>=cfg['extra_visit_log_evidence_threshold']:
                left = max(left,1)
            else:
                # Low confidence can pull an owed visit forward, but cannot
                # consume another dealer's coverage slot for a speculative repeat.
                pending_drop[k]['extra_visit_suppressed'] = True
        desired[k] = min(left,2-len(completed.get(k,[])))
    pending_drop = {k:e for k,e in pending_drop.items() if desired.get(k,0)>0}
    missed = {r['account_id'] for r in state['visits_today'] if r['status']!='completed'}
    def priority(k):
        r = records[k]
        return (10000 if r['is_club'] else 0)+(7000 if k in pending_drop else 0)+(300 if k in missed else 0)+r['priority_score']*10
    def allowed(k,d,groups):
        r = records[k]
        if r['is_club']:
            others = completed.get(k,[])+[day for day,ids in groups.items() if k in ids]
            if any(abs((pd.Timestamp(d)-pd.Timestamp(x)).days)<cfg['club_min_gap_days'] for x in others):
                return False
        # Hike holds never override club cadence; for other dealers defer, don't
        # permanently waive the initial focus obligation.
        hold = memory['hike_holds'].get(k)
        if hold and d<hold and not r['is_club'] and k not in pending_drop:
            return False
        return k not in groups[d]
    output,logs,unplaced = [],[],[]
    for so,officer in sorted(context['officers'].items()):
        local = {k:r for k,r in records.items() if r['so_id']==so}
        solver = RouteSolver(local,officer['start'],officer['kind'],context['travel_model'])
        groups = {d:[] for d in dates}
        forecasts = state['weather_forecast_next_working_day']
        forecast = forecasts[so]
        if forecast not in ['clear','rain','traffic']:
            raise ValueError('Unsupported weather forecast')
        budget = context['travel_model']['day_minutes']-context['travel_model']['demand_generation_minutes']
        def fits(d,ids):
            cap = 5 if d==dates[0] and forecast!='clear' else 6
            if len(ids)>cap or len(set(ids))!=len(ids):
                return False
            mult = {'clear':1,'rain':1.5,'traffic':1.3}[forecast] if d==dates[0] else 1
            return solver.route(ids)[0]*mult+len(ids)*25<=budget+1e-7
        remaining = {k:n for k,n in desired.items() if k in local and n}
        rows = current.loc[current.so_id.eq(so)].sort_values(['date','sequence']) if not current.empty else current
        for v in rows.to_dict('records'):
            k,d = v['account_id'],v['date']
            if k not in records or records[k]['so_id']!=so:
                raise ValueError('Invalid future-plan ownership')
            if d not in groups or not remaining.get(k,0):
                continue
            due = str((pd.Timestamp(pending_drop[k]['detected_on'])+pd.Timedelta(days=cfg['drop_response_calendar_days'])).date()) if k in pending_drop else None
            if due and d>due:
                logs.append(dict(so_id=so,account_id=k,reason='pull_forward_observed_drop',old_date=d))
                continue
            if allowed(k,d,groups):
                groups[d].append(k)
                remaining[k] -= 1
        for d,ids in groups.items():
            while not fits(d,ids):
                k = min(ids,key=lambda k:(priority(k),k))
                ids.remove(k)
                remaining[k] = remaining.get(k,0)+1
                logs.append(dict(so_id=so,account_id=k,old_date=d,reason='forecast_capacity_or_travel'))
        queue = deque(k for k in sorted(remaining,key=lambda k:(-priority(k),k)) for _ in range(remaining[k]))
        operations = 0
        while queue:
            operations += 1
            if operations>2000:
                raise ValueError('Insertion search exceeded its bounded work limit')
            k = queue.popleft()
            placed = False
            for d in dates:
                if not allowed(k,d,groups):
                    continue
                ids = groups[d]
                if fits(d,ids+[k]):
                    ids.append(k)
                    logs.append(dict(so_id=so,account_id=k,new_date=d,reason='recover_club_or_miss' if records[k]['is_club'] or k in missed else 'observed_drop' if k in pending_drop else 'remaining_focus_obligation'))
                    placed = True
                    break
                lower = sorted([a for a in ids if priority(a)<priority(k) and
                                (not records[a]['is_club'] or records[k]['is_club'])],key=lambda a:(priority(a),a))
                for old in lower:
                    trial = [a for a in ids if a!=old]+[k]
                    if fits(d,trial):
                        groups[d] = trial
                        queue.append(old)
                        logs.append(dict(so_id=so,account_id=k,new_date=d,displaced_account_id=old,reason='higher_priority_obligation'))
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                unplaced.append(dict(so_id=so,account_id=k,is_club=records[k]['is_club'],
                    reason='no_feasible_remaining_slot_or_cadence_or_active_hike_hold'))
        for d,ids in groups.items():
            if not ids:
                continue
            mult = {'clear':1,'rain':1.5,'traffic':1.3}[forecast] if d==dates[0] else 1
            if d==dates[0]:
                weights = {k:(4 if k in pending_drop else 0)+(2 if records[k]['is_club'] else 0)+records[k]['priority_score']/100 for k in ids}
                order = prioritize_order(solver,ids,weights,mult,budget)
            else:
                _,order = solver.route(ids)
            elapsed = 570.
            for seq,(k,travel) in enumerate(zip(order,solver.legs(order)),1):
                elapsed += travel*mult
                arrival = int(math.ceil(elapsed-1e-9))
                elapsed += 25
                output.append(dict(date=d,so_id=so,sequence=seq,account_id=k,account_type='Dealer',
                    planned_arrival=f'{arrival//60:02d}:{arrival%60:02d}',travel_min_from_previous=round(travel*mult,6),
                    reason='Observed drop response' if k in pending_drop else 'Protected club cadence' if records[k]['is_club'] else 'Remaining focus coverage'))
    frame = pd.DataFrame(output,columns=['date','so_id','sequence','account_id','account_type','planned_arrival','travel_min_from_previous','reason'])
    # Final whole-plan guardrails, independent of insertion-loop bookkeeping.
    scheduled = frame.account_id.value_counts().to_dict()
    for k,n in scheduled.items():
        if n+len(completed.get(k,[]))>2:
            raise ValueError('Re-plan would exceed dealer frequency')
        if records[k]['is_club']:
            visits = sorted(completed.get(k,[])+list(frame.loc[frame.account_id.eq(k),'date']))
            if any((pd.Timestamp(b)-pd.Timestamp(a)).days<10 for a,b in zip(visits,visits[1:])):
                raise ValueError('Re-plan would breach club spacing')
    audit = dict(today=today,signals=signals,changes=logs,unplaced_obligations=unplaced,
        protected_club_shortfalls=[x for x in unplaced if x['is_club']],
        input_future_rows=len(current),output_future_rows=len(frame),uses_hidden_events=False,
        output_plan=frame.to_dict('records'),
        completed_visits_month_to_date=state['completed_visits_month_to_date'],
        weather_forecast_next_working_day=state['weather_forecast_next_working_day'])
    return frame,memory,audit


def run_replan(state_path,plan_path,out,context_path,memory_path=None):
    started = time.monotonic()
    context_path = Path(context_path)
    state = json.loads(Path(state_path).read_text())
    context = json.loads(context_path.read_text())
    context_hash = hashlib.sha256(context_path.read_bytes()).hexdigest()
    memory_path = Path(memory_path) if memory_path else Path(plan_path).parent/'pjp_memory.json'
    memory = json.loads(memory_path.read_text()) if memory_path.exists() else {}
    if memory.get('context_sha256',context_hash)!=context_hash:
        raise ValueError('Memory context changed; use fresh run memory')
    memory['context_sha256'] = context_hash
    current = pd.read_csv(plan_path,dtype={'account_id':str,'so_id':str,'date':str})
    frame,memory,audit = replan(context,state,current,memory)
    out = Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    temporary = Path(str(out)+'.tmp')
    frame.to_csv(temporary,index=False)
    temporary.replace(out)
    memory_path.parent.mkdir(parents=True,exist_ok=True)
    temp_memory = Path(str(memory_path)+'.tmp')
    temp_memory.write_text(json.dumps(memory,allow_nan=False))
    temp_memory.replace(memory_path)
    log_dir = memory_path.parent/'replan_logs'
    log_dir.mkdir(parents=True,exist_ok=True)
    audit['runtime_seconds'] = time.monotonic()-started
    (log_dir/f"{state['today']}.json").write_text(json.dumps(audit,indent=2,allow_nan=False))
