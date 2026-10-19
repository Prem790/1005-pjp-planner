"""Independently reconcile message evidence to plan, focus and source ledger.

Does not import the nudge generator or ranking implementation.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import pandas as pd


def close(a,b):
    assert math.isclose(float(a),float(b),abs_tol=1e-6,rel_tol=1e-10),(a,b)


def verify(workbook,plan_dir,folder):
    source_hash = hashlib.sha256(workbook.read_bytes()).hexdigest()
    config = json.loads(Path('config/ranking.json').read_text())
    summary = json.loads((folder/'nudge_summary.json').read_text())
    assert source_hash==summary['source_sha256']
    for name,digest in summary['input_sha256'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
    plan = pd.read_csv(plan_dir/'plan_november.csv')
    focus = pd.read_csv(plan_dir/'scheduled_focus_dealers.csv').set_index('dealer_id')
    rows = pd.read_csv(folder/'visit_nudges.csv')
    examples = pd.read_csv(folder/'example_nudges.csv')
    evidence = [json.loads(line) for line in (folder/'nudge_evidence.jsonl').read_text().splitlines()]
    assert len(rows)==len(plan)==len(evidence)
    assert rows.nudge_id.is_unique
    assert Counter(zip(rows.date,rows.so_id,rows.account_id))==Counter(zip(plan.date,plan.so_id,plan.account_id))
    assert rows.message.str.len().eq(rows.character_count).all() and rows.character_count.lt(300).all()
    assert len(examples)>=5 and set(examples.nudge_id)<=set(rows.nudge_id)
    with pd.ExcelFile(workbook) as book:
        sales = pd.read_excel(book,sheet_name='Sales_Daily')
        master = pd.read_excel(book,sheet_name='Dealers').set_index('dealer_id')
        targets = pd.read_excel(book,sheet_name='Scheme_Targets',header=2)
    sales['date'] = pd.to_datetime(sales.date)
    as_of = pd.Timestamp(config['as_of'])
    sales = sales.loc[sales.date.le(as_of)].copy()
    parent = master.parent_dealer_id.to_dict()
    roots = {}
    for k in parent:
        cursor = k
        while pd.notna(parent[cursor]):
            cursor = parent[cursor]
        roots[k] = cursor
    sales['root'] = sales.dealer_id.map(roots)
    sales['points'] = sales.sheets*sales.points_per_sheet
    totals = {}
    for name,(start,end) in config['comparison_periods'].items():
        totals[name] = sales.loc[sales.date.between(start,end)].groupby('dealer_id').value_inr.sum().to_dict()
    positive = sales.loc[sales.txn_type.eq('Sale') & (sales.sheets.gt(0) | sales.value_inr.gt(0))]
    last_sale = positive.groupby('dealer_id').date.max().to_dict()
    evidence_lookup = {r['nudge_id']:r for r in evidence}
    visits = plan.set_index(['date','so_id','account_id'])
    counts = Counter()
    for r in rows.itertuples():
        visit = visits.loc[(r.date,r.so_id,r.account_id)]
        d = focus.loc[r.account_id]
        assert master.loc[r.account_id,'so_id']==r.so_id
        assert r.planned_arrival==visit.planned_arrival and r.sequence==visit.sequence
        send = pd.Timestamp(r.send_at)
        assert send.date()==(pd.Timestamp(r.date)-pd.Timedelta(days=1)).date()
        assert (send.hour,send.minute)==(18,30) and send.utcoffset().total_seconds()==19800
        proof = evidence_lookup[r.nudge_id]
        for key in ['message','account_id','date','send_at','character_count']:
            assert proof[key]==getattr(r,key)
        assert proof['evidence']['snapshot_date']==config['as_of']
        assert '15 Oct data.' in r.message
        for fact in proof['evidence']['claims']:
            assert fact['text'] in r.message
            for field,value in fact['evidence'].items():
                source = d[field]
                if value is None:
                    assert pd.isna(source)
                elif isinstance(value,(float,int)) and not isinstance(value,bool):
                    close(value,source)
                else:
                    assert str(value)==str(source),(field,value,source)
            kind = fact['kind']
            counts[kind] += 1
            if kind=='value_loss':
                did = r.account_id
                loss_mom = max(0,totals['mom_reference'].get(did,0)-totals['mom_current'].get(did,0))
                loss_qoq = max(0,totals['qoq_reference'].get(did,0)-totals['qoq_current'].get(did,0))/3
                loss_yoy = max(0,totals['yoy_reference'].get(did,0)-totals['yoy_current'].get(did,0))/3
                weights = config['value_loss_weights']
                close(d.raw_weighted_value_loss,weights['mom']*loss_mom+weights['qoq']*loss_qoq+weights['yoy']*loss_yoy)
            elif kind=='recent_drop':
                close(d.actual_recent_value,totals['sudden_current'].get(r.account_id,0))
                close(d.expected_recent_value,max(0,totals['sudden_reference'].get(r.account_id,0))*14/56)
            elif kind in ['reactivation','first_purchase']:
                if kind=='first_purchase':
                    assert r.account_id not in last_sale
                else:
                    assert pd.Timestamp(d.last_positive_sale_date)==last_sale[r.account_id]
            elif kind=='returns_review':
                start,end = config['comparison_periods']['sudden_current']
                part = sales.loc[sales.dealer_id.eq(r.account_id) & sales.date.between(start,end)]
                close(d.recent_return_value,part.loc[part.txn_type.eq('Return'),'value_inr'].sum())
                close(d.recent_gross_sale_value,part.loc[part.txn_type.eq('Sale'),'value_inr'].sum())
            elif kind=='location_check':
                dealer = master.loc[r.account_id]
                assert pd.isna(dealer.lat) or pd.isna(dealer.lon)
            elif kind=='scheme_context':
                assert roots[r.account_id]==d.root_dealer_id
                start,end = pd.Timestamp(d.scheme_window_start),pd.Timestamp(d.scheme_window_end)
                assert start<=pd.Timestamp(r.date)<=end
                part = sales.loc[sales.root.eq(d.root_dealer_id) & sales.date.between(start,as_of)]
                close(d.scheme_net_points,part.points.sum())
                close(d.scheme_gap_points,d.scheme_next_target-d.scheme_net_points)
                candidates = targets.loc[targets.parent_dealer_id.eq(d.root_dealer_id) & targets.scheme.eq(d.scheme_name)]
                slab = candidates.loc[candidates.slab.eq(d.scheme_next_slab)]
                assert not slab.empty
                assert any(math.isclose(float(x),d.scheme_next_target) for x in slab.slab_target_points)
                if str(d.scheme_target_source).startswith('assumed_'):
                    assert 'target assumed' in r.message
                assert 'verify balance/offer' in r.message
    assert hashlib.sha256(workbook.read_bytes()).hexdigest()==source_hash
    result = dict(status='passed',messages_checked=len(rows),examples_checked=len(examples),
                  max_characters=int(rows.character_count.max()),claims_checked=dict(counts),
                  source_sha256=source_hash,source_unchanged=True,
                  checks=['dated_plan_identity','length','previous_evening_IST','evidence_to_focus',
                          'raw_ledger_losses_and_recent_values','positive_sale_dates','return_signs',
                          'owner_group_points_and_target','missing_GPS','input_hashes'])
    (folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--workbook',type=Path,required=True)
    p.add_argument('--plan-dir',type=Path,default=Path('artifacts/plan'))
    p.add_argument('--out',type=Path,default=Path('artifacts/nudges'))
    a = p.parse_args()
    verify(a.workbook,a.plan_dir,a.out)
