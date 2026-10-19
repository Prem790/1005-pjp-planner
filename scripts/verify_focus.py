#!/usr/bin/env python3
"""Independent focus-output integrity and optimistic travel checks.

Does not import the selection or travel implementation.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import openpyxl


def csv_rows(path):
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def sheet(book,name):
    it=book[name].iter_rows(values_only=True)
    head=next(it)
    return [dict(zip(head,row)) for row in it if row[0] is not None]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workbook',type=Path,required=True)
    p.add_argument('--ranking',type=Path,default=Path('artifacts/ranking/dealer_rankings.csv'))
    p.add_argument('--config',type=Path,default=Path('config/selection.json'))
    p.add_argument('--out',type=Path,default=Path('artifacts/focus'))
    a=p.parse_args()
    cfg=json.loads(a.config.read_text());model=cfg['travel_model']
    summary=json.loads((a.out/'focus_summary.json').read_text())
    decisions=csv_rows(a.out/'selection_decisions.csv');selected=csv_rows(a.out/'focus_dealers.csv')
    deferred=csv_rows(a.out/'deferred_dealers.csv');ranking=csv_rows(a.ranking)
    book=openpyxl.load_workbook(a.workbook,read_only=True,data_only=True)
    try:
        dealers=sheet(book,'Dealers');officers=sheet(book,'Officers');calendar=sheet(book,'Planning_Calendar')
    finally:
        book.close()
    master={d['dealer_id']:d for d in dealers};by_id={r['dealer_id']:r for r in decisions}
    selected_ids={r['dealer_id'] for r in selected};deferred_ids={r['dealer_id'] for r in deferred}
    clubs={d['dealer_id'] for d in dealers if d['loyalty_club_member']=='Yes'}
    checks={
        'source_hash_unchanged':hashlib.sha256(a.workbook.read_bytes()).hexdigest()==summary['source_sha256'],
        'ranking_hash_matches':hashlib.sha256(a.ranking.read_bytes()).hexdigest()==summary['ranking_sha256'],
        'one_decision_per_dealer':len(decisions)==len(by_id)==len(master) and set(by_id)==set(master),
        'selected_and_deferred_partition_universe':not(selected_ids&deferred_ids) and selected_ids|deferred_ids==set(master),
        'all_clubs_protected':clubs<=selected_ids,
        'ownership_matches_master':all(r['so_id']==master[r['dealer_id']]['so_id'] for r in decisions),
        'selected_visits_match_membership':all(int(r['selected_visit_requirement'])==(2 if r['dealer_id'] in clubs else 1) for r in selected),
        'selected_flags_match_files':all((r['selected']=='True')==(r['dealer_id'] in selected_ids) for r in decisions),
        'every_decision_explained':all(r['decision_reason'] for r in decisions),
        'commercial_scores_not_modified':all(math.isclose(float(r['priority_score']),float(by_id[r['dealer_id']]['priority_score']),abs_tol=1e-10) for r in ranking),
        'explicitly_not_route_verified':summary['full_route_feasibility_verified'] is False and summary['dated_schedule_generated'] is False,
    }
    workdays=sum(r['working_day']=='Yes' and r['date'].strftime('%Y-%m')==cfg['planning_month'] for r in calendar)
    checks['budget_and_per_so_exports_match']=True
    for officer in officers:
        so=officer['so_id'];group=[r for r in selected if r['so_id']==so]
        visits=sum(int(r['selected_visit_requirement']) for r in group)
        cap=workdays*cfg['normal_visits_per_day']
        stats=summary['officers'][so]
        checks['budget_and_per_so_exports_match'] &= (visits<=cap and visits==stats['required_visits'] and
            cap-visits==stats['unfilled_visit_slots'] and csv_rows(a.out/f'focus_{so}.csv')==group)
    protected_roots={r['root_dealer_id'] for r in selected if r['dealer_id'] in clubs}
    optional=[r for r in selected if r['dealer_id'] not in clubs]
    checks['optional_root_redundancy_avoided']=(len({r['root_dealer_id'] for r in optional})==len(optional) and
        not ({r['root_dealer_id'] for r in optional}&protected_roots))
    officers={r['so_id']:r for r in officers};budget=model['day_minutes']-model['demand_generation_minutes']
    travel_ok=True
    for r in decisions:
        d=master[r['dealer_id']];o=officers[r['so_id']]
        kind='upcountry' if 'upcountry' in str(o['geography']).lower() else 'metro'
        if d['lat'] is None or d['lon'] is None:
            direct=model['unknown_location_travel_minutes'][kind]
        else:
            x1,y1,x2,y2=map(math.radians,[o['approx_day_start_lat'],o['approx_day_start_lon'],d['lat'],d['lon']])
            h=math.sin((x2-x1)/2)**2+math.cos(x1)*math.cos(x2)*math.sin((y2-y1)/2)**2
            distance=6371*2*math.asin(min(1,math.sqrt(h)))
            direct=distance*model['road_factor']/model['speed_kmh'][kind]*60
        bound=direct+cfg['normal_visits_per_day']*model['dealer_call_minutes']
        passes=bound<=budget+1e-9
        travel_ok &= (math.isclose(bound,float(r['optimistic_six_visit_day_minutes']),abs_tol=1e-8) and
                      passes==(r['normal_day_screen_pass']=='True') and
                      (r['dealer_id'] not in selected_ids or passes))
    checks['all_travel_bounds_recomputed']=travel_ok
    result=dict(all_passed=all(checks.values()),checks=checks,selected_dealers=len(selected),
        protected_club_outlets=len(clubs),required_visits=sum(int(r['selected_visit_requirement']) for r in selected),
        method='Independent openpyxl/csv checks and travel arithmetic; selection implementation is not imported.')
    (a.out/'verification.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2))
    if not result['all_passed']:
        raise SystemExit('Focus verification failed')


if __name__=='__main__':
    main()
