import unittest
import pandas as pd

from pjp.nudges import build_nudge, generate_nudges, number


RANK = dict(as_of='2026-10-15',comparison_periods={
    'sudden_current':['2026-10-02','2026-10-15'],
    'sudden_reference':['2026-08-07','2026-10-01']})
POLICY = dict(max_characters_exclusive=300,send_time_previous_evening='18:30',timezone='Asia/Kolkata')
VISIT = dict(date='2026-11-02',so_id='SO-X',account_id='X1',sequence=1,planned_arrival='10:00')
ROW = dict(so_id='SO-X',as_of='2026-10-15',planning_month='2026-11',
    strong_drop_signal=False,return_driven_recent_reduction=False,recency_category='active',
    raw_weighted_value_loss=10,loss_mom=25,loss_qoq_monthly=0,loss_yoy_monthly=0,
    potential_score=0,priority_score=4,is_club=False,has_location=True,lat=12,lon=77,
    first_positive_sale_date='2026-01-01',last_positive_sale_date='2026-10-01',
    root_dealer_id='X1',scheme_status='no_applicable_targets')


class NudgeTests(unittest.TestCase):
    def test_snapshot_units_and_previous_evening(self):
        result,proof = build_nudge(VISIT,ROW,RANK,POLICY)
        self.assertIn('15 Oct data',result['message'])
        self.assertIn('(scaled/month)',result['message'])
        self.assertEqual(result['send_at'],'2026-11-01T18:30+05:30')
        self.assertEqual(proof['claims'][0]['evidence']['loss_mom'],25)

    def test_drop_is_historical_and_negative_returns_remain_signed(self):
        r = dict(ROW,strong_drop_signal=True,actual_recent_value=-50,
                 expected_recent_value=200,sudden_support=1)
        result,proof = build_nudge(VISIT,r,RANK,POLICY)
        self.assertIn('2-15 Oct net -50 vs 200 baseline',result['message'])
        self.assertEqual(proof['claims'][0]['comparison_window'],RANK['comparison_periods']['sudden_current'])

    def test_shared_scheme_is_assumed_and_dated(self):
        r = dict(ROW,is_club=True,root_dealer_id='PARENT',scheme_gap_points=600,
                 scheme_next_target=1000,scheme_net_points=400,scheme_next_slab='Slab 1',
                 scheme_points_as_of='2026-10-15',scheme_window_start='2026-10-01',
                 scheme_window_end='2026-12-31',scheme_kind='quarterly',
                 scheme_target_source='assumed_reused_previous_quarter')
        result,proof = build_nudge(VISIT,r,RANK,POLICY)
        self.assertIn('Owner PARENT: 600 pts',result['message'])
        self.assertIn('Q4 target assumed',result['message'])
        self.assertIn('verify balance/offer',result['message'])
        self.assertLess(len(result['message']),300)
        expired = dict(r,scheme_window_end='2026-10-31')
        result,proof = build_nudge(VISIT,expired,RANK,POLICY)
        self.assertNotIn('pts to',result['message'])
        self.assertEqual(proof['scheme_context_status'],'expired_or_not_yet_applicable')

    def test_missing_gps_and_no_sales_are_explicit(self):
        r = dict(ROW,recency_category='never_purchased',has_location=False,
                 lat=float('nan'),lon=float('nan'),first_positive_sale_date=None)
        result,proof = build_nudge(VISIT,r,RANK,POLICY)
        self.assertIn('No positive sale in supplied history',result['message'])
        self.assertIn('GPS missing: confirm pin',result['message'])
        self.assertIsNone(proof['claims'][1]['evidence']['lat'])

    def test_returns_prompt_does_not_assert_causation(self):
        r = dict(ROW,return_driven_recent_reduction=True,recent_gross_sale_value=400,recent_return_value=-200)
        result,_ = build_nudge(VISIT,r,RANK,POLICY)
        self.assertIn('Returns may explain',result['message'])
        self.assertIn('before ordering',result['message'])

    def test_positive_partial_signal_does_not_become_false_zero_claim(self):
        result,_ = build_nudge(VISIT,dict(ROW,raw_weighted_value_loss=0,priority_score=8),RANK,POLICY)
        self.assertNotIn('No positive ranking signal',result['message'])
        self.assertIn('Review current stock',result['message'])

    def test_future_snapshot_or_reactivation_date_is_rejected(self):
        with self.assertRaises(ValueError):
            build_nudge(VISIT,dict(ROW,as_of='2026-11-03'),RANK,POLICY)
        with self.assertRaises(ValueError):
            build_nudge(VISIT,dict(ROW,recency_category='dormant',last_positive_sale_date='2026-11-01'),RANK,POLICY)

    def test_ownership_and_duplicate_dates_rejected(self):
        focus = pd.DataFrame([dict(ROW,dealer_id='X1')])
        with self.assertRaises(ValueError):
            generate_nudges(pd.DataFrame([dict(VISIT,so_id='SO-Y')]),focus,RANK,POLICY)
        with self.assertRaises(ValueError):
            generate_nudges(pd.DataFrame([VISIT,VISIT]),focus,RANK,POLICY)

    def test_length_failure_does_not_truncate_evidence(self):
        with self.assertRaisesRegex(ValueError,'character budget'):
            build_nudge(dict(VISIT,account_id='x'*300),ROW,RANK,POLICY)

    def test_small_values_are_not_rendered_as_zero(self):
        self.assertEqual(number(.45),'0.45')
        self.assertEqual(number(.001),'<0.01')
        with self.assertRaises(ValueError):
            number(float('inf'))


if __name__=='__main__':
    unittest.main()
