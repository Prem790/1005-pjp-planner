"""Independent CSV/workbook check; does not import planner or route solver."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import pandas as pd


def distance(a,b):
    lat1,lon1,lat2,lon2 = map(math.radians,(*a,*b))
    h = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742*math.asin(min(1,math.sqrt(h)))


def verify(workbook,folder,focus):
    digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
    dealers = pd.read_excel(workbook,sheet_name='Dealers').set_index('dealer_id')
    officers = pd.read_excel(workbook,sheet_name='Officers').set_index('so_id')
    calendar = pd.read_excel(workbook,sheet_name='Planning_Calendar')
    workdays = set(pd.to_datetime(calendar.loc[calendar.working_day.eq('Yes'),'date']).dt.strftime('%Y-%m-%d'))
    plan = pd.read_csv(folder/'plan_november.csv')
    initial_focus_hash = hashlib.sha256(focus.read_bytes()).hexdigest()
    provisional = pd.read_csv(focus)
    focus = pd.read_csv(folder/'scheduled_focus_dealers.csv')
    repairs = pd.read_csv(folder/'focus_repairs.csv')
    removed = set(provisional.dealer_id)-set(focus.dealer_id)
    added = set(focus.dealer_id)-set(provisional.dealer_id)
    assert removed==set(repairs.removed_account_id)
    assert added==set(repairs.replacement_account_id)
    assert len(provisional)==len(focus)
    assert not any(dealers.loc[k,'loyalty_club_member']=='Yes' for k in removed)
    for r in repairs.itertuples():
        assert dealers.loc[r.removed_account_id,'so_id']==r.so_id
        assert dealers.loc[r.replacement_account_id,'so_id']==r.so_id
    assert set(focus.loc[focus.loyalty_club_member.eq('Yes'),'dealer_id'])==set(dealers.loc[dealers.loyalty_club_member.eq('Yes')].index)
    roots = focus.root_dealer_id.value_counts()
    for root,n in roots.items():
        if n>1:
            assert focus.loc[focus.root_dealer_id.eq(root),'loyalty_club_member'].eq('Yes').all()
    daily = pd.read_csv(folder/'daily_routes.csv').set_index(['so_id','date'])
    summary = json.loads((folder/'plan_summary.json').read_text())
    assert digest==summary['source_sha256']
    assert initial_focus_hash==summary['focus_sha256']
    assert len(plan)==450 and len(daily)==75
    expected = Counter({r.dealer_id: 2 if dealers.loc[r.dealer_id,'loyalty_club_member']=='Yes' else 1
                        for r in focus.itertuples()})
    assert Counter(plan.account_id)==expected
    assert not plan.duplicated(['so_id','date','account_id']).any()
    checked = 0
    for so,officer in officers.iterrows():
        rows = plan.loc[plan.so_id.eq(so)]
        assert set(rows.date)==workdays
        assert pd.read_csv(folder/f'plan_{so}.csv').equals(rows.reset_index(drop=True))
        kind = 'upcountry' if so=='SO-C' else 'metro'
        for date,stops in rows.groupby('date'):
            stops = stops.sort_values('sequence')
            assert list(stops.sequence)==list(range(1,7))
            position = (officer.approx_day_start_lat,officer.approx_day_start_lon)
            used = 0
            for r in stops.itertuples():
                d = dealers.loc[r.account_id]
                assert d.so_id==so
                if pd.isna(d.lat) or pd.isna(d.lon):
                    travel = 45 if kind=='upcountry' else 25
                else:
                    nxt = (d.lat,d.lon)
                    travel = distance(position,nxt)*1.35/(30 if kind=='upcountry' else 18)*60
                    position = nxt
                assert abs(travel-r.travel_min_from_previous)<.000001
                arrival = math.ceil(570+used+travel-1e-9)
                assert r.planned_arrival==f'{arrival//60:02d}:{arrival%60:02d}'
                used += travel+25
            assert used<=460+1e-7
            for field,value in dict(dg_call_1_start='17:10',dg_call_1_end='17:50',
                                    dg_call_2_start='17:50',dg_call_2_end='18:30').items():
                assert daily.loc[(so,date),field]==value
            assert abs(used-daily.loc[(so,date),'dealer_route_minutes'])<.000001
            checked += 1
    gaps = []
    for k,count in expected.items():
        if count==2:
            dates = sorted(pd.to_datetime(plan.loc[plan.account_id.eq(k),'date']))
            gap = (dates[1]-dates[0]).days
            assert gap>=10
            gaps.append(gap)
    assert hashlib.sha256(workbook.read_bytes()).hexdigest()==digest
    result = dict(status='passed',source_sha256=digest,visits_checked=len(plan),routes_checked=checked,
                  focus_dealers=len(expected),focus_repairs=len(repairs),clubs_checked=len(gaps),min_club_gap_days=min(gaps),
                  checks=['ownership','workdays','frequency','six_stops_per_day','sequence','no_duplicates',
                          'independent_travel_and_budget','arrival_times','club_spacing','per_SO_exports','source_unchanged'])
    (folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--workbook',type=Path,required=True)
    p.add_argument('--plan-dir',type=Path,default=Path('artifacts/plan'))
    p.add_argument('--focus',type=Path,default=Path('artifacts/focus/focus_dealers.csv'))
    args = p.parse_args()
    verify(args.workbook,args.plan_dir,args.focus)
