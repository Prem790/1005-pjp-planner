import json
from pathlib import Path
import unittest

import pandas as pd

from pjp.replanning import observe, replan, prioritize_order
from pjp.routing import RouteSolver


def fixture(kind='metro',unknown=False):
    selection = json.loads(Path('config/selection.json').read_text())
    policy = json.loads(Path('config/replanning.json').read_text())
    records = {str(i):dict(so_id='SO-X',is_club=i==0,in_focus=True,
               priority_score=90-i,baseline_sheets=20,has_location=not unknown,
               lat=None if unknown else 12+.001*i,lon=None if unknown else 77) for i in range(10)}
    days = [str(d.date()) for d in pd.date_range('2026-11-01','2026-11-30') if d.dayofweek!=6]
    context = dict(records=records,policy=policy,officers={'SO-X':dict(start=[12,77],kind=kind)},
                   travel_model=selection['travel_model'],workdays=days)
    state = dict(today='2026-11-02',next_working_day='2026-11-03',
        completed_visits_month_to_date=[],sales_today=[],visits_today=[],
        weather_forecast_next_working_day={'SO-X':'clear'},rules=dict(dealer_visits_per_day=6,
        disrupted_visits_per_day=5,max_visits_per_dealer_month=2,club_min_gap_days=10))
    plan = pd.DataFrame([dict(date='2026-11-16',so_id='SO-X',sequence=1,account_id='0')])
    return context,state,plan


class ReplanningTests(unittest.TestCase):
    def test_missed_club_restored_and_spaced(self):
        c,s,p = fixture()
        s['visits_today'] = [dict(account_id='0',status='missed_dealer_unavailable')]
        output,_,audit = replan(c,s,p,{})
        dates = sorted(pd.to_datetime(output.loc[output.account_id.eq('0'),'date']))
        self.assertEqual(len(dates),2)
        self.assertGreaterEqual((dates[1]-dates[0]).days,10)
        self.assertFalse(audit['protected_club_shortfalls'])

    def test_five_unknown_upcountry_rain_calls_are_not_feasible(self):
        c,s,p = fixture('upcountry',True)
        s['weather_forecast_next_working_day']['SO-X'] = 'rain'
        output,_,_ = replan(c,s,p,{})
        tomorrow = output.loc[output.date.eq('2026-11-03')]
        self.assertLessEqual(len(tomorrow),4)
        self.assertTrue((tomorrow.travel_min_from_previous==67.5).all())

    def test_zero_filled_sales_detect_persistent_strong_drop(self):
        c,s,_ = fixture()
        s['today'] = '2026-11-04'
        memory = dict(sales={'2026-11-02':{},'2026-11-03':{}})
        signals = observe(c,s,memory)
        self.assertEqual(signals['1']['type'],'drop')
        self.assertGreater(signals['1']['log_evidence'],2.3)

    def test_quiet_low_volume_days_do_not_imply_drop(self):
        c,s,_ = fixture()
        c['records']['1']['baseline_sheets'] = 1
        s['today'] = '2026-11-04'
        memory = dict(sales={'2026-11-02':{},'2026-11-03':{}})
        self.assertNotIn('1',observe(c,s,memory))

    def test_completed_twice_is_never_scheduled_again(self):
        c,s,p = fixture()
        s['today'],s['next_working_day'] = '2026-11-16','2026-11-17'
        s['completed_visits_month_to_date'] = [dict(date='2026-11-02',account_id='1'),dict(date='2026-11-10',account_id='1')]
        memory = dict(sales={'2026-11-13':{},'2026-11-14':{}})
        output,_,_ = replan(c,s,p,memory)
        self.assertNotIn('1',set(output.account_id))

    def test_low_confidence_repeat_cannot_crowd_first_visit(self):
        c,s,p = fixture()
        s['today'],s['next_working_day'] = '2026-11-04','2026-11-05'
        s['completed_visits_month_to_date'] = [dict(date='2026-11-02',account_id='1')]
        memory = dict(sales={'2026-11-02':{},'2026-11-03':{}},
                      drop_episodes={'1':dict(detected_on='2026-11-03',log_evidence=2.5)})
        c['records']['1']['baseline_sheets'] = 1
        output,_,_ = replan(c,s,p,memory)
        self.assertNotIn('1',set(output.account_id))

    def test_hike_deferred_without_waiving_obligation(self):
        c,s,p = fixture()
        s['today'],s['next_working_day'] = '2026-11-04','2026-11-05'
        s['sales_today'] = [dict(dealer_id='1',sheets=80)]
        memory = dict(sales={'2026-11-02':{'1':80},'2026-11-03':{'1':80}})
        output,memory,audit = replan(c,s,p,memory)
        self.assertEqual(audit['signals']['1']['type'],'hike')
        self.assertTrue(output.loc[output.account_id.eq('1'),'date'].ge(memory['hike_holds']['1']).all())
        self.assertEqual(sum(output.account_id.eq('1')),1)

    def test_priority_order_keeps_urgent_stop_first_when_feasible(self):
        c,_,_ = fixture()
        records = {k:c['records'][k] for k in ['1','2','3']}
        solver = RouteSolver(records,[12,77],'metro',c['travel_model'])
        order = prioritize_order(solver,records,{'1':0,'2':0,'3':5},1,460)
        self.assertEqual(order[0],'3')
        self.assertLessEqual(sum(solver.legs(order))+75,460)

    def test_wrong_owner_rejected(self):
        c,s,p = fixture()
        c['records']['0']['so_id'] = 'OTHER'
        with self.assertRaisesRegex(ValueError,'ownership'):
            replan(c,s,p,{})

    def test_contract_default_memory_persists_between_evenings(self):
        import tempfile
        from pjp.replanning import run_replan
        c,s,p = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context,state,plan,out = [root/name for name in ['context.json','state.json','plan.csv','next.csv']]
            context.write_text(json.dumps(c))
            state.write_text(json.dumps(s))
            p.to_csv(plan,index=False)
            run_replan(state,plan,out,context)
            s['today'],s['next_working_day'] = '2026-11-03','2026-11-04'
            state.write_text(json.dumps(s))
            run_replan(state,plan,out,context)
            memory = json.loads((root/'pjp_memory.json').read_text())
            self.assertEqual(sorted(memory['sales']),['2026-11-02','2026-11-03'])
            self.assertEqual(len(list((root/'replan_logs').glob('*.json'))),2)

    def test_observations_cannot_go_backwards(self):
        c,s,_ = fixture()
        with self.assertRaisesRegex(ValueError,'later day'):
            observe(c,s,{'last_today':'2026-11-03'})


if __name__=='__main__':
    unittest.main()
