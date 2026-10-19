"""Load ranking inputs and validate their identities without modifying the workbook."""
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class RankingInputs:
    officers: pd.DataFrame
    dealers: pd.DataFrame
    sales: pd.DataFrame
    gst: pd.DataFrame
    tiers: pd.DataFrame
    targets: pd.DataFrame


def load_inputs(path: Path) -> RankingInputs:
    with pd.ExcelFile(path) as book:
        return RankingInputs(
            pd.read_excel(book, sheet_name='Officers'),
            pd.read_excel(book, sheet_name='Dealers'),
            pd.read_excel(book, sheet_name='Sales_Daily'),
            pd.read_excel(book, sheet_name='GST_Filings', header=2),
            pd.read_excel(book, sheet_name='Product_Tiers'),
            pd.read_excel(book, sheet_name='Scheme_Targets', header=2),
        )


def resolve_roots(dealers: pd.DataFrame) -> dict[str, str]:
    """Resolve chains; fail on cycles, unknown parents, or mixed-SO owner groups."""
    parent = dealers.set_index('dealer_id').parent_dealer_id.to_dict()
    owners = dealers.set_index('dealer_id').so_id.to_dict()
    roots = {}
    for dealer in parent:
        current, seen = dealer, set()
        while pd.notna(parent.get(current)):
            if current in seen:
                raise ValueError(f'Parent cycle involving {dealer}')
            seen.add(current)
            current = parent[current]
            if current not in parent:
                raise ValueError(f'Unknown parent {current} for {dealer}')
        if owners[current] != owners[dealer]:
            raise ValueError(f'Owner group crosses SO territories: {dealer}, {current}')
        roots[dealer] = current
    return roots


def prepare_inputs(inputs: RankingInputs, as_of: pd.Timestamp) -> tuple[RankingInputs, dict]:
    """Validate required fields and filter dated observations at the declared cutoff."""
    required = {
        'officers': ['so_id'],
        'dealers': ['dealer_id', 'so_id', 'parent_dealer_id', 'loyalty_club_member', 'lat', 'lon'],
        'sales': ['date', 'dealer_id', 'billed_by', 'product_tier', 'points_per_sheet',
                  'txn_type', 'sheets', 'value_inr'],
        'gst': ['dealer_id', 'turnover_band_proxy', 'gst_status'],
        'tiers': ['product_tier', 'points_per_sheet'],
        'targets': ['parent_dealer_id', 'scheme', 'start', 'end', 'slab', 'slab_target_points'],
    }
    frames = {name: getattr(inputs, name).copy() for name in required}
    for name, columns in required.items():
        missing = set(columns) - set(frames[name].columns)
        if missing:
            raise ValueError(f'{name}: missing columns {sorted(missing)}')
    for name, key in [('officers', 'so_id'), ('dealers', 'dealer_id'), ('gst', 'dealer_id'),
                      ('tiers', 'product_tier')]:
        if frames[name][key].isna().any() or frames[name][key].duplicated().any():
            raise ValueError(f'{name}: {key} must be present and unique')
    d, s, g, t = (frames[name] for name in ['dealers', 'sales', 'gst', 'targets'])
    ids = set(d.dealer_id)
    if d.so_id.isna().any() or not set(d.so_id) <= set(frames['officers'].so_id):
        raise ValueError('Dealer has missing or unknown current SO')
    if not d.loyalty_club_member.isin(['Yes', 'No']).all():
        raise ValueError('Club membership must explicitly be Yes or No')
    resolve_roots(d)
    for name, column in [('sales', 'dealer_id'), ('gst', 'dealer_id'), ('targets', 'parent_dealer_id')]:
        if frames[name][column].isna().any() or not set(frames[name][column]) <= ids:
            raise ValueError(f'{name}: missing or unknown dealer reference')
    for column in ['date']:
        s[column] = pd.to_datetime(s[column], errors='raise')
        if s[column].isna().any():
            raise ValueError('Sales date is missing')
    for column in ['sheets', 'value_inr', 'points_per_sheet']:
        s[column] = pd.to_numeric(s[column], errors='raise')
        if not np.isfinite(s[column]).all():
            raise ValueError(f'Sales {column} must be finite and present')
    if not s.txn_type.isin(['Sale', 'Return']).all():
        raise ValueError('Unsupported transaction type')
    bad_sign = (s.txn_type.eq('Sale') & ((s.sheets < 0) | (s.value_inr < 0))) | (
        s.txn_type.eq('Return') & ((s.sheets > 0) | (s.value_inr > 0)))
    if bad_sign.any():
        raise ValueError('Sales/return signs contradict transaction types')
    expected = s.product_tier.map(frames['tiers'].set_index('product_tier').points_per_sheet)
    if expected.isna().any() or not s.points_per_sheet.eq(expected).all():
        raise ValueError('Unknown tier or inconsistent product points')
    for column in ['start', 'end']:
        t[column] = pd.to_datetime(t[column], errors='raise')
        if t[column].isna().any():
            raise ValueError('Scheme dates are missing')
    t['slab_target_points'] = pd.to_numeric(t.slab_target_points, errors='raise')
    if not np.isfinite(t.slab_target_points).all() or (t.slab_target_points <= 0).any():
        raise ValueError('Scheme targets must be finite and positive')
    if (t.start > t.end).any() or t.scheme.isna().any() or t.slab.isna().any():
        raise ValueError('Invalid scheme window/name/slab')
    key = ['parent_dealer_id', 'scheme', 'start', 'end', 'slab']
    if t.duplicated(key).any():
        raise ValueError('Duplicate scheme slab key')
    for _, group in t.groupby(key[:4]):
        number = group.slab.astype(str).str.extract(r'(\d+)')[0]
        if number.isna().any() or number.duplicated().any():
            raise ValueError('Scheme slabs need unique numeric positions')
        ordered = group.assign(position=number.astype(int)).sort_values('position')
        if not (ordered.slab_target_points.diff().dropna() > 0).all():
            raise ValueError('Scheme targets must increase strictly across slabs')
    excluded = int((s.date > as_of).sum())
    frames['sales'] = s.loc[s.date <= as_of].copy()
    return replace(inputs, **frames), {'sales_rows_after_cutoff_excluded': excluded,
                                      'sales_rows_used': len(frames['sales'])}
