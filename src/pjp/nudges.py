"""Private, deterministic pre-visit drafts with field-level evidence.

These messages are generated locally for Sales Officers. Nothing is sent to
WhatsApp or an external language model. Static evidence is always dated.
"""
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from .selection import _bool_series


def present(value):
    return value is not None and not pd.isna(value) and str(value).strip() != ''


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Nudge numbers must be finite')
    if 0 < abs(value) < .01:
        return '<0.01' if value > 0 else '>-0.01'
    return f'{value:,.2f}'.rstrip('0').rstrip('.')


def plain(value):
    if not present(value):
        return None
    if hasattr(value, 'item'):
        value = value.item()
    return value


def claim(kind, text, row, fields):
    return dict(kind=kind, text=text, evidence={key: plain(row.get(key)) for key in fields})


def build_nudge(visit, row, ranking_config, policy):
    """Return a bounded draft and the exact evidence for its rendered claims."""
    date = pd.Timestamp(visit['date']).normalize()
    as_of = pd.Timestamp(row['as_of']).normalize()
    if as_of >= date:
        raise ValueError('Static evidence snapshot must precede the visit')
    prefix = (f"{visit['so_id']} {visit['account_id']} | {date:%d %b} "
              f"{visit['planned_arrival']}. {as_of:%d %b} data. ")
    action = 'Ask what changed; agree next order.'
    periods = ranking_config['comparison_periods']
    if row['strong_drop_signal']:
        start,end = map(pd.Timestamp, periods['sudden_current'])
        text = (f"{start.day}-{end:%d %b} net {number(row['actual_recent_value'])} vs "
                f"{number(row['expected_recent_value'])} baseline (scaled).")
        primary = claim('recent_drop',text,row,['actual_recent_value','expected_recent_value',
                         'strong_drop_signal','sudden_support'])
        primary['comparison_window'] = periods['sudden_current']
        primary['reference_window'] = periods['sudden_reference']
    elif row['return_driven_recent_reduction']:
        primary = claim('returns_review','Returns may explain the recent net reduction.',row,
                        ['return_driven_recent_reduction','recent_gross_sale_value','recent_return_value'])
        action = 'Check returns and fresh demand before ordering.'
    elif row['recency_category'] == 'never_purchased':
        primary = claim('first_purchase','No positive sale in supplied history; check fit.',row,
                        ['recency_category','first_positive_sale_date'])
        action = 'Confirm buying need and next step.'
    elif row['recency_category'] in ['lapsed','dormant']:
        last = pd.Timestamp(row['last_positive_sale_date'])
        if pd.isna(last) or last>as_of:
            raise ValueError('Reactivation requires a valid historical last sale')
        primary = claim('reactivation',f'Last positive sale {last:%d %b %Y}; explore restarting.',row,
                        ['recency_category','last_positive_sale_date'])
        action = 'Check stock and agree a next step.'
    elif float(row['raw_weighted_value_loss']) > 0:
        primary = claim('value_loss',f"Value-loss signal {number(row['raw_weighted_value_loss'])} (scaled/month).",row,
                        ['raw_weighted_value_loss','loss_mom','loss_qoq_monthly','loss_yoy_monthly'])
    elif float(row['potential_score']) > 0:
        primary = claim('potential_check','GST band is coarse; verify buying potential.',row,
                        ['potential_score','potential_status','turnover_band_proxy','potential_confidence'])
        action = 'Ask product mix and monthly buying needs.'
    else:
        primary = claim('needs_check','Review current stock and buying needs.',row,['priority_score'])
        action = 'Agree a useful next step.'
    candidates = []
    scheme_note = 'not_applicable_or_missing'
    if row['is_club']:
        required = ['scheme_gap_points','scheme_next_slab','scheme_points_as_of',
                    'scheme_window_start','scheme_window_end','scheme_target_source']
        available = all(present(row.get(k)) for k in required)
        if available:
            start,end,snapshot = (pd.Timestamp(row[k]).normalize() for k in
                                  ['scheme_window_start','scheme_window_end','scheme_points_as_of'])
            if snapshot != as_of:
                raise ValueError('Scheme evidence must match the declared snapshot')
            valid = start<=as_of<=end and start<=date<=end and float(row['scheme_gap_points'])>0
            scheme_note = 'applicable_dated_context' if valid else 'expired_or_not_yet_applicable'
            if valid:
                assumed = str(row['scheme_target_source']).startswith('assumed_')
                label = 'Q4 target assumed' if assumed else 'target supplied'
                text = (f"Owner {row['root_dealer_id']}: {number(row['scheme_gap_points'])} pts to "
                        f"{row['scheme_next_slab']} ({label}); verify balance/offer.")
                candidates.append(claim('scheme_context',text,row,
                    ['root_dealer_id','scheme_gap_points','scheme_next_target','scheme_next_slab',
                     'scheme_net_points','scheme_kind','scheme_window_start','scheme_window_end',
                     'scheme_points_as_of','scheme_target_source']))
        else:
            candidates.append(claim('club_check','Club visit: confirm current scheme terms.',row,
                                    ['is_club','scheme_status']))
    location = 'GPS missing: confirm pin.' if not row['has_location'] else ''
    mandatory = [primary]
    if location:
        mandatory.append(claim('location_check',location,row,['has_location','lat','lon']))
    claims = list(mandatory)

    def render(items):
        return prefix+' '.join([item['text'] for item in items]+[action])

    limit = policy['max_characters_exclusive']
    if len(render(claims))>=limit:
        raise ValueError('Required nudge context exceeds the character budget')
    omitted = []
    for item in candidates:
        trial = [primary,item]+mandatory[1:]
        if len(render(trial))<limit:
            claims = trial
        else:
            omitted.append(item['kind'])
    message = render(claims)
    send_date = date-pd.Timedelta(days=1)
    evidence = dict(snapshot_date=as_of.date().isoformat(),
        data_scope='Supplied sales history only; figures scaled; no November observations.',
        sales_source='Sales_Daily',master_source='Dealers',
        claims=claims,action=action,scheme_context_status=scheme_note,
        omitted_for_length=omitted,
        caveats=['Static draft: refresh sales and scheme balances before use.',
                 'GST band does not establish exact wallet share.',
                 'Scheme points belong to the owner group, not separately to each outlet.'])
    result = dict(nudge_id=f"{date:%Y%m%d}:{visit['so_id']}:{visit['account_id']}",
        date=date.date().isoformat(),so_id=visit['so_id'],account_id=visit['account_id'],
        sequence=int(visit['sequence']),planned_arrival=visit['planned_arrival'],
        send_at=f"{send_date.date().isoformat()}T{policy['send_time_previous_evening']}+05:30",
        timezone=policy['timezone'],as_of=as_of.date().isoformat(),
        primary_topic=primary['kind'],is_club=bool(row['is_club']),
        location_review_required=not bool(row['has_location']),
        message=message,character_count=len(message))
    return result,evidence


