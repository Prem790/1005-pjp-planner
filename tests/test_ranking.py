"""Business-rule tests with synthetic data; no confidential workbook fixtures."""
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from pjp.data import RankingInputs, resolve_roots
from pjp.ranking import normalize_by_so, positive_loss, rank_dealers, sudden_features, validate_config

CONFIG_PATH = Path(__file__).resolve().parents[1]/'config/ranking.json'


def config():
    result = json.loads(CONFIG_PATH.read_text())
    result['potential']['minimum_same_so_same_proxy_peer_roots'] = 2
    return result


def fixture():
    dealers = pd.DataFrame([
        ['D0','SO-A',None,'Yes',12.0,77.0],
        ['D1','SO-A','D0','Yes',12.1,77.1],
        ['D2','SO-A',None,'No',12.2,77.2],
        ['D3','SO-A',None,'Yes',None,None],
    ],columns=['dealer_id','so_id','parent_dealer_id','loyalty_club_member','lat','lon'])
    observations = [
        ['2025-07-01','D0',90,1],['2026-04-01','D0',120,1],
        ['2026-08-07','D0',120,2],['2026-08-14','D0',120,2],
        ['2026-08-21','D0',120,2],['2026-09-04','D0',120,2],
        ['2026-10-02','D0',20,2],['2026-10-03','D0',-10,-1],
        ['2026-10-04','D1',30,3],['2026-09-20','D2',400,4],['2026-10-04','D2',200,2],
    ]
    sales = pd.DataFrame([dict(date=day,dealer_id=dealer,value_inr=value,sheets=quantity,
        billed_by='Different officer',product_tier='Core',points_per_sheet=2,
        txn_type='Sale' if value >= 0 else 'Return') for day,dealer,value,quantity in observations])
    gst = pd.DataFrame([['D0','broad band','Active'],['D1','different child band','Active'],
                        ['D2','broad band','Active'],['D3',None,None]],
                       columns=['dealer_id','turnover_band_proxy','gst_status'])
    targets = pd.DataFrame([
        ['D0','Club annual scheme','2026-04-01','2027-03-31','Slab 1',30],
        ['D0','Club annual scheme','2026-04-01','2027-03-31','Slab 2',60],
        ['D0','Club quarterly scheme (Jul-Sep)','2026-07-01','2026-09-30','Slab 1',20],
        ['D0','Club quarterly scheme (Jul-Sep)','2026-07-01','2026-09-30','Slab 2',40],
        ['D0','Club monthly top-up (Aug)','2026-08-01','2026-08-31','Slab 1',1],
    ],columns=['parent_dealer_id','scheme','start','end','slab','slab_target_points'])
    return RankingInputs(pd.DataFrame({'so_id':['SO-A']}),dealers,sales,gst,
                         pd.DataFrame({'product_tier':['Core'],'points_per_sheet':[2]}),targets)


