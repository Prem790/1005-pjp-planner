"""Explainable initial dealer ranking; no focus selection or scheduling."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import RankingInputs, load_inputs, prepare_inputs, resolve_roots


COMPONENTS = {'value_loss': 'value_loss_score', 'sudden_drop': 'sudden_drop_score',
              'potential_proxy': 'potential_score', 'scheme_opportunity': 'scheme_score'}
PERIODS = ['mom_reference', 'mom_current', 'qoq_reference', 'qoq_current',
           'yoy_reference', 'yoy_current', 'sudden_reference', 'sudden_current',
           'potential_recent_90_days']


def validate_config(config):
    for key, names in [('component_weights', set(COMPONENTS)),
                       ('value_loss_weights', {'mom', 'qoq', 'yoy'})]:
        weights = config[key]
        if set(weights) != names or any(not np.isfinite(x) or x < 0 for x in weights.values()):
            raise ValueError(f'Invalid {key}')
        if not np.isclose(sum(weights.values()), 1, atol=1e-12, rtol=0):
            raise ValueError(f'{key} must sum to one')
    as_of = pd.Timestamp(config['as_of'])
    if pd.Period(config['planning_month'], freq='M').start_time <= as_of:
        raise ValueError('Initial planning month must start after the observation cutoff')
    for name in PERIODS:
        start, end = map(pd.Timestamp, config['comparison_periods'][name])
        if start > end or end > as_of:
            raise ValueError(f'Invalid or future comparison period: {name}')
    for name in ['mom_reference', 'mom_current']:
        start, end = map(pd.Timestamp, config['comparison_periods'][name])
        month = start.to_period('M')
        if start != month.start_time or end != month.end_time.normalize():
            raise ValueError(f'{name} must contain a complete calendar month')
    for name in ['qoq_reference', 'qoq_current', 'yoy_reference', 'yoy_current']:
        start, end = map(pd.Timestamp, config['comparison_periods'][name])
        quarter = start.to_period('Q')
        if start != quarter.start_time or end != quarter.end_time.normalize():
            raise ValueError(f'{name} must contain a complete calendar quarter')
    if config['comparison_periods']['yoy_current'] != config['comparison_periods']['qoq_current']:
        raise ValueError('YoY and QoQ must compare the same current quarter')
    last_month = as_of.to_period('M')
    if as_of.normalize() != last_month.end_time.normalize():
        last_month -= 1
    current_month = pd.Timestamp(config['comparison_periods']['mom_current'][0]).to_period('M')
    previous_month = pd.Timestamp(config['comparison_periods']['mom_reference'][0]).to_period('M')
    if current_month != last_month or previous_month != current_month-1:
        raise ValueError('MoM requires the latest complete month and its preceding month')
    current_quarter = pd.Timestamp(config['comparison_periods']['qoq_current'][0]).to_period('Q')
    last_quarter = as_of.to_period('Q')
    if as_of.normalize() != last_quarter.end_time.normalize():
        last_quarter -= 1
    previous_quarter = pd.Timestamp(config['comparison_periods']['qoq_reference'][0]).to_period('Q')
    year_quarter = pd.Timestamp(config['comparison_periods']['yoy_reference'][0]).to_period('Q')
    if current_quarter != last_quarter or previous_quarter != current_quarter-1 or year_quarter != current_quarter-4:
        raise ValueError('Quarter comparisons must use latest complete, previous, and same-prior-year quarters')
    for name, count in [('sudden_reference', config['sudden_drop']['reference_calendar_days']),
                        ('sudden_current', config['sudden_drop']['recent_calendar_days']),
                        ('potential_recent_90_days', config['scheme']['rate_reference_days'])]:
        start, end = map(pd.Timestamp, config['comparison_periods'][name])
        if (end-start).days + 1 != count:
            raise ValueError(f'{name} length disagrees with configured days')
    ref_start, ref_end = map(pd.Timestamp, config['comparison_periods']['sudden_reference'])
    cur_start, cur_end = map(pd.Timestamp, config['comparison_periods']['sudden_current'])
    if ref_end + pd.Timedelta(days=1) != cur_start or cur_end != as_of:
        raise ValueError('Sudden-drop windows must be adjacent and end at the cutoff')
    if config['sudden_drop']['reference_calendar_days'] != 7*config['sudden_drop']['reference_weeks']:
        raise ValueError('Reference weeks must partition the reference window')
    if not 0 < config['normalization']['quantile'] <= 1:
        raise ValueError('Normalization quantile must be in (0,1]')
    if config['normalization']['quantile_interpolation'] != 'linear':
        raise ValueError('Only linear quantile interpolation is supported')
    if config['normalization']['method'] != 'cap_at_positive_values_95th_percentile_within_current_owner_so':
        raise ValueError('Unsupported normalization method')
    for name in ['full_support_positive_sale_days', 'full_support_active_reference_weeks']:
        if config['sudden_drop'][name] <= 0:
            raise ValueError('Support thresholds must be positive')
    if not 0 <= config['sudden_drop']['strong_signal_drop_fraction'] <= 1:
        raise ValueError('Invalid drop threshold')
    if config['potential']['minimum_same_so_same_proxy_peer_roots'] < 1:
        raise ValueError('Peer threshold must be positive')
    for flag in ['exact_gst_wallet_share_supported', 'renormalize_weights_for_missing_components',
                 'turnover_band_midpoint_imputation']:
        if config['potential'][flag]:
            raise ValueError(f'Unsupported potential policy: {flag}')
    if not config['potential']['require_positive_net_reference_purchases_for_peer_pool']:
        raise ValueError('Peers must have positive net reference purchases')
    if config['potential']['missing_or_insufficient_evidence_component_score'] != 0:
        raise ValueError('Missing evidence cannot receive a potential bonus')
    if config['scheme']['use_expired_August_club_monthly_targets_for_November']:
        raise ValueError('Expired August offers cannot be used for November')
    if config['scheme']['quarterly_target_assumption'] != 'reuse_Jul_Sep_targets_for_Oct_Dec_as_instructed':
        raise ValueError('Unsupported quarterly target policy')
    if config['scheme']['initial_nonclub_november_scheme_bonus'] != 0:
        raise ValueError('Unobserved November non-club scheme progress cannot receive a bonus')
    if config['scheme']['estimated_opportunity_window_days'] <= 0:
        raise ValueError('Scheme opportunity horizon must be positive')
    if config['recency']['active_max_days_since_positive_sale'] > config['recency']['lapsed_max_days_since_positive_sale']:
        raise ValueError('Recency boundaries are reversed')
    expected_order = ['priority_score_desc', 'raw_weighted_value_loss_desc',
                      'recent_90_day_net_value_desc', 'dealer_id_asc']
    if config['rank_order'] != expected_order:
        raise ValueError('Unsupported ranking tie-break policy')
    limits = config['selection_not_ranking_constraints']
    if limits['club_visits'] != 2 or limits['nonclub_normal_visits'] != 1:
        raise ValueError('Visit requirements must match the assignment')
    if config['normalization']['zero_input_score'] != 0 or config['normalization']['all_zero_group_score'] != 0:
        raise ValueError('Zero evidence must produce a zero component score')
    return as_of


def positive_loss(reference, current, months=1):
    """Loss in value, not percentage. A nonpositive baseline supports no business-loss claim."""
    reference, current = np.asarray(reference), np.asarray(current)
    return np.where(reference > 0, np.maximum(reference-current, 0)/months, 0)


def normalize_by_so(values: pd.Series, owners: pd.Series, quantile=0.95):
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('Raw ranking magnitudes must be finite and nonnegative')
    scales = values.groupby(owners).transform(
        lambda group: group[group > 0].quantile(quantile, interpolation='linear') if (group > 0).any() else 0.0)
    scores = (100*values/scales.where(scales > 0)).fillna(0).clip(0, 100)
    return scores, scales


def sudden_features(expected, actual, positive_sale_days, active_weeks, config):
    support = (positive_sale_days/config['full_support_positive_sale_days']).clip(upper=1) * (
        active_weeks/config['full_support_active_reference_weeks']).clip(upper=1)
    loss = (expected-actual).clip(lower=0).where(expected > 0, 0)
    strong = (expected > 0) & (actual <= expected*(1-config['strong_signal_drop_fraction'])) & (support >= 1)
    return support, loss*support, strong


def _totals(sales, start, end, key='dealer_id'):
    part = sales.loc[sales.date.between(pd.Timestamp(start), pd.Timestamp(end))]
    return part.groupby(key)[['value_inr', 'sheets', 'points', 'gross_sale_value', 'return_value']].sum()


def _owner_context(dealers, sales, gst, config, as_of):
    root_ids = sorted(set(dealers.root_dealer_id))
    roots = dealers.set_index('dealer_id').loc[root_ids, ['so_id']].copy()
    for name in ['turnover_band_proxy', 'gst_status']:
        roots[name] = gst.set_index('dealer_id')[name].reindex(roots.index)
    period = config['comparison_periods']['potential_recent_90_days']
    totals = _totals(sales, *period, key='root_dealer_id').reindex(roots.index).fillna(0)
    roots['root_recent_net_value'] = totals.value_inr
    roots['root_recent_net_points'] = totals.points
    roots['root_monthly_purchases'] = totals.value_inr / (config['scheme']['rate_reference_days']/30)
    dates = sales.loc[sales.positive_purchase].groupby('root_dealer_id').date.max()
    roots['root_last_positive_sale'] = dates.reindex(roots.index)
    ages = (as_of-roots.root_last_positive_sale).dt.days
    recent = ages.le(config['recency']['lapsed_max_days_since_positive_sale'])
    proxy = roots.turnover_band_proxy.notna() & roots.turnover_band_proxy.astype(str).str.strip().ne('')
    pool = roots.loc[proxy & roots.root_recent_net_value.gt(0)]
    stats = pool.groupby(['so_id', 'turnover_band_proxy']).root_monthly_purchases.agg(['size', 'median'])
    keys = pd.MultiIndex.from_frame(roots[['so_id', 'turnover_band_proxy']])
    roots['potential_peer_count'] = stats['size'].reindex(keys).fillna(0).to_numpy(dtype=int)
    roots['potential_peer_median_monthly_value'] = stats['median'].reindex(keys).to_numpy()
    supported = proxy & recent & roots.potential_peer_count.ge(config['potential']['minimum_same_so_same_proxy_peer_roots'])
    median = roots.potential_peer_median_monthly_value
    roots['potential_score'] = (100*(1-roots.root_monthly_purchases.clip(lower=0)/median)).clip(0,100).where(supported,0)
    roots['potential_status'] = np.select(
        [~proxy, ~recent, ~supported],
        ['unknown_no_gst_proxy', 'no_recent_purchase', 'unknown_insufficient_peers'],
        default='coarse_peer_context_available')
    roots['potential_confidence'] = np.where(supported, 'low', 'unavailable')
    return roots


def _scheme_context(sales, roots, targets, config, as_of):
    """Select only applicable offers; keep a separate row for every applicable root scheme."""
    plan_start = pd.Period(config['planning_month'], freq='M').start_time
    candidates = []
    for (root, name, start, end), slabs in targets.groupby(['parent_dealer_id', 'scheme', 'start', 'end']):
        if root not in roots.index:
            continue
        lower = name.lower()
        kind = 'annual' if 'annual' in lower else 'quarterly' if 'quarter' in lower else None
        if kind is None:
            continue
        source = 'supplied'
        effective_start, effective_end = start, end
        if not (start <= as_of <= end and start <= plan_start <= end):
            quarter = start.to_period('Q')
            next_quarter = quarter+1
            can_reuse = (kind == 'quarterly' and start == quarter.start_time and
                         end == quarter.end_time.normalize() and
                         next_quarter.start_time <= as_of <= next_quarter.end_time and
                         next_quarter.start_time <= plan_start <= next_quarter.end_time)
            if not can_reuse:
                continue
            effective_start, effective_end = next_quarter.start_time, next_quarter.end_time.normalize()
            source = 'assumed_reused_previous_quarter'
        candidates.append((root, kind, effective_start, effective_end, source, name, slabs))
    # A supplied current-quarter offer takes precedence over an assumed carry-forward for that root/window.
    supplied = {(r, k, start, end) for r,k,start,end,source,_,_ in candidates if source == 'supplied'}
    records = []
    for root, kind, start, end, source, name, slabs in candidates:
        if source != 'supplied' and (root,kind,start,end) in supplied:
            continue
        earned = sales.loc[(sales.root_dealer_id == root) & sales.date.between(start, as_of), 'points'].sum()
        expected = max(0, roots.loc[root, 'root_recent_net_points']) * (
            config['scheme']['estimated_opportunity_window_days']/config['scheme']['rate_reference_days'])
        next_slabs = slabs.loc[slabs.slab_target_points > earned].sort_values(['slab_target_points', 'slab'])
        target = float(next_slabs.iloc[0].slab_target_points) if len(next_slabs) else np.nan
        gap = target-earned if len(next_slabs) else np.nan
        score = 100*max(0, 1-gap/expected) if expected > 0 and len(next_slabs) else 0.0
        records.append(dict(root_dealer_id=root, scheme_name=name, scheme_kind=kind,
            scheme_target_source=source, scheme_window_start=start, scheme_window_end=end,
            scheme_points_as_of=as_of, scheme_net_points=float(earned),
            scheme_next_slab=next_slabs.iloc[0].slab if len(next_slabs) else None,
            scheme_next_target=target, scheme_gap_points=gap,
            scheme_expected_points_horizon=float(expected), root_scheme_score=score,
            scheme_status='next_slab_available' if len(next_slabs) else 'all_supplied_slabs_reached'))
    columns = ['root_dealer_id', 'scheme_name', 'scheme_kind', 'scheme_target_source',
               'scheme_window_start', 'scheme_window_end', 'scheme_points_as_of', 'scheme_net_points',
               'scheme_next_slab', 'scheme_next_target', 'scheme_gap_points',
               'scheme_expected_points_horizon', 'root_scheme_score', 'scheme_status']
    context = pd.DataFrame(records, columns=columns)
    defaults = pd.DataFrame(index=roots.index)
    for col in columns[1:]:
        defaults[col] = np.nan if col in ['scheme_net_points','scheme_next_target','scheme_gap_points',
                                         'scheme_expected_points_horizon'] else None
    defaults['root_scheme_score'] = 0.0
    defaults['scheme_status'] = 'no_applicable_targets'
    if not context.empty:
        order = context.assign(_gap_ratio=context.scheme_gap_points/context.scheme_expected_points_horizon.where(
            context.scheme_expected_points_horizon > 0)).sort_values(
                ['root_dealer_id','root_scheme_score','_gap_ratio','scheme_name','scheme_target_source'],
                ascending=[True,False,True,True,True], na_position='last', kind='stable')
        for _, record in order.drop_duplicates('root_dealer_id').iterrows():
            for col in columns[1:]:
                # Object columns preserve dates/text; numeric columns remain numeric.
                if col in ['scheme_window_start','scheme_window_end','scheme_points_as_of']:
                    defaults[col] = defaults[col].astype(object)
                defaults.loc[record.root_dealer_id,col] = record[col]
    return defaults, context


def score_and_sort(frame, weights):
    result = frame.copy()
    result['priority_score'] = sum(weights[key]*result[column] for key,column in COMPONENTS.items())
    result = result.sort_values(['so_id','priority_score','raw_weighted_value_loss','recent_90_day_net_value','dealer_id'],
                                ascending=[True,False,False,False,True], kind='stable')
    result['rank_within_so'] = result.groupby('so_id').cumcount()+1
    result['priority_score_display'] = result.priority_score.round(2)
    return result.reset_index(drop=True)


def _explain(row):
    reasons = []
    if row.raw_weighted_value_loss > 0:
        reasons.append(f'Monthly-equivalent net loss {row.raw_weighted_value_loss:,.0f} scaled value units '
                       f'(MoM {row.loss_mom:,.0f}; QoQ {row.loss_qoq_monthly:,.0f}; YoY {row.loss_yoy_monthly:,.0f})')
    if row.expected_recent_value > 0 and row.actual_recent_value < row.expected_recent_value:
        reasons.append(f'Recent net value {row.actual_recent_value:,.0f} vs expected {row.expected_recent_value:,.0f}; '
                       f'support {row.sudden_support:.2f}')
    if row.potential_score > 0:
        reasons.append('Owner-group purchases below coarse GST-proxy peers; investigate, not measured wallet share')
    elif row.potential_status.startswith('unknown'):
        reasons.append('Potential unknown: insufficient GST/peer evidence')
    if row.scheme_score > 0:
        reasons.append(f'Owner group needs {row.scheme_gap_points:,.0f} points for {row.scheme_next_slab}; '
                       f'target source {row.scheme_target_source}; measured as of {row.as_of}')
    if row.is_club:
        reasons.append('Club coverage: two visits if selected')
    if row.recent_return_value < 0:
        reasons.append('Recent returns included; do not infer cause of decline')
    if not reasons:
        reasons.append('No positive supported ranking signal in observed history')
    return '; '.join(reasons)


def rank_dealers(inputs: RankingInputs, config: dict):
    """Pure in-memory ranking entry point, also used by focused synthetic tests."""
    as_of = validate_config(config)
    inputs, observations = prepare_inputs(inputs, as_of)
    d, sales = inputs.dealers.copy(), inputs.sales.copy()
    roots = resolve_roots(d)
    d['root_dealer_id'] = d.dealer_id.map(roots)
    sales['root_dealer_id'] = sales.dealer_id.map(roots)
    sales['points'] = sales.sheets*sales.points_per_sheet
    sales['gross_sale_value'] = sales.value_inr.where(sales.txn_type.eq('Sale'),0)
    sales['return_value'] = sales.value_inr.where(sales.txn_type.eq('Return'),0)
    sales['positive_purchase'] = sales.txn_type.eq('Sale') & (sales.value_inr.gt(0) | sales.sheets.gt(0))
    result = d.set_index('dealer_id', drop=False)
    for name in PERIODS:
        totals = _totals(sales, *config['comparison_periods'][name]).reindex(result.index).fillna(0)
        for source, suffix in [('value_inr','net_value'),('sheets','net_sheets'),('points','net_points'),
                               ('gross_sale_value','gross_sale_value'),('return_value','return_value')]:
            result[f'{name}_{suffix}'] = totals[source]
    loss_weights = config['value_loss_weights']
    result['loss_mom'] = positive_loss(result.mom_reference_net_value,result.mom_current_net_value)
    result['loss_qoq_monthly'] = positive_loss(result.qoq_reference_net_value,result.qoq_current_net_value,3)
    result['loss_yoy_monthly'] = positive_loss(result.yoy_reference_net_value,result.yoy_current_net_value,3)
    result['raw_weighted_value_loss'] = (loss_weights['mom']*result.loss_mom +
        loss_weights['qoq']*result.loss_qoq_monthly + loss_weights['yoy']*result.loss_yoy_monthly)
    result['value_loss_score'], result['value_loss_normalization_scale'] = normalize_by_so(
        result.raw_weighted_value_loss,result.so_id,config['normalization']['quantile'])
    window = config['sudden_drop']
    result['expected_recent_value'] = result.sudden_reference_net_value.clip(lower=0) * (
        window['recent_calendar_days']/window['reference_calendar_days'])
    result['actual_recent_value'] = result.sudden_current_net_value
    ref_start, ref_end = map(pd.Timestamp, config['comparison_periods']['sudden_reference'])
    positive_ref = sales.loc[sales.positive_purchase & sales.date.between(ref_start,ref_end)].copy()
    positive_ref['reference_week'] = (positive_ref.date-ref_start).dt.days//7
    counts = positive_ref.groupby('dealer_id').agg(positive_sale_days=('date','nunique'),
                                                  active_reference_weeks=('reference_week','nunique'))
    result['positive_sale_days_reference'] = counts.positive_sale_days.reindex(result.index).fillna(0).astype(int)
    result['active_reference_weeks'] = counts.active_reference_weeks.reindex(result.index).fillna(0).astype(int)
    support, raw, strong = sudden_features(result.expected_recent_value,result.actual_recent_value,
                                          result.positive_sale_days_reference,result.active_reference_weeks,window)
    result['sudden_support'], result['supported_raw_sudden_loss'], result['strong_drop_signal'] = support, raw, strong
    result['sudden_drop_score'],result['sudden_drop_normalization_scale'] = normalize_by_so(raw,result.so_id,config['normalization']['quantile'])
    result['sudden_status'] = np.select([result.expected_recent_value.le(0),support.lt(1)],
                                       ['no_positive_reference','limited_history_support'],default='supported')
    dates = sales.loc[sales.positive_purchase].groupby('dealer_id').date.agg(['min','max'])
    result['first_positive_sale_date'] = dates['min'].reindex(result.index)
    result['last_positive_sale_date'] = dates['max'].reindex(result.index)
    result['days_since_positive_sale'] = (as_of-result.last_positive_sale_date).dt.days
    result['recency_category'] = np.select(
        [result.last_positive_sale_date.isna(),result.days_since_positive_sale.le(config['recency']['active_max_days_since_positive_sale']),
         result.days_since_positive_sale.le(config['recency']['lapsed_max_days_since_positive_sale'])],
        ['never_purchased','active','lapsed'],default='dormant')
    result['recent_90_day_net_value'] = result.potential_recent_90_days_net_value
    result['recent_gross_sale_value'] = result.sudden_current_gross_sale_value
    result['recent_return_value'] = result.sudden_current_return_value
    result['return_driven_recent_reduction'] = (result.actual_recent_value < result.expected_recent_value) & (
        result.sudden_current_gross_sale_value >= result.sudden_reference_gross_sale_value * (
            window['recent_calendar_days']/window['reference_calendar_days'])) & result.recent_return_value.lt(0)
    owner_context = _owner_context(d,sales,inputs.gst,config,as_of)
    schemes, scheme_context = _scheme_context(sales,owner_context,inputs.targets,config,as_of)
    owner_context = owner_context.join(schemes)
    # Consolidate numeric feature blocks before adding owner-group context.
    result = result.copy()
    for column in owner_context.columns.difference(['so_id']):
        result[column] = result.root_dealer_id.map(owner_context[column])
    result['is_club'] = result.loyalty_club_member.eq('Yes')
    result['visits_required_if_selected'] = np.where(result.is_club,2,1)
    result['scheme_score'] = result.root_scheme_score.where(result.is_club,0).astype(float)
    result['has_location'] = result.lat.notna() & result.lon.notna()
    result['shared_owner'] = result.root_dealer_id.map(d.groupby('root_dealer_id').size()).gt(1)
    result['as_of'] = as_of.date().isoformat()
    result['planning_month'] = config['planning_month']
    result['ranking_version'] = config['version']
    def flags(row):
        return '|'.join(name for name, condition in [
            ('missing_location',not row.has_location),
            ('potential_unknown',str(row.potential_status).startswith('unknown')),
            ('no_recent_root_purchase',row.potential_status=='no_recent_purchase'),
            ('shared_owner',row.shared_owner),
            ('missing_applicable_club_targets',row.is_club and row.scheme_status=='no_applicable_targets'),
            ('gst_status_needs_verification',pd.notna(row.gst_status) and row.gst_status!='Active'),
            ('returns_driven_recent_reduction',row.return_driven_recent_reduction),
            ('negative_recent_net_value',row.actual_recent_value<0)] if condition)
    result['data_flags'] = result.apply(flags,axis=1)
    result = score_and_sort(result.reset_index(drop=True),config['component_weights'])
    result['reason'] = result.apply(_explain,axis=1)
    validate_output(result,inputs.dealers)
    return result, scheme_context, owner_context.reset_index(names='root_dealer_id'), observations


def validate_output(ranking, dealers):
    if len(ranking) != len(dealers) or ranking.dealer_id.duplicated().any() or set(ranking.dealer_id) != set(dealers.dealer_id):
        raise ValueError('Output must preserve each original dealer exactly once')
    owners = ranking.dealer_id.map(dealers.set_index('dealer_id').so_id)
    if not ranking.so_id.eq(owners).all():
        raise ValueError('Output territory ownership changed')
    for column in ['priority_score',*COMPONENTS.values()]:
        if not np.isfinite(ranking[column]).all() or not ranking[column].between(0,100).all():
            raise ValueError(f'Non-finite or out-of-bounds score: {column}')
    if 'reason' in ranking and not ranking.reason.str.len().gt(0).all():
        raise ValueError('Missing dealer explanation')


def _clean_json(value):
    if isinstance(value,dict): return {str(k):_clean_json(v) for k,v in value.items()}
    if isinstance(value,list): return [_clean_json(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT: return None
    if isinstance(value,(pd.Timestamp,)): return value.isoformat()
    if isinstance(value,(np.integer,)): return int(value)
    if isinstance(value,(np.bool_,)): return bool(value)
    if isinstance(value,(float,np.floating)): return float(value) if np.isfinite(value) else None
    return value


def run_ranking(workbook: Path, config_path: Path, out: Path):
    source_hash = hashlib.sha256(workbook.read_bytes()).hexdigest()
    config = json.loads(config_path.read_text())
    rankings, schemes, roots, observations = rank_dealers(load_inputs(workbook),config)
    if hashlib.sha256(workbook.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError('Source workbook changed during ranking')
    out.mkdir(parents=True,exist_ok=True)
    rankings.to_csv(out/'dealer_rankings.csv',index=False,date_format='%Y-%m-%d')
    for so, frame in rankings.groupby('so_id'):
        frame.to_csv(out/f'rankings_{so}.csv',index=False,date_format='%Y-%m-%d')
    schemes.to_csv(out/'scheme_context.csv',index=False,date_format='%Y-%m-%d')
    roots.to_csv(out/'root_context.csv',index=False,date_format='%Y-%m-%d')
    sensitivity = []
    for shift in [-0.05,0.05]:
        weights = dict(config['component_weights'])
        weights['value_loss'] -= shift
        weights['sudden_drop'] += shift
        if min(weights.values()) < 0:
            continue
        changed = score_and_sort(rankings,weights)
        for so, baseline in rankings.groupby('so_id'):
            n = min(20,len(baseline))
            initial_ids = set(baseline.head(n).dealer_id)
            candidate_ids = set(changed.loc[changed.so_id==so].head(n).dealer_id)
            sensitivity.append(dict(so_id=so,value_loss_weight=weights['value_loss'],
                sudden_drop_weight=weights['sudden_drop'],top_n=n,overlap=len(initial_ids & candidate_ids),
                overlap_fraction=len(initial_ids & candidate_ids)/n))
    pd.DataFrame(sensitivity).to_csv(out/'sensitivity_top20.csv',index=False)
    summary = dict(status='ranking_implemented_not_simulator_validated',as_of=config['as_of'],
        planning_month=config['planning_month'],source_sha256=source_hash,source_unchanged=True,
        config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),component_weights=config['component_weights'],
        dealer_count=len(rankings),club_outlets=int(rankings.is_club.sum()),owner_groups=len(roots),
        recency_counts=rankings.recency_category.value_counts().to_dict(),
        potential_status_counts=rankings.potential_status.value_counts().to_dict(),
        strong_drop_signals=int(rankings.strong_drop_signal.sum()),
        positive_scheme_bonus_outlets=int(rankings.scheme_score.gt(0).sum()),
        sensitivity=sensitivity,observations=observations,officers={})
    for so, frame in rankings.groupby('so_id'):
        columns = ['dealer_id','rank_within_so','priority_score','raw_weighted_value_loss','sudden_drop_score',
                   'potential_score','scheme_score','recency_category','is_club','has_location','reason']
        summary['officers'][so] = dict(dealers=len(frame),zero_score_dealers=int(frame.priority_score.eq(0).sum()),
            strong_drop_signals=int(frame.strong_drop_signal.sum()),top10=frame.head(10)[columns].to_dict('records'))
    summary = _clean_json(summary)
    (out/'ranking_summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    lines = ['PJP DEALER RANKING SUMMARY',
        f"Snapshot: {config['as_of']}; planning month: {config['planning_month']}",
        'Confidential local output. Weights are baseline assumptions; no scheduling or simulation is implemented.',
        f'Dealers: {len(rankings)}; club outlets: {summary["club_outlets"]}; owner groups: {len(roots)}',
        f'Strong recent-drop warnings: {summary["strong_drop_signals"]}',
        f'Recency: {summary["recency_counts"]}', f'Potential evidence: {summary["potential_status_counts"]}',
        'Value amounts are scaled source units, not verified exact commercial rupees.',
        'No coordinate penalty, invented turnover midpoint, or redistribution of missing evidence weights.',
        'Scheme context is as of the snapshot, not actual November progress. Quarterly targets are reused only as instructed.',
        'No focus set or visit plan has been selected. Club obligations will be protected during focus selection.', '']
    for so, frame in rankings.groupby('so_id'):
        lines.append(f'{so} — top 10')
        lines.append(frame.head(10)[['rank_within_so','dealer_id','priority_score_display',
                     'recency_category','is_club','has_location']].to_string(index=False))
        for _,row in frame.head(3).iterrows():
            lines.append(f'{row.dealer_id}: {row.reason}')
        lines.append('')
    lines.extend(['TOP-20 WEIGHT SENSITIVITY',pd.DataFrame(sensitivity).to_string(index=False)])
    (out/'ranking_summary.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='officers'},indent=2,allow_nan=False))
    return rankings,summary
