import unittest

import pandas as pd

from pjp.evaluation import reconcile_static, ratio


def fixture():
    plan = pd.DataFrame([
        dict(date='2026-11-02',so_id='SO-X',account_id='club',sequence=1),
        dict(date='2026-11-04',so_id='SO-X',account_id='a',sequence=1),
        dict(date='2026-11-10',so_id='SO-X',account_id='b',sequence=1),
        dict(date='2026-11-16',so_id='SO-X',account_id='club',sequence=1)])
    focus = pd.DataFrame([dict(dealer_id='club',is_club=True),
                          dict(dealer_id='a',is_club=False),dict(dealer_id='b',is_club=False)])
    visits = plan.copy()
    visits['status'] = ['completed','completed','missed_dealer_unavailable','missed_weather']
    daily = plan[['date','so_id']].copy()
    daily['planned'] = 1
    daily['completed'] = [1,1,0,0]
    events = pd.DataFrame([dict(dealer_id='a',type='drop',onset='2026-11-01'),
                           dict(dealer_id='outside_focus',type='drop',onset='2026-11-01')])
    summary = dict(simulated_adherence=.5,initial_plan_adherence=.5,focus_coverage=.3333,
        club_cadence_met=0,drop_response_rate=.5,visits_completed=2,visits_planned_day_of=4,
        focus_dealers=3,club_dealers_in_focus=1,drop_events=2,drops_visited_within_7_days=1,
        missed_by_reason=dict(missed_dealer_unavailable=1,missed_weather=1),replan_failures=0)
    return plan,focus,visits,daily,events,summary


class EvaluationTests(unittest.TestCase):
    def test_club_needs_two_visits_and_drop_denominator_includes_nonfocus(self):
        values,events = reconcile_static(*fixture())
        self.assertEqual(values['focus_coverage'],.3333)
        self.assertEqual(values['drop_response_rate'],.5)
        self.assertFalse(events.loc[events.dealer_id.eq('outside_focus'),'in_initial_focus'].iloc[0])

    def test_tampered_summary_rejected(self):
        args = list(fixture())
        args[-1]['simulated_adherence'] = 1
        with self.assertRaisesRegex(ValueError,'simulated_adherence'):
            reconcile_static(*args)

    def test_silently_ignored_or_duplicate_execution_rows_rejected(self):
        args = list(fixture())
        args[2] = args[2].iloc[:-1]
        with self.assertRaisesRegex(ValueError,'execution rows'):
            reconcile_static(*args)
        args = list(fixture())
        args[2] = pd.concat([args[2],args[2].iloc[:1]])
        with self.assertRaisesRegex(ValueError,'execution rows'):
            reconcile_static(*args)

    def test_daily_counts_must_reconcile(self):
        args = list(fixture())
        args[3].loc[0,'completed'] = 0
        with self.assertRaisesRegex(ValueError,'Daily totals'):
            reconcile_static(*args)

    def test_two_visits_can_cover_club_but_fail_cadence(self):
        plan,focus,visits,daily,events,summary = fixture()
        plan.loc[3,'date'] = visits.loc[3,'date'] = daily.loc[3,'date'] = '2026-11-07'
        visits.loc[3,'status'] = 'completed'
        daily.loc[3,'completed'] = 1
        summary.update(simulated_adherence=.75,initial_plan_adherence=.75,focus_coverage=.6667,
                       visits_completed=3,missed_by_reason=dict(missed_dealer_unavailable=1))
        values,_ = reconcile_static(plan,focus,visits,daily,events,summary)
        self.assertEqual(values['club_cadence_met'],0)
        self.assertEqual(values['focus_coverage'],.6667)

    def test_no_drop_events_are_unavailable_not_zero(self):
        args = list(fixture())
        args[4] = args[4].iloc[:0]
        args[-1].update(drop_events=0,drops_visited_within_7_days=0,drop_response_rate=None)
        values,_ = reconcile_static(*args)
        self.assertIsNone(values['drop_response_rate'])
        self.assertIsNone(ratio(0,0))


if __name__=='__main__':
    unittest.main()