class RankingTests(unittest.TestCase):
    def test_value_loss_keeps_returns_and_scales_quarters(self):
        np.testing.assert_allclose(positive_loss([100,0,-20,100],[60,-10,-40,120]),[40,0,0,0])
        np.testing.assert_allclose(positive_loss([300],[0],3),[100])

    def test_normalization_preserves_zero_and_is_territory_relative(self):
        values = pd.Series([0.,10.,20.,1.,0.])
        scores,scales = normalize_by_so(values,pd.Series(['A','A','A','B','C']))
        self.assertEqual(scores.iloc[0],0)
        self.assertAlmostEqual(scales.iloc[1],19.5)
        self.assertAlmostEqual(scores.iloc[1],100*10/19.5)
        self.assertEqual(scores.iloc[2],100)
        self.assertEqual(scores.iloc[3],100)
        self.assertEqual(scores.iloc[4],0)
        with self.assertRaises(ValueError):
            normalize_by_so(pd.Series([np.inf]),pd.Series(['A']))

    def test_sparse_history_cannot_create_strong_drop(self):
        expected,actual = pd.Series([100.,100.,0.]),pd.Series([25.,25.,-10.])
        support,loss,strong = sudden_features(expected,actual,pd.Series([1,4,4]),pd.Series([1,4,4]),config()['sudden_drop'])
        self.assertAlmostEqual(support.iloc[0],1/12)
        self.assertAlmostEqual(loss.iloc[0],75/12)
        self.assertEqual(strong.tolist(),[False,True,False])
        self.assertEqual(loss.iloc[2],0)

    def test_parent_cycles_and_unknown_parents_fail(self):
        d = fixture().dealers.copy()
        d.loc[d.dealer_id=='D0','parent_dealer_id']='D1'
        with self.assertRaisesRegex(ValueError,'cycle'):
            resolve_roots(d)
        d.loc[d.dealer_id=='D0','parent_dealer_id']='missing'
        with self.assertRaisesRegex(ValueError,'Unknown parent'):
            resolve_roots(d)

    def test_end_to_end_raw_values_and_owner_attribution(self):
        ranked,_,_,_ = rank_dealers(fixture(),config())
        r = ranked.set_index('dealer_id')
        self.assertEqual(len(r),4)
        self.assertTrue(r.so_id.eq('SO-A').all())  # Invoice officer was deliberately different.
        self.assertEqual(r.loc['D0','loss_mom'],240)
        self.assertEqual(r.loc['D0','raw_weighted_value_loss'],96)
        self.assertEqual(r.loc['D0','expected_recent_value'],120)
        self.assertEqual(r.loc['D0','actual_recent_value'],10)  # 20 sale minus 10 return.
        self.assertTrue(r.loc['D0','strong_drop_signal'])
        self.assertEqual(r.loc['D3','priority_score'],0)
        self.assertEqual(r.loc['D3','recency_category'],'never_purchased')
        self.assertEqual(r.loc['D3','visits_required_if_selected'],2)

    def test_parent_points_are_aggregated_once_and_expired_monthly_offer_is_excluded(self):
        ranked,schemes,roots,_ = rank_dealers(fixture(),config())
        r = ranked.set_index('dealer_id')
        annual = schemes.loc[schemes.scheme_kind=='annual'].iloc[0]
        self.assertEqual(annual.scheme_net_points,26)  # Includes child and negative return points.
        self.assertEqual(annual.scheme_gap_points,4)
        self.assertAlmostEqual(annual.root_scheme_score,50)
        self.assertEqual(r.loc['D0','scheme_score'],r.loc['D1','scheme_score'])
        self.assertEqual(len(roots),3)  # Shared owner is only one peer/root.
        self.assertEqual(len(schemes),2)
        quarter = schemes.loc[schemes.scheme_kind=='quarterly'].iloc[0]
        self.assertEqual(str(quarter.scheme_window_start.date()),'2026-10-01')
        self.assertEqual(str(quarter.scheme_window_end.date()),'2026-12-31')
        self.assertEqual(quarter.scheme_net_points,8)  # No Jul-Sep points carried into Q4.
        self.assertEqual(quarter.scheme_target_source,'assumed_reused_previous_quarter')
        self.assertTrue(pd.isna(r.loc['D3','scheme_gap_points']))

    def test_missing_gst_never_becomes_a_headroom_claim(self):
        ranked,_,roots,_ = rank_dealers(fixture(),config())
        r = ranked.set_index('dealer_id')
        self.assertEqual(r.loc['D3','potential_score'],0)
        self.assertEqual(r.loc['D3','potential_status'],'unknown_no_gst_proxy')
        self.assertEqual(r.loc['D0','potential_peer_count'],2)
        self.assertAlmostEqual(r.loc['D0','potential_score'],100/14)
        self.assertEqual(r.loc['D1','turnover_band_proxy'],'broad band')
        self.assertEqual(r.loc['D0','potential_score'],r.loc['D1','potential_score'])
        modified = fixture()
        modified.gst.loc[modified.gst.dealer_id=='D3','turnover_band_proxy']='broad band'
        changed,_,_,_ = rank_dealers(modified,config())
        self.assertEqual(changed.set_index('dealer_id').loc['D3','potential_score'],0)

    def test_future_sales_cannot_change_rank_or_observed_features(self):
        initial,_,_,_ = rank_dealers(fixture(),config())
        changed = fixture()
        future = changed.sales.iloc[[0]].copy()
        future['date']='2026-10-16'
        future['value_inr']=10**12
        changed.sales = pd.concat([changed.sales,future],ignore_index=True)
        observed,_,_,notes = rank_dealers(changed,config())
        pd.testing.assert_frame_equal(initial,observed)
        self.assertEqual(notes['sales_rows_after_cutoff_excluded'],1)

    def test_row_order_and_missing_gps_do_not_change_commercial_rank(self):
        initial,_,_,_ = rank_dealers(fixture(),config())
        shuffled = fixture()
        shuffled.dealers = shuffled.dealers.sample(frac=1,random_state=7).reset_index(drop=True)
        shuffled.sales = shuffled.sales.sample(frac=1,random_state=2).reset_index(drop=True)
        shuffled.dealers[['lat','lon']]=np.nan
        actual,_,_,_ = rank_dealers(shuffled,config())
        self.assertEqual(initial.dealer_id.tolist(),actual.dealer_id.tolist())
        np.testing.assert_allclose(initial.priority_score,actual.priority_score)
        self.assertTrue(actual.data_flags.str.contains('missing_location').all())

    def test_current_quarter_targets_override_assumed_carry_forward(self):
        inputs = fixture()
        replacement = inputs.targets.loc[inputs.targets.scheme.str.contains('quarterly')].copy()
        replacement['start']='2026-10-01'
        replacement['end']='2026-12-31'
        replacement['scheme']='Club quarterly scheme (Oct-Dec)'
        replacement['slab_target_points'] *= 2
        inputs.targets = pd.concat([inputs.targets,replacement],ignore_index=True)
        _,schemes,_,_ = rank_dealers(inputs,config())
        quarter = schemes.loc[schemes.scheme_kind=='quarterly']
        self.assertEqual(len(quarter),1)
        self.assertEqual(quarter.iloc[0].scheme_target_source,'supplied')
        self.assertEqual(quarter.iloc[0].scheme_next_target,40)

    def test_invalid_weights_and_future_windows_are_rejected(self):
        cfg=config();cfg['component_weights']['value_loss']=0.9
        with self.assertRaisesRegex(ValueError,'sum to one'):
            validate_config(cfg)
        cfg=config();cfg['comparison_periods']['mom_current'][1]='2026-10-16'
        with self.assertRaisesRegex(ValueError,'future'):
            validate_config(cfg)

    def test_partial_month_cannot_be_used_as_full_mom_period(self):
        cfg=config()
        cfg['comparison_periods']['mom_current']=['2026-10-01','2026-10-15']
        with self.assertRaisesRegex(ValueError,'complete calendar month'):
            validate_config(cfg)

    def test_score_formula_matches_hand_worked_components(self):
        from pjp.ranking import score_and_sort
        row = pd.DataFrame([dict(dealer_id='example',so_id='A',raw_weighted_value_loss=10,
            recent_90_day_net_value=100,value_loss_score=80,sudden_drop_score=60,
            potential_score=40,scheme_score=20)])
        ranked=score_and_sort(row,config()['component_weights'])
        self.assertEqual(ranked.iloc[0].priority_score,67)

    def test_return_sign_and_unknown_product_points_fail(self):
        inputs=fixture()
        inputs.sales.loc[inputs.sales.txn_type=='Return','value_inr']=10
        with self.assertRaisesRegex(ValueError,'signs'):
            rank_dealers(inputs,config())
        inputs=fixture()
        inputs.sales.loc[0,'points_per_sheet']=100
        with self.assertRaisesRegex(ValueError,'product points'):
            rank_dealers(inputs,config())

    def test_zero_signal_ties_use_dealer_id(self):
        inputs=fixture()
        extra=inputs.dealers.loc[inputs.dealers.dealer_id=='D3'].copy()
        extra['dealer_id']='D4'
        extra['loyalty_club_member']='No'
        inputs.dealers=pd.concat([extra,inputs.dealers],ignore_index=True)
        ranked,_,_,_=rank_dealers(inputs,config())
        zero=ranked.loc[ranked.priority_score==0]
        # D2 has greater recent sales; only D3/D4 tie on all numeric keys.
        self.assertEqual(zero.dealer_id.tolist(),['D2','D3','D4'])

    def test_duplicate_dealer_ids_are_rejected(self):
        inputs=fixture()
        inputs.dealers=pd.concat([inputs.dealers,inputs.dealers.iloc[[0]]],ignore_index=True)
        with self.assertRaisesRegex(ValueError,'unique'):
            rank_dealers(inputs,config())


if __name__ == '__main__':
    unittest.main()
