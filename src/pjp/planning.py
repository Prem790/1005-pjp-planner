"""Dated, capacity-constrained static planning from the provisional focus set."""
import hashlib
import json
import math
import random
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd

from .routing import RouteSolver
from .selection import _bool_series, validate_config


class CalendarSearchFailure(ValueError):
    def __init__(self, message, groups):
        super().__init__(message)
        self.groups = groups


def club_dates(days, count, target_gap=14):
    """Spread first rounds across early working days; same-weekday second round."""
    index = {d: i for i,d in enumerate(days)}
    pairs = [(i,index[d+timedelta(days=target_gap)]) for i,d in enumerate(days)
             if d+timedelta(days=target_gap) in index]
    if count and not pairs:
        raise ValueError('Calendar cannot support the required club cadence')
    early = pairs[:min(10,len(pairs))]
    return [early[i % len(early)] for i in range(count)]


def schedule_officer(records, days, solver, policy):
    """Fixed club cadence, geographic initialization, then reproducible swaps.

    Search minimizes travel and priority-weighted lateness with a steep penalty
    for overtime. It returns only a fully feasible schedule, or raises.
    """
    clubs = sorted((k for k,v in records.items() if v['is_club']),
                   key=lambda k: (-solver.priority(k),k))
    fixed = [[] for _ in days]
    for k,(a,b) in zip(clubs,club_dates(days,len(clubs),policy['club_target_gap_days'])):
        fixed[a].append(k)
        fixed[b].append(k)
    if any(len(x)>6 for x in fixed):
        raise ValueError('Club commitments exceed daily visit capacity')
    optional = [k for k in records if k not in clubs]
    if len(optional)+2*len(clubs) != 6*len(days):
        raise ValueError('Focus visit requirements do not fill the monthly capacity')
    budget = solver.model['day_minutes']-solver.model['demand_generation_minutes']
    service = 6*solver.model['dealer_call_minutes']
    slots = [(i,j) for i,row in enumerate(fixed) for j in range(len(row),6)]

    def cost(i, row):
        travel,_ = solver.route(row)
        over = max(0,travel+service-budget)
        late = sum(solver.priority(k) for k in row)*i/max(1,len(days)-1)
        return travel+policy['priority_date_weight']*late+2000*over+20*over*over

    best = None
    best_cost = math.inf
    least_overtime = math.inf
    closest = None
    for attempt in range(policy['max_attempts']):
        rng = random.Random(policy['random_seed']+attempt)
        # Geographic blocks keep long upcountry trips together from the outset.
        ordered = sorted(optional, key=lambda k: (solver.position[k] is None,
                          round(solver.position[k][1],1) if solver.position[k] else 0,
                          solver.position[k][0] if solver.position[k] else -solver.priority(k),k))
        groups = [list(row) for row in fixed]
        if attempt:
            rng.shuffle(ordered)
            for (i,j),k in zip(slots,ordered):
                groups[i].append(k)
        else:
            # Seed an empty day with the furthest remaining dealer, then choose
            # insertions by the full open route rather than pairwise proximity.
            # This keeps the small remote clusters together without splitting
            # a six-stop block around a pre-booked club appointment.
            remaining = set(optional)
            empty = [i for i,row in enumerate(groups) if not row]
            for day in empty:
                far = [k for k in remaining if solver.position[k] is not None and solver.initial[k]>160]
                if not far:
                    break
                seed = max(far,key=lambda k: (solver.initial[k],k))
                groups[day].append(seed)
                remaining.remove(seed)
                while len(groups[day])<6:
                    candidates = sorted(remaining)
                    if not candidates:
                        break
                    viable = [k for k in candidates if solver.route(groups[day]+[k])[0]+service<=budget+1e-7]
                    choices = viable or candidates
                    def insertion_cost(k):
                        proximity = solver.distance[seed,k] if solver.position[k] is not None else 500
                        return (solver.route(groups[day]+[k])[0]+.5*proximity,-solver.priority(k),k)
                    nxt = min(choices,key=insertion_cost)
                    groups[day].append(nxt)
                    remaining.remove(nxt)
            ordered = sorted(remaining,key=lambda k: (-solver.priority(k),k))
            open_slots = [(i,j) for i,row in enumerate(groups) for j in range(len(row),6)]
            for (i,j),k in zip(open_slots,ordered):
                groups[i].append(k)
        costs = [cost(i,row) for i,row in enumerate(groups)]
        for step in range(policy['iterations_per_attempt']):
            (a,x),(b,y) = rng.sample(slots,2)
            if a==b:
                continue
            groups[a][x],groups[b][y] = groups[b][y],groups[a][x]
            new_a,new_b = cost(a,groups[a]),cost(b,groups[b])
            delta = new_a+new_b-costs[a]-costs[b]
            temperature = 80*(.002**(step/policy['iterations_per_attempt']))
            if delta<=0 or rng.random()<math.exp(-min(delta/temperature,700)):
                costs[a],costs[b] = new_a,new_b
            else:
                groups[a][x],groups[b][y] = groups[b][y],groups[a][x]
            if sum(costs)<best_cost or step % 100 == 0 or step == policy['iterations_per_attempt']-1:
                total = sum(costs)
                overtime = sum(max(0,solver.route(row)[0]+service-budget) for row in groups)
                if overtime < least_overtime:
                    least_overtime = overtime
                    closest = [list(row) for row in groups]
                if total<best_cost and all(solver.route(row)[0]+service <= budget+1e-7 for row in groups):
                    best_cost = total
                    best = [list(row) for row in groups]
        if best is not None:
            break
    if best is None:
        details = [(i,round(solver.route(row)[0]+service,1),row)
                   for i,row in enumerate(closest) if solver.route(row)[0]+service>budget+1e-7]
        raise CalendarSearchFailure(f'No feasible six-stop calendar found. Best overtime: {least_overtime:.1f}; days: {details}',closest)
    return best


