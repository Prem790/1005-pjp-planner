"""Focus-selection constraints tested with synthetic dealer universes."""
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from pjp.selection import select_focus
from pjp.travel import direct_travel_minutes, normal_day_lower_bound

CONFIG=Path(__file__).resolve().parents[1]/'config/selection.json'


def fixture(n=160):
    config=json.loads(CONFIG.read_text())
    dealers=pd.DataFrame([dict(dealer_id=f'T{i:03}',so_id='SO-A',parent_dealer_id=None,
        loyalty_club_member='Yes' if i==n-1 else 'No',lat=12+i*0.0001,lon=77.) for i in range(n)])
    ranked=pd.DataFrame([dict(dealer_id=f'T{i:03}',so_id='SO-A',root_dealer_id=f'T{i:03}',
        rank_within_so=i+1,priority_score=max(0,100-i*0.5) if i<n-1 else 0,
        raw_weighted_value_loss=float(n-i),recent_90_day_net_value=100.,
        is_club=i==n-1,has_location=True,recency_category='active',as_of=config['as_of'],
        planning_month=config['planning_month'],reason='Synthetic test evidence') for i in range(n)])
    officers=pd.DataFrame([dict(so_id='SO-A',geography='Dense metro',approx_day_start_lat=12.,approx_day_start_lon=77.)])
    dates=pd.date_range('2026-11-01','2026-11-30')
    calendar=pd.DataFrame({'date':dates,'working_day':['Yes' if d.weekday()!=6 else 'No' for d in dates]})
    return ranked,dealers,officers,calendar,config


class SelectionTests(unittest.TestCase):
    def test_all_clubs_protected_and_normal_visit_budget_filled(self):
        decisions,summary=select_focus(*fixture())
        selected=decisions.loc[decisions.selected]
        self.assertEqual(len(selected),149)
        self.assertEqual(selected.selected_visit_requirement.sum(),150)
        self.assertTrue(decisions.loc[decisions.is_club,'selected'].all())
        self.assertEqual(summary['SO-A']['unfilled_visit_slots'],0)
        self.assertEqual(summary['SO-A']['selected_club_zero_priority_signal'],1)
        self.assertEqual(decisions.loc[decisions.dealer_id=='T159','decision_reason'].iloc[0],'protected_club_coverage')

    def test_missing_location_is_retained_with_a_review_flag(self):
        args=fixture();ranking,dealers=args[:2]
        dealers.loc[dealers.dealer_id=='T000',['lat','lon']]=np.nan
        ranking.loc[ranking.dealer_id=='T000','has_location']=False
        decisions,_=select_focus(*args)
        row=decisions.loc[decisions.dealer_id=='T000'].iloc[0]
        self.assertTrue(row.selected)
        self.assertTrue(row.location_review_required)
        self.assertEqual(row.direct_travel_min_from_start,25)

    def test_optional_impossible_travel_is_deferred_without_changing_rank(self):
        args=fixture();args[1].loc[args[1].dealer_id=='T000','lat']=16.
        decisions,summary=select_focus(*args)
        row=decisions.loc[decisions.dealer_id=='T000'].iloc[0]
        self.assertFalse(row.selected)
        self.assertEqual(row.rank_within_so,1)
        self.assertEqual(row.priority_score,100)
        self.assertEqual(row.decision_reason,'deferred_normal_day_travel_lower_bound')
        self.assertEqual(summary['SO-A']['required_visits'],150)

    def test_impossible_mandatory_club_fails_instead_of_silently_dropping(self):
        args=fixture();args[1].loc[args[1].dealer_id=='T159','lat']=16.
        with self.assertRaisesRegex(ValueError,'protected club outlets'):
            select_focus(*args)

    def test_duplicate_optional_owner_is_deferred(self):
        args=fixture();args[1].loc[args[1].dealer_id=='T001','parent_dealer_id']='T000'
        args[0].loc[args[0].dealer_id=='T001','root_dealer_id']='T000'
        decisions,summary=select_focus(*args)
        row=decisions.loc[decisions.dealer_id=='T001'].iloc[0]
        self.assertFalse(row.selected)
        self.assertEqual(row.decision_reason,'deferred_shared_owner_already_covered')
        self.assertEqual(summary['SO-A']['required_visits'],150)

    def test_all_related_mandatory_clubs_remain_selected(self):
        args=fixture()
        args[1].loc[args[1].dealer_id=='T000','parent_dealer_id']='T159'
        args[1].loc[args[1].dealer_id=='T000','loyalty_club_member']='Yes'
        args[0].loc[args[0].dealer_id=='T000','root_dealer_id']='T159'
        args[0].loc[args[0].dealer_id=='T000','is_club']=True
        decisions,summary=select_focus(*args)
        pair=decisions.loc[decisions.dealer_id.isin(['T000','T159'])]
        self.assertTrue(pair.selected.all())
        self.assertTrue(pair.shared_owner_review_required.all())
        self.assertEqual(pair.selected_visit_requirement.sum(),4)
        self.assertEqual(summary['SO-A']['selected_dealers'],148)

    def test_capacity_shortfall_is_explicit(self):
        decisions,summary=select_focus(*fixture(n=3))
        self.assertEqual(summary['SO-A']['required_visits'],4)
        self.assertEqual(summary['SO-A']['unfilled_visit_slots'],146)
        self.assertEqual(summary['SO-A']['selection_status'],'capacity_shortfall_pending_review')

    def test_stale_ownership_or_duplicate_ranking_fails(self):
        args=fixture();args[0].loc[0,'so_id']='SO-B'
        with self.assertRaisesRegex(ValueError,'ownership'):
            select_focus(*args)
        args=list(fixture());args[0]=pd.concat([args[0],args[0].iloc[[0]]],ignore_index=True)
        with self.assertRaisesRegex(ValueError,'exactly once'):
            select_focus(*args)

    def test_row_order_does_not_change_selection(self):
        original,_=select_focus(*fixture())
        args=list(fixture());args[0]=args[0].sample(frac=1,random_state=4)
        changed,_=select_focus(*args)
        self.assertEqual(original.loc[original.selected,'dealer_id'].tolist(),changed.loc[changed.selected,'dealer_id'].tolist())

    def test_rain_risk_is_flagged_without_claiming_route_feasibility(self):
        args=fixture();args[1].loc[args[1].dealer_id=='T000','lat']=12.54
        decisions,_=select_focus(*args)
        row=decisions.loc[decisions.dealer_id=='T000'].iloc[0]
        self.assertTrue(row.selected)
        self.assertTrue(row.normal_day_screen_pass)
        self.assertFalse(row.rain_day_screen_pass)

    def test_calendar_requires_full_month_and_correct_workdays(self):
        args=list(fixture());args[3]=args[3].iloc[:-1].copy()
        with self.assertRaisesRegex(ValueError,'every day'):
            select_focus(*args)
        args=fixture();args[3].loc[0,'working_day']='Yes'
        with self.assertRaisesRegex(ValueError,'Monday-Saturday'):
            select_focus(*args)

    def test_lower_bound_and_simulator_fallbacks(self):
        model=fixture()[4]['travel_model']
        self.assertEqual(direct_travel_minutes((12.,77.),None,'metro',model),25)
        self.assertEqual(direct_travel_minutes((12.,77.),None,'upcountry',model),45)
        self.assertEqual(normal_day_lower_bound(310,6,model),460)
        self.assertGreater(normal_day_lower_bound(310.1,6,model),460)
        with self.assertRaisesRegex(ValueError,'Coordinates'):
            direct_travel_minutes((12.,77.),(100.,77.),'metro',model)


if __name__=='__main__':
    unittest.main()