def generate_nudges(plan,focus,ranking_config,policy):
    if policy['max_characters_exclusive']!=300 or policy['timezone']!='Asia/Kolkata':
        raise ValueError('Nudges require the assignment limit and IST timezone')
    if policy['send_time_previous_evening']!='18:30':
        raise ValueError('Supported send time is 18:30 IST on the preceding calendar day')
    if focus.dealer_id.isna().any() or focus.dealer_id.duplicated().any():
        raise ValueError('Focus IDs must be unique and present')
    required = ['date','so_id','account_id','sequence','planned_arrival']
    if not set(required)<=set(plan) or plan[required].isna().any().any():
        raise ValueError('Visit plan lacks required nudge fields')
    if plan.duplicated(['date','so_id','account_id']).any():
        raise ValueError('Duplicate dated visit')
    lookup = focus.set_index('dealer_id').to_dict('index')
    rows,evidence = [],[]
    for visit in plan.sort_values(['date','so_id','sequence']).to_dict('records'):
        if visit['account_id'] not in lookup:
            raise ValueError('Plan includes an account outside the scheduled focus set')
        row = lookup[visit['account_id']]
        if visit['so_id']!=row['so_id']:
            raise ValueError('Visit and focus SO ownership differ')
        if row['as_of']!=ranking_config['as_of'] or pd.Timestamp(visit['date']).strftime('%Y-%m')!=row['planning_month']:
            raise ValueError('Nudge dates differ from ranking snapshot or planning month')
        message,proof = build_nudge(visit,row,ranking_config,policy)
        rows.append(message)
        evidence.append(dict(**message,evidence=proof))
    return pd.DataFrame(rows),evidence


