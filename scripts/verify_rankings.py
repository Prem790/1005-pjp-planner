#!/usr/bin/env python3
"""Independent source-to-output checks; does not import the ranking implementation.

Run from the repository root after generating artifacts/ranking:
python scripts/verify_rankings.py --workbook data/private/PJP_Assignment_Sample_Data.xlsx
"""
import argparse
import collections
import csv
import datetime as dt
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path

import openpyxl


def read_csv(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def sheet(book,name,header=1):
    it=book[name].iter_rows(values_only=True)
    for _ in range(header-1):
        next(it)
    columns=next(it)
    return [dict(zip(columns,row)) for row in it if row[0] is not None]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=Path('config/ranking.json'))
    parser.add_argument('--out',type=Path,default=Path('artifacts/ranking'))
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    summary=json.loads((args.out/'ranking_summary.json').read_text())
    rows=read_csv(args.out/'dealer_rankings.csv')
    by_id={r['dealer_id']:r for r in rows}
    book=openpyxl.load_workbook(args.workbook,read_only=True,data_only=True)
    try:
        dealers=sheet(book,'Dealers')
        sales=sheet(book,'Sales_Daily')
    finally:
        book.close()
    checks={}
    checks['source_checksum_matches']=hashlib.sha256(args.workbook.read_bytes()).hexdigest()==summary['source_sha256']
    checks['one_row_per_original_dealer']=len(rows)==len(dealers)==len(by_id) and set(by_id)=={d['dealer_id'] for d in dealers}
    checks['current_ownership_preserved']=all(by_id[d['dealer_id']]['so_id']==d['so_id'] for d in dealers)
    checks['explicit_club_flags_preserved']=all((by_id[d['dealer_id']]['is_club']=='True')==(d['loyalty_club_member']=='Yes') for d in dealers)
    checks['finite_bounded_scores']=all(math.isfinite(float(r[c])) and 0<=float(r[c])<=100 for r in rows
        for c in ['priority_score','value_loss_score','sudden_drop_score','potential_score','scheme_score'])
    checks['weighted_components_reconcile']=all(math.isclose(float(r['priority_score']),sum(
        config['component_weights'][key]*float(r[col]) for key,col in [
            ('value_loss','value_loss_score'),('sudden_drop','sudden_drop_score'),
            ('potential_proxy','potential_score'),('scheme_opportunity','scheme_score')]),rel_tol=1e-12,abs_tol=1e-10) for r in rows)
    so_rows=collections.defaultdict(list)
    for r in rows:
        so_rows[r['so_id']].append(r)
    checks['per_so_files_and_ranks_match']=all(
        [int(r['rank_within_so']) for r in group]==list(range(1,len(group)+1)) and
        read_csv(args.out/f'rankings_{so}.csv')==group for so,group in so_rows.items())
    # Use an independent implementation of linear interpolation for the normalizing quantile.
    normalization_ok=True
    for group in so_rows.values():
        for raw,score in [('raw_weighted_value_loss','value_loss_score'),('supported_raw_sudden_loss','sudden_drop_score')]:
            positive=sorted(float(r[raw]) for r in group if float(r[raw])>0)
            if positive:
                position=(len(positive)-1)*config['normalization']['quantile']
                left=math.floor(position);right=math.ceil(position)
                scale=positive[left]+(position-left)*(positive[right]-positive[left])
            else:
                scale=0
            normalization_ok &= all(math.isclose(float(r[score]),100*min(float(r[raw])/scale,1) if scale else 0,
                                                 rel_tol=1e-12,abs_tol=1e-9) for r in group)
    checks['territory_normalization_reconciles']=normalization_ok
    sample={r['dealer_id'] for group in so_rows.values() for r in group[:3]}
    for dealer in dealers:
        if dealer['parent_dealer_id']:
            sample.update([dealer['dealer_id'],dealer['parent_dealer_id']])
    sample.update(next((r['dealer_id'] for r in group if r['recency_category']=='never_purchased'),group[-1]['dealer_id'])
                  for group in so_rows.values())
    windows={k:tuple(dt.date.fromisoformat(x) for x in dates) for k,dates in config['comparison_periods'].items()}
    totals={did:{name:Decimal(0) for name in windows} for did in sample}
    cutoff=dt.date.fromisoformat(config['as_of'])
    sale_days=collections.defaultdict(set)
    active_weeks=collections.defaultdict(set)
    master={d['dealer_id']:d for d in dealers}
    roots={}
    for did in master:
        root=did
        while master[root]['parent_dealer_id']:
            root=master[root]['parent_dealer_id']
        roots[did]=root
    points=[]
    for sale in sales:
        day=sale['date'].date()
        if day>cutoff:
            continue
        did=sale['dealer_id']
        points.append((roots[did],day,Decimal(str(sale['sheets']))*Decimal(str(sale['points_per_sheet']))))
        if did not in sample:
            continue
        value=Decimal(str(sale['value_inr']))
        for name,(start,end) in windows.items():
            if start<=day<=end:
                totals[did][name]+=value
        if windows['sudden_reference'][0]<=day<=windows['sudden_reference'][1] and sale['txn_type']=='Sale' and (value>0 or sale['sheets']>0):
            sale_days[did].add(day)
            active_weeks[did].add((day-windows['sudden_reference'][0]).days//7)
    comparisons=[]
    for did,values in sorted(totals.items()):
        r=by_id[did]
        loss={}
        for name,months in [('mom',1),('qoq',3),('yoy',3)]:
            reference,current=values[name+'_reference'],values[name+'_current']
            loss[name]=max(Decimal(0),reference-current)/months if reference>0 else Decimal(0)
        raw=sum(Decimal(str(config['value_loss_weights'][name]))*loss[name] for name in loss)
        expected=max(Decimal(0),values['sudden_reference'])*Decimal(14)/56
        support=min(1,len(sale_days[did])/config['sudden_drop']['full_support_positive_sale_days'])*min(
            1,len(active_weeks[did])/config['sudden_drop']['full_support_active_reference_weeks'])
        passed=(all(math.isclose(float(values[name]),float(r[name+'_net_value']),abs_tol=1e-8) for name in windows) and
                math.isclose(float(raw),float(r['raw_weighted_value_loss']),abs_tol=1e-8) and
                math.isclose(float(expected),float(r['expected_recent_value']),abs_tol=1e-8) and
                math.isclose(support,float(r['sudden_support']),abs_tol=1e-10))
        comparisons.append(dict(dealer_id=did,passed=passed,independent_raw_value_loss=float(raw),
                                independent_expected_recent_value=float(expected),independent_support=support))
    checks['sampled_source_calculations_reconcile']=all(r['passed'] for r in comparisons)
    scheme_rows=read_csv(args.out/'scheme_context.csv')
    schemes_ok=True
    for row in scheme_rows:
        start=dt.date.fromisoformat(row['scheme_window_start'])
        earned=sum((point for root,day,point in points if root==row['root_dealer_id'] and start<=day<=cutoff),Decimal(0))
        schemes_ok &= math.isclose(float(earned),float(row['scheme_net_points']),abs_tol=1e-8)
        if row['scheme_next_target']:
            schemes_ok &= math.isclose(float(row['scheme_next_target'])-float(earned),float(row['scheme_gap_points']),abs_tol=1e-8)
    checks['all_scheme_points_reconcile_to_signed_owner_group_sales']=schemes_ok
    result=dict(all_passed=all(checks.values()),checks=checks,sampled_dealers=comparisons,
                scheme_rows_checked=len(scheme_rows),method='Independent openpyxl/Decimal source sums and CSV checks; ranking implementation is not imported.')
    (args.out/'verification.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='sampled_dealers'},indent=2))
    if not result['all_passed']:
        raise SystemExit('Ranking verification failed; inspect verification.json')


if __name__=='__main__':
    main()
