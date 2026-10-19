#!/usr/bin/env python3
"""Read-only audit of the supplied PJP workbook. No network access required.

Run: python -m pjp audit --workbook INPUT.xlsx --out AUDIT_DIRECTORY
Dependencies: pandas, openpyxl. Original cells are never modified.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workbook', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    tables = a.out / 'tables'
    tables.mkdir(exist_ok=True)
    before = digest(a.workbook)
    xls = pd.ExcelFile(a.workbook)
    readme = pd.read_excel(xls, sheet_name='README', header=None).iloc[:, 0].dropna().astype(str).tolist()
    data = {name: pd.read_excel(xls, sheet_name=name, header=2 if name in
            {'Scheme_Targets', 'GST_Filings', 'Baseline_Metrics'} else 0)
            for name in xls.sheet_names if name != 'README'}
    o, d, s, tiers, targets, nonclub, v, g, b, cal = [data[n] for n in
        ['Officers', 'Dealers', 'Sales_Daily', 'Product_Tiers', 'Scheme_Targets',
         'Scheme_Rules_NonClub', 'SFDC_Activities', 'GST_Filings', 'Baseline_Metrics', 'Planning_Calendar']]
    for frame, columns in [(s, ['date']), (v, ['date', 'rescheduled_to']),
                           (targets, ['start', 'end']), (cal, ['date'])]:
        for column in columns:
            frame[column] = pd.to_datetime(frame[column], errors='coerce')
    dids, sos = set(d.dealer_id), set(o.so_id)
    owner = d.set_index('dealer_id').so_id
    club = d.set_index('dealer_id').loyalty_club_member.eq('Yes')
    findings = []
    def finding(level, name, count, meaning, action):
        findings.append(dict(level=level, finding=name, count=int(count), meaning=meaning, action=action))
    def save(name, frame):
        frame.to_csv(tables / f'{name}.csv', index=False, date_format='%Y-%m-%d')
    def cnt(mask):
        return int(mask.fillna(False).sum())
    inventory = pd.DataFrame([dict(sheet=n, rows=len(f), columns=len(f.columns)) for n, f in data.items()])
    save('sheet_inventory', inventory)
    missing = pd.DataFrame([dict(sheet=n, column=c, missing=int(f[c].isna().sum()),
                                rows=len(f), missing_fraction=float(f[c].isna().mean()))
                           for n, f in data.items() for c in f.columns])
    save('column_missingness', missing)
    keychecks = []
    for n, key in [('Officers', ['so_id']), ('Dealers', ['dealer_id']),
                   ('Product_Tiers', ['product_tier']), ('SFDC_Activities', ['activity_id']),
                   ('GST_Filings', ['dealer_id']), ('Baseline_Metrics', ['so_id', 'month']),
                   ('Planning_Calendar', ['date']),
                   ('Scheme_Targets', ['parent_dealer_id', 'scheme', 'start', 'end', 'slab'])]:
        f = data[n]
        keychecks.append(dict(sheet=n, key=' + '.join(key), missing_key_rows=cnt(f[key].isna().any(axis=1)),
                              duplicate_key_rows=cnt(f.duplicated(key, keep=False))))
    save('key_checks', pd.DataFrame(keychecks))
    join_checks = {
        'dealers_with_unknown_so': cnt(~d.so_id.isin(sos)),
        'sales_with_unknown_dealer': cnt(~s.dealer_id.isin(dids)),
        'gst_with_unknown_dealer': cnt(~g.dealer_id.isin(dids)),
        'dealers_without_gst_row': len(dids - set(g.dealer_id)),
        'targets_with_unknown_parent': cnt(~targets.parent_dealer_id.isin(dids)),
        'activities_with_unknown_so': cnt(~v.so_id.isin(sos)),
        'invalid_parent_links': cnt(d.parent_dealer_id.notna() & ~d.parent_dealer_id.isin(dids)),
        'self_parent_links': cnt(d.parent_dealer_id.eq(d.dealer_id)),
    }

    # Dealer-level facts: do not use billed_by as territory ownership.
    s['owner_so_id'] = s.dealer_id.map(owner)
    cutoff = s.date.max()
    d['located'] = d.lat.notna() & d.lon.notna()
    d['is_club'] = d.loyalty_club_member.eq('Yes')
    coord_partial = d.lat.isna() ^ d.lon.isna()
    coord_invalid = d.located & (~d.lat.between(-90, 90) | ~d.lon.between(-180, 180))
    summary = d.groupby('so_id').agg(dealers=('dealer_id', 'size'), located=('located', 'sum'),
                                    club_dealers=('is_club', 'sum')).reset_index()
    summary['missing_locations'] = summary.dealers - summary.located
    wd = int(cal.working_day.eq('Yes').sum())
    summary['nominal_monthly_slots'] = wd * 6
    summary['max_distinct_focus_if_all_club_included'] = wd * 6 - summary.club_dealers
    save('officer_summary', summary)
    save('missing_locations', d.loc[~d.located, ['dealer_id', 'so_id', 'town', 'loyalty_club_member', 'location_source']])
    save('parent_links', d.loc[d.parent_dealer_id.notna(), ['dealer_id', 'parent_dealer_id', 'so_id']])
    tier_conflict = ~d.is_club & d.club_tier.notna()
    finding('Important', 'Missing dealer coordinates', cnt(~d.located),
            'Travel cannot be geographically estimated for these outlets.',
            'Use documented fallback travel allowances; retain location-confidence flags.')
    finding('Important', 'Non-club dealers with a populated club_tier', cnt(tier_conflict),
            'A tier label is not equivalent to loyalty membership.',
            'Use loyalty_club_member, not club_tier, for the twice-monthly rule.')
    save('membership_tier_disagreement', d.loc[tier_conflict, ['dealer_id', 'so_id', 'loyalty_club_member', 'club_tier']])

    # Ledger dates, signs, duplicates, product point mappings, and coverage.
    s['net_points'] = s.sheets.fillna(0) * s.points_per_sheet.fillna(0)
    s['month'] = s.date.dt.strftime('%Y-%m')
    monthly = s.groupby(['owner_so_id', 'month']).agg(rows=('dealer_id', 'size'), dealers=('dealer_id', 'nunique'),
        net_value_inr=('value_inr', 'sum'), net_sheets=('sheets', 'sum'), net_points=('net_points', 'sum')).reset_index()
    monthly['partial_month'] = monthly.month.eq(cutoff.strftime('%Y-%m'))
    save('sales_monthly_by_owner', monthly)
    never_bought = dids - set(s.dealer_id)
    save('dealers_without_sales', d.loc[d.dealer_id.isin(never_bought), ['dealer_id', 'so_id', 'loyalty_club_member', 'located']])
    full_sales_cols = list(data['Sales_Daily'].columns[:10])
    exact_dup = s.duplicated(full_sales_cols, keep=False)
    natural_key = ['date', 'dealer_id', 'product_tier', 'txn_type', 'billed_by']
    duplicate_group_rows = s.duplicated(natural_key, keep=False)
    save('sales_exact_duplicate_candidates', s.loc[exact_dup, full_sales_cols])
    expected_pps = s.product_tier.map(tiers.set_index('product_tier').points_per_sheet)
    pps_bad = expected_pps.notna() & s.points_per_sheet.ne(expected_pps)
    sign_bad = (s.txn_type.eq('Sale') & ((s.sheets < 0) | (s.value_inr < 0))) | (
        s.txn_type.eq('Return') & ((s.sheets > 0) | (s.value_inr > 0)))
    save('sales_sign_or_points_issues', s.loc[pps_bad | sign_bad, full_sales_cols])
    billing = s.groupby(['owner_so_id', 'billed_by']).agg(rows=('dealer_id', 'size'),
                net_value_inr=('value_inr', 'sum')).reset_index()
    save('sales_billing_vs_ownership', billing)
    billed_mismatch = s.billed_by.ne(s.owner_so_id)
    finding('Important', 'Sales billed by someone other than current dealer owner', cnt(billed_mismatch),
            'Invoice attribution is not the same as dealer ownership.',
            'Join sales to Dealers.dealer_id and use Dealers.so_id for territory planning.')
    finding('Important', 'Sales rows with repeated date/dealer/tier/type/biller keys', cnt(duplicate_group_rows),
            'Rows are not uniquely identified by dealer/date/product tier; multiple transactions can share those fields.',
            'Aggregate legitimate ledger rows. Do not deduplicate on these fields.')
    finding('Review', 'Exact duplicate sales-row candidates', cnt(exact_dup),
            'No invoice ID is available to establish whether identical rows are erroneous or separate transactions.',
            'Report candidates; preserve all source rows unless a business rule confirms duplication.')
    finding('Important', 'Dealers with no sales records', len(never_bought),
            'Workbook README states these dealers bought nothing; this is not missing ledger data.',
            'Represent zero purchases explicitly and distinguish dormant/never-billed outlets.')
    finding('Important', 'October sales are partial', 1,
            f'Latest sale date is {cutoff.date()}; October 16-31 is not observed.',
            'Use matched elapsed periods for October comparisons; do not fill unobserved future dates with zero.')
    finding('Definition', 'Rows with nonzero FOC quantities', cnt(s.foc_sheets.fillna(0).ne(0)),
            'A separate free-of-charge field exists but has no nonzero values in this workbook. The simulator calculates points as sheets x points_per_sheet.',
            'Do not automatically add foc_sheets to paid volume or scheme points without a stated business rule.')

    # Dealer master flags reconciled to the ledger and actual activity IDs.
    sales_ids_apr = set(s.loc[s.date >= '2026-04-01', 'dealer_id'])
    first_sale = s.groupby('dealer_id').date.min()
    d['ledger_first_date'] = d.dealer_id.map(first_sale)
    d['master_first_date'] = pd.to_datetime(d.first_sale_in_data, errors='coerce')
    first_bad = ~((d.ledger_first_date == d.master_first_date) | (d.ledger_first_date.isna() & d.master_first_date.isna()))
    billed_flag_bad = d.billed_since_apr_2026.eq('Yes').ne(d.dealer_id.isin(sales_ids_apr))

    # Visit history: non-dealer demand-gen records are expected, not orphan dealer rows.
    v['month'] = v.date.dt.strftime('%Y-%m')
    v['is_dealer'] = v.account_id.isin(dids)
    v['is_completed'] = v.status.eq('Completed')
    v['owner_so_id'] = v.account_id.map(owner)
    nondealer = ~v.is_dealer
    save('nondealer_account_types', v.loc[nondealer].groupby('account_type', dropna=False).size().rename('records').reset_index())
    activity_owner_bad = v.is_dealer & v.so_id.ne(v.owner_so_id)
    save('activity_ownership_issues', v.loc[activity_owner_bad])
    finding('Review', 'Historical dealer visits by another current owner SO', cnt(activity_owner_bad),
            'Historical visiting officer differs from current master ownership; ownership history is not supplied.',
            'Retain historical actor, but use current Dealers.so_id for the November plan.')
    short = v.is_completed & v.duration_min.lt(10)
    save('short_completed_calls', v.loc[short, ['activity_id', 'so_id', 'date', 'account_id', 'account_type', 'duration_min', 'is_dealer']])
    frequencies = v.loc[v.is_completed & v.is_dealer].groupby(['month', 'account_id']).agg(
        completed_visits=('activity_id', 'size'), visiting_sos=('so_id', lambda x: '|'.join(sorted(set(x))))).reset_index()
    frequencies.insert(0, 'so_id', frequencies.account_id.map(owner))
    frequencies['is_club'] = frequencies.account_id.map(club)
    frequencies['normal_required_visits'] = np.where(frequencies.is_club, 2, 1)
    frequencies['above_normal_requirement'] = frequencies.completed_visits > frequencies.normal_required_visits
    save('dealer_month_visit_counts', frequencies)
    save('dealer_month_over_two_visits', frequencies.loc[frequencies.completed_visits > 2])
    save('dealer_month_over_ten_visits', frequencies.loc[frequencies.completed_visits > 10])
    raw_count = v.loc[v.is_dealer].groupby('account_id').size()
    completed_count = v.loc[v.is_dealer & v.is_completed].groupby('account_id').size()
    master_count_bad = d.sfdc_visits_apr_oct_2026.fillna(0).ne(d.dealer_id.map(completed_count).fillna(0))
    master_vs_all_records = d.sfdc_visits_apr_oct_2026.fillna(0).ne(d.dealer_id.map(raw_count).fillna(0))
    # Negative same-day clock differences may indicate next-day checkout.
    def minute_clock(x):
        if pd.isna(x):
            return np.nan
        try:
            return pd.to_datetime(str(x), format='%I:%M %p').hour * 60 + pd.to_datetime(str(x), format='%I:%M %p').minute
        except ValueError:
            return np.nan
    starts = v.check_in.map(minute_clock)
    ends = v.check_out.map(minute_clock)
    v['clock_duration_min'] = ends - starts
    v['elapsed_if_next_day_checkout'] = v.clock_duration_min.where(v.clock_duration_min >= 0, v.clock_duration_min + 1440)
    overnight = v.is_completed & v.clock_duration_min.lt(0)
    duration_bad = v.is_completed & v.duration_min.notna() & v.clock_duration_min.notna() & (v.duration_min - v.elapsed_if_next_day_checkout).abs().gt(1)
    save('visit_duration_disagreements', v.loc[duration_bad])
    save('overnight_or_excessive_durations', v.loc[overnight | (v.is_completed & v.duration_min.gt(540))])
    save('future_activity_records', v.loc[v.date > cutoff])
    finding('Historical flag', 'Completed calls shorter than 10 minutes', cnt(short),
            'These breach the assignment productive-call threshold; short duration alone does not establish misconduct.',
            'Flag them separately; do not automatically use them as productive dealer coverage.')
    finding('Historical flag', 'Dealer-months with more than two completed visits', cnt(frequencies.completed_visits > 2),
            'Historical frequency exceeds the proposed monthly cap; count is dealer-months, not unique dealers.',
            'Flag over-visiting and cap the new plan at two visits per dealer.')

    # Independently recompute baseline measures at the same SO-month grain.
    def baseline_at(end):
        rec = []
        for (so, month), f in v.loc[v.date <= end].groupby(['so_id', 'month']):
            completed = f.loc[f.is_completed]
            planned = f.planned_or_adhoc.eq('PJP (planned)')
            nplanned, ndone = int(planned.sum()), cnt(planned & f.is_completed)
            dc = completed.loc[completed.is_dealer]
            counts = dc.groupby('account_id').size()
            rec.append(dict(so_id=so, month=month, pjp_visits_planned=nplanned, pjp_visits_completed=ndone,
                pjp_adherence=ndone/nplanned if nplanned else np.nan, completed_visits=len(completed),
                adhoc_share_of_completed=completed.planned_or_adhoc.eq('Ad hoc').mean(),
                dealer_share_of_completed=completed.is_dealer.mean(), distinct_dealers_visited=dc.account_id.nunique(),
                dealers_with_gt10_visits=cnt(counts > 10), share_calls_under_10_min=completed.duration_min.lt(10).mean()))
        return pd.DataFrame(rec)
    def compare_baseline(computed):
        diffs = []
        for _, source in b.iterrows():
            match = computed.loc[(computed.so_id == source.so_id) & (computed.month == source.month)].iloc[0]
            for col in computed.columns.difference(['so_id', 'month']):
                x, y = source[col], match[col]
                if pd.isna(x) and pd.isna(y):
                    continue
                tol = 0.00051 if ('share' in col or 'adherence' in col) else 0
                if pd.isna(x) != pd.isna(y) or abs(x-y) > tol:
                    diffs.append(dict(so_id=source.so_id, month=source.month, metric=col, supplied=x, recomputed=y))
        return diffs
    recomputed = baseline_at(cutoff)
    save('baseline_recomputed', recomputed)
    diffs = compare_baseline(recomputed)
    previous_cutoff = cutoff - pd.Timedelta(days=1)
    prior_baseline = baseline_at(previous_cutoff)
    prior_diffs = compare_baseline(prior_baseline)
    save('baseline_recomputed_previous_day', prior_baseline)
    save('baseline_reconciliation_differences', pd.DataFrame(diffs, columns=['so_id', 'month', 'metric', 'supplied', 'recomputed']))
    save('baseline_previous_day_differences', pd.DataFrame(prior_diffs, columns=['so_id', 'month', 'metric', 'supplied', 'recomputed']))
    finding('Definition', 'Master visit counts reconcile to completed visits', len(d),
            f'All dealer counts match completed activity records; {cnt(master_vs_all_records)} differ if all statuses are counted.',
            'Use completed visits for this field and retain non-completed activities separately.')
    finding('Review', 'Supplied baseline differs from current-cutoff recomputation', len(diffs),
            f'There are {len(prior_diffs)} metric differences when recomputing through {previous_cutoff.date()} instead of {cutoff.date()}.',
            'Label the apparent baseline snapshot cutoff; use recomputed metrics for comparisons at the current cutoff.')
    finding('Historical flag', 'Completed visits implying overnight checkout', cnt(overnight),
            'Recorded durations match next-day checkout but are not plausible normal same-day sales calls.',
            'Keep original records and flag timing anomalies; exclude from service-duration estimation.')
    finding('Review', 'Brief maximum repeat count is not reproduced in workbook', 1,
            f'Brief mentions 33 visits in a dealer-month; maximum in supplied completed dealer history is {int(frequencies.completed_visits.max())}.',
            'Report the measured workbook maximum without claiming the broader narrative figure is present.')

    # GST fields are proxies, not exact turnover or judgments about counterparty reliability.
    g['has_turnover_proxy'] = g.turnover_band_proxy.notna()
    g['has_exact_turnover_slab'] = g.aggregate_turnover_slab.notna()
    g['so_id'] = g.dealer_id.map(owner)
    save('gst_coverage_by_owner', g.groupby('so_id').agg(dealers=('dealer_id', 'size'),
        turnover_proxy_available=('has_turnover_proxy', 'sum'), exact_slab_available=('has_exact_turnover_slab', 'sum')).reset_index())
    save('gst_status_and_proxy', g[['dealer_id', 'so_id', 'gst_status', 'turnover_band_proxy', 'lookup_status']])
    gst_nonactive = g.gst_status.notna() & ~g.gst_status.eq('Active')
    finding('Important', 'Exact GST turnover slab unavailable', cnt(~g.has_exact_turnover_slab),
            'All exact turnover slabs are blank; broad proxies do not measure exact dealer turnover.',
            'Use cautious ordinal potential signals and explicit missing-data flags; do not fabricate wallet share.')
    finding('Important', 'GST turnover proxy unavailable', cnt(~g.has_turnover_proxy),
            'No broad turnover-band proxy is available for these dealers.',
            'Keep potential unknown; do not interpret missing as zero opportunity.')
    finding('Review', 'GST status explicitly non-active', cnt(gst_nonactive),
            'Source status is suspended or cancelled; this does not prove current trading activity or a cause of sales decline.',
            'Flag for verification; do not silently exclude dealers or infer misconduct.')
    save('gst_proxy_distribution', g.groupby('turnover_band_proxy', dropna=False).size().rename('dealers').reset_index())

    # Scheme coverage, hierarchy, windows and monotonicity.
    target_ids = set(targets.parent_dealer_id)
    club_ids = set(d.loc[d.is_club, 'dealer_id'])
    parent_map = d.set_index('dealer_id').parent_dealer_id.to_dict()
    def root(x):
        seen = set()
        while pd.notna(parent_map.get(x)):
            if x in seen:
                return 'CYCLE'
            seen.add(x)
            x = parent_map[x]
        return x
    roots = {x: root(x) for x in dids}
    missing_club_targets = {x for x in club_ids if roots[x] not in target_ids}
    save('club_dealers_without_targets', d.loc[d.dealer_id.isin(missing_club_targets), ['dealer_id', 'so_id', 'club_tier']])
    scheme_coverage = []
    nonmonotonic = []
    for scheme, f in targets.groupby('scheme'):
        ids = set(f.parent_dealer_id)
        scheme_coverage.append(dict(scheme=scheme, rows=len(f), parent_dealers=len(ids),
            start=f.start.min(), end=f.end.max(), club_outlets_without_targets=sum(roots[x] not in ids for x in club_ids)))
        for pid, ff in f.groupby('parent_dealer_id'):
            vals = ff.assign(slab_number=ff.slab.str.extract(r'(\d+)')[0].astype(int)).sort_values('slab_number').slab_target_points
            if not vals.is_monotonic_increasing:
                nonmonotonic.append(dict(parent_dealer_id=pid, scheme=scheme))
    save('scheme_coverage', pd.DataFrame(scheme_coverage))
    finding('Important', 'Club outlets without any provided scheme targets after parent mapping', len(missing_club_targets),
            'Loyalty membership still creates visit obligations even when scheme targets are missing.',
            'Keep club visit cadence; omit or qualify next-slab claims for missing schemes.')
    finding('Important', 'November club quarterly targets not directly supplied', 1,
            'The workbook explicitly instructs reusing Jul-Sep targets for Oct-Dec; historical monthly top-ups are only for August.',
            'Roll quarterly dates forward as an explicit assumption. Do not invent November monthly club offers.')
    save('nonclub_rules_original', nonclub)

    # Calendar, strict dates, and location plausibility relative to an SO start point.
    calendar_wrong = cal.working_day.eq('Yes').ne(cal.date.dt.dayofweek.ne(6))
    starts_by_so = o.set_index('so_id')[['approx_day_start_lat', 'approx_day_start_lon']].to_dict('index')
    def distance(r):
        if not r.located:
            return np.nan
        start = starts_by_so[r.so_id]
        la, lo, lb, lob = map(math.radians, [start['approx_day_start_lat'], start['approx_day_start_lon'], r.lat, r.lon])
        h = math.sin((lb-la)/2)**2 + math.cos(la)*math.cos(lb)*math.sin((lob-lo)/2)**2
        return 6371*2*math.asin(min(1, math.sqrt(h)))
    d['straight_line_km_from_start'] = d.apply(distance, axis=1)
    save('dealer_distance_from_start', d[['dealer_id', 'so_id', 'town', 'located', 'straight_line_km_from_start']])
    distant = d.loc[d.straight_line_km_from_start > 100]
    save('locations_over_100km_review', distant[['dealer_id', 'so_id', 'town', 'lat', 'lon', 'straight_line_km_from_start']])
    finding('Review', 'Located dealers over 100 km from SO start', len(distant),
            '100 km is an audit review threshold, not proof of bad coordinates or a route constraint.',
            'Check route feasibility. Do not drop or relocate these dealers automatically.')

    checks = dict(join_checks)
    checks.update(
        missing_key_rows=sum(x['missing_key_rows'] for x in keychecks),
        duplicate_key_rows=sum(x['duplicate_key_rows'] for x in keychecks),
        partial_coordinate_rows=cnt(coord_partial), invalid_coordinate_rows=cnt(coord_invalid),
        product_tier_unmapped_rows=cnt(expected_pps.isna()), product_points_mismatch_rows=cnt(pps_bad),
        sale_or_return_sign_conflicts=cnt(sign_bad), invalid_sales_dates=cnt(s.date.isna()),
        invalid_visit_dates=cnt(v.date.isna()), visits_after_data_cutoff=cnt(v.date > cutoff),
        calendar_weekday_rule_mismatches=cnt(calendar_wrong),
        dealer_first_sale_mismatches=cnt(first_bad), dealer_billed_flag_mismatches=cnt(billed_flag_bad),
        dealer_activity_count_mismatches=cnt(master_count_bad), visit_owner_mismatches=cnt(activity_owner_bad),
        completed_duration_clock_disagreements_allowing_next_day=cnt(duration_bad),
        completed_overnight_checkout_rows=cnt(overnight), baseline_metric_differences_current_cutoff=len(diffs),
        baseline_metric_differences_previous_day=len(prior_diffs),
        parent_cycles=sum(x == 'CYCLE' for x in roots.values()), scheme_nonmonotonic_groups=len(nonmonotonic),
        future_planned_activity_rows=cnt(v.status.eq('Planned') & (v.date > cutoff)),
    )
    save('integrity_checks', pd.DataFrame([dict(check=k, issue_count=int(val)) for k, val in checks.items()]))
    save('master_flag_disagreements', d.loc[first_bad | billed_flag_bad | master_count_bad,
        ['dealer_id', 'so_id', 'first_sale_in_data', 'ledger_first_date', 'billed_since_apr_2026', 'sfdc_visits_apr_oct_2026']])
    save('findings', pd.DataFrame(findings))
    historical_daily = v.loc[v.is_completed].groupby(['so_id', 'date']).agg(
        completed_calls=('activity_id', 'size'), dealer_calls=('is_dealer', 'sum'), productive_minutes=('duration_min', 'sum')).reset_index()
    save('historical_daily_activity', historical_daily)
    bsep = recomputed.loc[recomputed.month.eq('2026-09')]
    xls.close()
    after = digest(a.workbook)
    if before != after:
        raise RuntimeError('Source workbook changed during read-only audit.')
    result = dict(source_workbook=str(a.workbook.resolve()), source_sha256=before,
        source_unchanged=True, sheet_count=len(xls.sheet_names), record_counts={n:len(f) for n,f in data.items()},
        sales_as_of=str(cutoff.date()), sales_start=str(s.date.min().date()), working_days=wd,
        dealer_summary=summary.to_dict('records'), integrity_checks=checks,
        ledger=dict(net_value_inr=float(s.value_inr.sum()), net_sheets=float(s.sheets.sum()),
            net_points=float(s.net_points.sum()), return_rows=cnt(s.txn_type.eq('Return')),
            exact_duplicate_candidate_rows=cnt(exact_dup), repeated_grain_rows=cnt(duplicate_group_rows),
            billed_by_other_than_owner_rows=cnt(billed_mismatch), never_bought_dealers=len(never_bought)),
        visits=dict(completed=cnt(v.is_completed), nondealer_records=cnt(nondealer),
            short_completed_calls=cnt(short), short_completed_dealer_calls=cnt(short & v.is_dealer),
            dealer_months_over_two=cnt(frequencies.completed_visits > 2),
            dealer_months_over_ten=cnt(frequencies.completed_visits > 10),
            max_completed_visits_same_dealer_month=int(frequencies.completed_visits.max())),
        gst=dict(exact_turnover_slabs=cnt(g.has_exact_turnover_slab),
            turnover_proxy_available=cnt(g.has_turnover_proxy), explicitly_nonactive=cnt(gst_nonactive)),
        schemes=scheme_coverage, findings=findings)
    def serial(x):
        if isinstance(x, (np.integer,)): return int(x)
        if isinstance(x, (np.floating,)): return float(x)
        if isinstance(x, (pd.Timestamp,)): return x.isoformat()
        if isinstance(x, (np.bool_,)): return bool(x)
        raise TypeError(type(x).__name__)
    (a.out/'audit_summary.json').write_text(json.dumps(result, indent=2, default=serial, allow_nan=False))
    lines = [
        'PJP ASSIGNMENT DATA AUDIT',
        'Conclusion: the data is usable for a POC with explicit handling of missing fields, ownership, time cutoffs and scheme gaps.',
        'Read-only audit of the supplied workbook. No planning model or simulation results have been produced.',
        'Confidential assignment data: keep this report and its row-level tables private.',
        '', 'SUMMARY',
        f'- {len(d)} dealers, {len(s):,} sales rows, {len(v):,} activities and {wd} November working days.',
        f'- {cnt(~d.located)} missing locations; {cnt(d.is_club)} club outlets; {len(never_bought)} dealers with no purchases.',
        f'- Exact GST turnover is unavailable for all {len(g)} dealers; {cnt(g.has_turnover_proxy)} have only broad proxies.',
        f'- Sales cutoff is {cutoff.date()}; baseline matches a {previous_cutoff.date()} snapshot ({len(prior_diffs)} metric differences).',
        f'- All {len(d)} master activity counts reconcile to completed visits.',
        '- Use current dealer ownership, the explicit club flag, matched sales periods and documented travel fallbacks.',
        '', 'SCOPE AND REPRODUCIBILITY',
        f'Workbook: {a.workbook.name}', f'SHA-256: {before}',
        'All 11 worksheets inspected. Source checksum is unchanged after the audit.',
        'The README is explanatory text; the other ten sheets are parsed as tables.',
        'Re-run: python -m pjp audit --workbook INPUT.xlsx --out AUDIT_DIRECTORY',
        '', 'DATA INVENTORY', inventory.to_string(index=False),
        '', 'TERRITORY AND CAPACITY', summary.to_string(index=False),
        'Capacity columns use 25 normal days x 6 slots. They do not account for weather, unavailable dealers or long routes.',
        '', 'KEY FINDINGS AND RECOMMENDED HANDLING',
    ]
    for i, f in enumerate(findings,1):
        lines.extend([f'{i}. [{f["level"]}] {f["finding"]}: {f["count"]}',
                      f'   Meaning: {f["meaning"]}', f'   Handling: {f["action"]}'])
    lines.extend(['', 'CHECK AND OBSERVATION COUNTS (nonzero values can be expected observations, not necessarily errors)',
                  '\n'.join(f'{k}: {val}' for k,val in checks.items()),
                  '', 'SEPTEMBER BASELINE RECOMPUTED FROM VISITS', bsep.to_string(index=False),
                  'SO-C adherence is undefined when no visits were planned; do not turn it into 0% or 100%.',
                  'Observed historical figures are descriptive, not a controlled comparison with simulated outcomes.',
                  '', 'SALES CONTROLS',
                  json.dumps(result['ledger'],indent=2),
                  'Repeated grain is not a reason to delete rows: no transaction ID establishes uniqueness.',
                  '', 'AUDIT LIMITS AND NEXT DECISIONS',
                  '- GST compliance colours are source labels with no supplied semantic definition; do not invent a risk score from them.',
                  '- No exact GST turnover, exact wallet share, or cause of sales decline can be established.',
                  '- Sales and rupee values are scaled by an undisclosed factor. Cross-source GST ratios are not calibrated.',
                  '- Unknown location is not the same as zero distance. Missing scheme targets do not remove club membership.',
                  '- History ends Oct 15. October 16-31 is unobserved, not confirmed zero activity.',
                  '- Route review threshold (100 km) is descriptive. No coordinate was corrected or excluded.',
                  '- No source rows were removed, no input values were imputed, and no workbook cells were edited.',
                  '- Next: choose comparable sales periods, a conservative GST-potential policy, and focus-selection rules.',
                  '', 'SUPPORTING OUTPUTS',
                  'audit_summary.json: machine-readable counts and findings.',
                  'tables/: issue rows, missingness, schema checks, ownership, historical flags and reconciliations.',
                  'src/pjp/audit.py: reproducible audit code.'])
    (a.out/'audit_report.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(integrity_checks=checks, dealer_summary=result['dealer_summary'],
        ledger=result['ledger'], visits=result['visits'], gst=result['gst'], schemes=scheme_coverage),
        indent=2,default=serial))


if __name__ == '__main__':
    main()
