import itertools
import unittest
from datetime import datetime,timedelta

from pjp.planning import club_dates, clock_text, schedule_officer
from pjp.routing import RouteSolver


MODEL = dict(day_minutes=540,demand_generation_minutes=80,dealer_call_minutes=25,
             road_factor=1.35,speed_kmh={'metro':18,'upcountry':30},
             unknown_location_travel_minutes={'metro':25,'upcountry':45})


def record(lat=None,lon=None,score=1,club=False):
    return dict(lat=lat,lon=lon,priority_score=score,is_club=club,has_location=lat is not None)


class RoutingTests(unittest.TestCase):
    def test_exact_matches_exhaustive_open_path(self):
        records = {'a':record(12.99,77.65),'b':record(12.94,77.59),
                   'c':record(13.02,77.54),'d':record(12.92,77.61)}
        solver = RouteSolver(records,(12.97,77.59),'metro',MODEL)
        travel,order = solver.route(records)
        brute = min(sum(solver.legs(p)) for p in itertools.permutations(records))
        self.assertAlmostEqual(travel,brute)
        self.assertEqual(set(order),set(records))

    def test_unknown_preserves_position_and_can_go_first(self):
        records = {'a':record(13,77.6),'b':record(13.01,77.61),'u':record(score=99)}
        solver = RouteSolver(records,(12.97,77.59),'metro',MODEL)
        known,_ = solver.route(['a','b'])
        total,order = solver.route(records)
        self.assertEqual(order[0],'u')
        self.assertAlmostEqual(total,known+25)
        self.assertAlmostEqual(sum(solver.legs(order)),total)

    def test_five_unknown_upcountry_stops_exceed_rain_budget(self):
        records = {str(i):record() for i in range(5)}
        solver = RouteSolver(records,(15,75),'upcountry',MODEL)
        travel,_ = solver.route(records)
        self.assertGreater(travel*1.5+5*25,460)

    def test_route_rejects_duplicate_and_oversize(self):
        solver = RouteSolver({'a':record()},(12,77),'metro',MODEL)
        with self.assertRaises(ValueError):
            solver.route(['a','a'])
        with self.assertRaises(ValueError):
            solver.route(['a']*7)


class CalendarTests(unittest.TestCase):
    def test_clubs_get_fourteen_day_gap_and_no_sundays(self):
        days = [datetime(2026,11,1)+timedelta(days=i) for i in range(30)
                if (datetime(2026,11,1)+timedelta(days=i)).weekday()!=6]
        pairs = club_dates(days,18)
        self.assertEqual(len(pairs),18)
        for a,b in pairs:
            self.assertEqual((days[b]-days[a]).days,14)

    def test_impossible_cadence_raises(self):
        with self.assertRaises(ValueError):
            club_dates([datetime(2026,11,2)],1)

    def test_impossible_routes_raise_without_dropping_accounts(self):
        records = {str(i):record(30,100) for i in range(12)}
        solver = RouteSolver(records,(12,77),'metro',MODEL)
        policy = dict(club_target_gap_days=14,random_seed=1,iterations_per_attempt=30,
                      max_attempts=1,priority_date_weight=1.5)
        with self.assertRaisesRegex(ValueError,'No feasible'):
            schedule_officer(records,[datetime(2026,11,2),datetime(2026,11,3)],solver,policy)
        self.assertEqual(len(records),12)

    def test_full_calendar_preserves_clubs_and_all_optional_visits(self):
        from collections import Counter
        days = [datetime(2026,11,1)+timedelta(days=i) for i in range(30)
                if (datetime(2026,11,1)+timedelta(days=i)).weekday()!=6]
        records = {str(i):record(score=149-i) for i in range(149)}
        for r in records.values():
            r['priority_score'] /= 2
        records['0']['is_club'] = True
        solver = RouteSolver(records,(12,77),'metro',MODEL)
        policy = dict(club_target_gap_days=14,random_seed=1005,iterations_per_attempt=200,
                      max_attempts=1,priority_date_weight=1.5)
        groups = schedule_officer(records,days,solver,policy)
        repeated = schedule_officer(records,days,solver,policy)
        self.assertEqual(groups,repeated)
        self.assertTrue(all(len(row)==6 for row in groups))
        self.assertEqual(Counter(k for row in groups for k in row),
                         Counter({k:2 if r['is_club'] else 1 for k,r in records.items()}))
        dates = [d for d,row in zip(days,groups) if '0' in row]
        self.assertEqual((dates[1]-dates[0]).days,14)

    def test_arrival_rounding(self):
        self.assertEqual(clock_text(570.01),'09:31')
        self.assertEqual(clock_text(570),'09:30')


if __name__=='__main__':
    unittest.main()