def clock_text(minutes):
    # Round upwards to avoid suggesting an arrival before the calculated time.
    minute = math.ceil(minutes-1e-9)
    return f'{minute//60:02d}:{minute%60:02d}'


def run_planning(workbook, focus_path, selection_path, policy_path, out):
    workbook,focus_path,out = Path(workbook),Path(focus_path),Path(out)
    source_hash = hashlib.sha256(workbook.read_bytes()).hexdigest()
    selection = json.loads(Path(selection_path).read_text())
    model = validate_config(selection)
    policy = json.loads(Path(policy_path).read_text())
    if policy['start_time']!='09:30':
        raise ValueError('This calendar uses the assignment 09:30 start and fixed DG blocks')
    if policy['iterations_per_attempt']<1 or policy['max_attempts']<1:
        raise ValueError('Search iterations and attempts must be positive')
    if policy['club_target_gap_days']<selection['club_min_gap_days']:
        raise ValueError('Target club gap is below the required minimum')
    summary_path = focus_path.parent/'focus_summary.json'
    metadata = json.loads(summary_path.read_text())
    if metadata['config_sha256'] != hashlib.sha256(Path(selection_path).read_bytes()).hexdigest():
        raise ValueError('Selection configuration changed; regenerate focus selection')
    if metadata['source_sha256'] != source_hash:
        raise ValueError('Focus source workbook checksum differs')
    focus = pd.read_csv(focus_path)
    for c in ['has_location','is_club']:
        focus[c] = _bool_series(focus[c],c)
    if focus.dealer_id.duplicated().any():
        raise ValueError('Focus account identities must be unique')
    if not focus.planning_month.eq(selection['planning_month']).all():
        raise ValueError('Focus month differs from planning month')
    master = pd.read_excel(workbook,sheet_name='Dealers').set_index('dealer_id')
    if not set(focus.dealer_id)<=set(master.index):
        raise ValueError('Focus contains unknown dealer IDs')
    for r in focus.itertuples():
        d = master.loc[r.dealer_id]
        if r.so_id!=d.so_id or r.is_club!=(d.loyalty_club_member=='Yes'):
            raise ValueError('Focus ownership or club membership differs from workbook')
        has_location = pd.notna(d.lat) and pd.notna(d.lon)
        if r.has_location!=has_location:
            raise ValueError('Focus location flag differs from workbook')
        if has_location and (abs(r.lat-d.lat)>1e-9 or abs(r.lon-d.lon)>1e-9):
            raise ValueError('Focus coordinates differ from workbook')
        if not math.isfinite(r.priority_score) or not 0<=r.priority_score<=100:
            raise ValueError('Focus priority must be finite and within 0–100')
    officers = pd.read_excel(workbook,sheet_name='Officers')
    calendar = pd.read_excel(workbook,sheet_name='Planning_Calendar')
    calendar['date'] = pd.to_datetime(calendar.date)
    days = list(calendar.loc[calendar.working_day.eq('Yes') & calendar.date.dt.strftime('%Y-%m').eq(selection['planning_month']),'date'].sort_values())
    if len(days)!=25 or any(d.dayofweek==6 for d in days) or len(set(days))!=25:
        raise ValueError('Expected 25 unique November working dates')
    if set(focus.so_id)!=set(officers.so_id):
        raise ValueError('Focus must cover each officer')
    visits,daily,repairs = [],[],[]
    decisions = pd.read_csv(focus_path.parent/'selection_decisions.csv')
    for c in ['has_location','is_club']:
        decisions[c] = _bool_series(decisions[c],c)
    for officer in officers.to_dict('records'):
        so = officer['so_id']
        records = focus.loc[focus.so_id.eq(so)].set_index('dealer_id').to_dict('index')
        kinds = {v['geography_kind'] for v in records.values()}
        if len(kinds)!=1:
            raise ValueError('Each officer requires one consistent territory kind')
        kind = kinds.pop()
        start = (float(officer['approx_day_start_lat']),float(officer['approx_day_start_lon']))
        solver = RouteSolver(records,start,kind,model)
        for repair_attempt in range(policy.get('max_focus_repairs_per_officer',0)+1):
            solver = RouteSolver(records,start,kind,model)
            try:
                groups = schedule_officer(records,days,solver,policy)
                break
            except CalendarSearchFailure as failure:
                if repair_attempt>=policy.get('max_focus_repairs_per_officer',0):
                    raise
                budget = model['day_minutes']-model['demand_generation_minutes']
                bad = [row for row in failure.groups if solver.route(row)[0]+150>budget+1e-7]
                candidates = {k for row in bad for k in row if not records[k]['is_club']}
                # Only repair small remote clusters that failed full routing.
                isolated = [k for k in candidates if solver.position[k] is not None
                            and solver.initial[k]>160 and
                            sum(solver.position[j] is not None and
                                (j==k or solver.distance[k,j]<=30) for j in records)<6]
                if not isolated:
                    raise
                removed = min(isolated,key=lambda k: (solver.priority(k),k))
                active_roots = {v['root_dealer_id'] for k,v in records.items() if k!=removed}
                pool = decisions.loc[decisions.so_id.eq(so) &
                    decisions.decision_reason.eq('deferred_monthly_visit_budget') &
                    decisions.normal_day_screen_pass.eq(True) & ~decisions.is_club &
                    ~decisions.dealer_id.isin(records) &
                    ~decisions.root_dealer_id.isin(active_roots)].sort_values('rank_within_so')
                if pool.empty:
                    raise
                replacement = pool.iloc[0].to_dict()
                added = replacement.pop('dealer_id')
                reason = ('Full-route search exceeded 460 minutes in a small remote cluster; '
                          'lowest-priority eligible non-club in affected cluster deferred; '
                          'next commercially ranked eligible reserve selected. Search failure is not proof of infeasibility.')
                repairs.append(dict(so_id=so,removed_account_id=removed,replacement_account_id=added,
                    removed_priority_score=records[removed]['priority_score'],
                    replacement_priority_score=replacement['priority_score'],reason=reason))
                del records[removed]
                records[added] = replacement
                focus = focus.loc[~focus.dealer_id.eq(removed)].copy()
                replacement['dealer_id'] = added
                replacement['selected'] = True
                replacement['decision_reason'] = 'selected_route_repair_reserve'
                replacement['selected_visit_requirement'] = 1
                focus = pd.concat([focus,pd.DataFrame([replacement])],ignore_index=True)
                print(f'{so}: recorded focus repair {removed} -> {added}',flush=True)
        for date,group in zip(days,groups):
            travel,order = solver.route(group)
            legs = solver.legs(order)
            hour,minute = map(int,policy['start_time'].split(':'))
            elapsed = hour*60+minute
            for sequence,(k,leg) in enumerate(zip(order,legs),1):
                r = records[k]
                elapsed += leg
                arrival = clock_text(elapsed)
                elapsed += model['dealer_call_minutes']
                visits.append(dict(date=date.date().isoformat(),so_id=so,sequence=sequence,
                    account_id=k,account_type='Dealer',planned_arrival=arrival,
                    travel_min_from_previous=round(leg,6),planned_departure=clock_text(elapsed),
                    priority_score=r['priority_score'],is_club=r['is_club'],
                    location_review_required=not r['has_location'],
                    reason=('Club cadence; ' if r['is_club'] else '')+str(r['reason'])))
            rain_travel = sum(legs[:5])*model['rain_multiplier']
            rain_minutes = rain_travel+5*model['dealer_call_minutes']
            budget = model['day_minutes']-model['demand_generation_minutes']
            daily.append(dict(date=date.date().isoformat(),so_id=so,visits=6,
                travel_minutes=round(travel,6),dealer_service_minutes=150,
                dealer_route_minutes=round(travel+150,6),
                route_slack_minutes=round(budget-travel-150,6),
                route_end=clock_text(elapsed),
                dg_call_1_start='17:10',dg_call_1_end='17:50',
                dg_call_2_start='17:50',dg_call_2_end='18:30',
                unknown_locations=sum(not records[k]['has_location'] for k in order),
                rain_first_five_minutes=round(rain_minutes,6),
                rain_first_five_feasible=rain_minutes<=budget,
                dealer_order=' > '.join(order)))
        print(f'{so}: {len(groups)} feasible six-stop routes',flush=True)
    plan,route_table = pd.DataFrame(visits),pd.DataFrame(daily)
    expected = Counter({r.dealer_id: 2 if r.is_club else 1 for r in focus.itertuples()})
    if Counter(plan.account_id)!=expected:
        raise ValueError('Plan frequencies differ from the focus obligations')
    for k in focus.loc[focus.is_club,'dealer_id']:
        dates = sorted(pd.to_datetime(plan.loc[plan.account_id.eq(k),'date']))
        if (dates[1]-dates[0]).days<10:
            raise ValueError('Club gap violation')
    if route_table.route_slack_minutes.min() < -1e-6:
        raise ValueError('Route budget violation')
    if hashlib.sha256(workbook.read_bytes()).hexdigest()!=source_hash:
        raise ValueError('Workbook changed during planning')
    out.mkdir(parents=True,exist_ok=True)
    plan.to_csv(out/'plan_november.csv',index=False)
    for so,rows in plan.groupby('so_id'):
        rows.to_csv(out/f'plan_{so}.csv',index=False)
    route_table.to_csv(out/'daily_routes.csv',index=False)
    pd.DataFrame(repairs,columns=['so_id','removed_account_id','replacement_account_id',
        'removed_priority_score','replacement_priority_score','reason']).to_csv(out/'focus_repairs.csv',index=False)
    focus.to_csv(out/'scheduled_focus_dealers.csv',index=False)
    summary = dict(version=policy['version'],source_sha256=source_hash,
        focus_sha256=hashlib.sha256(focus_path.read_bytes()).hexdigest(),
        scheduling_config=policy,selection_config=selection,rows=len(plan),days=len(route_table),
        focus_repairs=len(repairs),simulator_evaluated=False,officers={})
    for so,rows in route_table.groupby('so_id'):
        subset = plan.loc[plan.so_id.eq(so)]
        summary['officers'][so] = dict(visits=len(subset),focus_dealers=subset.account_id.nunique(),
            max_route_minutes=float(rows.dealer_route_minutes.max()),
            mean_travel_minutes=float(rows.travel_minutes.mean()),
            min_slack_minutes=float(rows.route_slack_minutes.min()),
            rain_first_five_infeasible_days=int((~rows.rain_first_five_feasible).sum()))
    (out/'plan_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines = ['November 2026 static calendar and routes','450 dealer visits across 75 officer-days.',
             'Clubs twice, exactly 14 calendar days apart; other focus dealers once.',
             'Two DG calls reserved 17:10–17:50 and 17:50–18:30, including their travel.',
             'Travel matches simulator: open routes, no return leg; unknown GPS uses fixed allowance.',
             'Feasibility is model-based; unknown locations need field confirmation.',
             'Rain first-five diagnostic is a stress test, not a weather forecast.',
             f'No simulator adherence result is claimed. Focus replacements recorded: {len(repairs)}.','']
    lines += [f'{so}: {values}' for so,values in summary['officers'].items()]
    (out/'plan_summary.txt').write_text('\n'.join(lines)+'\n')
    print(f'Wrote plan and routes to {out}')
    return plan,route_table,summary