def choose_examples(rows,count):
    """Cover different SOs, commercial topics, club schemes and GPS gaps."""
    chosen = []
    def take(frame):
        remaining = frame.loc[~frame.index.isin(chosen)]
        if not remaining.empty and len(chosen)<count:
            chosen.append(remaining.index[0])
    for so in sorted(rows.so_id.unique()):
        take(rows.loc[rows.so_id.eq(so)])
    for topic in sorted(rows.primary_topic.unique()):
        take(rows.loc[rows.primary_topic.eq(topic)])
    take(rows.loc[rows.message.str.contains('pts to',regex=False)])
    take(rows.loc[rows.location_review_required])
    for i in rows.index:
        if len(chosen)<count and i not in chosen:
            chosen.append(i)
    return rows.loc[chosen]


def run_nudges(plan_path,focus_path,ranking_path,policy_path,out):
    plan_path,focus_path,out = Path(plan_path),Path(focus_path),Path(out)
    policy = json.loads(Path(policy_path).read_text())
    ranking = json.loads(Path(ranking_path).read_text())
    metadata = json.loads((plan_path.parent/'plan_summary.json').read_text())
    if ranking['as_of']!=metadata['selection_config']['as_of']:
        raise ValueError('Plan and ranking evidence snapshots differ')
    plan,focus = pd.read_csv(plan_path),pd.read_csv(focus_path)
    for column in ['is_club','has_location','strong_drop_signal','return_driven_recent_reduction']:
        focus[column] = _bool_series(focus[column],column)
    if set(plan.account_id)!=set(focus.dealer_id):
        raise ValueError('Plan and scheduled focus account sets differ')
    rows,evidence = generate_nudges(plan,focus,ranking,policy)
    if len(rows)<5 or policy['example_count']<5:
        raise ValueError('Assignment requires at least five scheduled nudges')
    examples = choose_examples(rows,policy['example_count'])
    out.mkdir(parents=True,exist_ok=True)
    rows.to_csv(out/'visit_nudges.csv',index=False)
    examples.to_csv(out/'example_nudges.csv',index=False)
    by_id = {r['nudge_id']:r for r in evidence}
    (out/'nudge_evidence.jsonl').write_text(''.join(json.dumps(r,allow_nan=False)+'\n' for r in evidence))
    lines = ['WhatsApp drafts for Sales Officers (not sent)',
             'Static October 15 snapshot; refresh observations before operational use.',
             'All sales values are scaled; scheme gaps are dated owner-group context.','']
    for r in examples.to_dict('records'):
        proof = by_id[r['nudge_id']]['evidence']
        lines += [f"{r['nudge_id']} | send {r['send_at']} | {r['character_count']} characters",
                  r['message'],'Data behind it: '+json.dumps(proof['claims'],allow_nan=False),'']
    (out/'example_nudges.txt').write_text('\n'.join(lines)+'\n')
    summary = dict(version=policy['version'],source_sha256=metadata['source_sha256'],
        input_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in [plan_path,focus_path,Path(ranking_path),Path(policy_path)]},
        messages=len(rows),example_messages=len(examples),max_characters=int(rows.character_count.max()),
        messages_by_topic={k:int(v) for k,v in rows.primary_topic.value_counts().items()},
        messages_by_so={k:int(v) for k,v in rows.so_id.value_counts().items()},
        messages_with_scheme_points=int(rows.message.str.contains('pts to',regex=False).sum()),
        messages_with_missing_gps=int(rows.location_review_required.sum()),
        draft_only=True,external_services_used=False,live_observations_available=False)
    (out/'nudge_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(f"Wrote {len(rows)} drafts and {len(examples)} examples to {out}; longest {summary['max_characters']} characters")
    return rows,evidence,summary
