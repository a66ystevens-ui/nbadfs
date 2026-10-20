import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from nbadfs.app import app
from nbadfs.contest import payouts_for_scores, select_portfolio
from nbadfs.demo import demo_players
from nbadfs.ingest import forecast_history, history_rows, parse_players, walk_forward
from nbadfs.models import Contest, Rules, RunRequest, SLOTS
from nbadfs.optimizer import solve, validate_lineup
from nbadfs.pipeline import run
from nbadfs.scoring import dk_points
from nbadfs.simulation import simulate

NOW = datetime(2026,10,20,16,tzinfo=timezone.utc)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.players = demo_players(NOW)

    def test_scoring_bonuses_and_turnovers(self):
        # Triple-double receives both DD (+1.5) and TD (+3).
        self.assertEqual(float(dk_points(30,4,10,10,2,1,4)),68)
        self.assertEqual(float(dk_points(10,0,10,0,0,0,0)),24)
        self.assertEqual(float(dk_points(0,0,0,0,0,0,2)),-1)

    def test_joint_rotations_and_reproducibility(self):
        scores, minutes = simulate(self.players,200,7)
        self.assertTrue(np.array_equal(scores,simulate(self.players,200,7)[0]))
        for t in {p.team for p in self.players}:
            idx = [i for i,p in enumerate(self.players) if p.team==t]
            np.testing.assert_allclose(minutes[:,idx].sum(axis=1),240)
        self.assertTrue((minutes <= 48+1e-8).all())
        self.players[0].play_probability=0
        scores,minutes = simulate(self.players,200,9)
        self.assertTrue((scores[:,0]==0).all())
        self.assertTrue((minutes[:,0]==0).all())

    def test_optimizer_rules_and_diversity(self):
        objective = np.array([p.minutes*p.fppm for p in self.players])
        rules = Rules(locks=['demo-0-0'],excludes=['demo-1-0'],max_from_team=3,min_salary=45000,min_unique=2)
        one = solve(self.players,objective,rules,as_of=NOW)
        two = solve(self.players,objective,rules,[one],as_of=NOW)
        validate_lineup(self.players,one,rules)
        self.assertIn(0,one)
        self.assertNotIn(10,one)
        self.assertLessEqual(len(set(one)&set(two)),6)
        with self.assertRaises(ValueError):
            solve(self.players,objective,Rules(locks=['missing']),as_of=NOW)

    def test_two_game_constraint_and_infeasibility(self):
        players = self.players[:20]
        with self.assertRaises(ValueError):
            solve(players,np.ones(20),Rules(),as_of=NOW)

    def test_tie_payouts_and_self_competition(self):
        contest = Contest(size=4,entry_fee=10,payouts=[20,10])
        payout,_,_ = payouts_for_scores(np.array([[100.,100.]]),np.array([[100.,90.]]),contest)
        np.testing.assert_allclose(payout,[[10,10]])
        # A four-way tie splits all prizes across all entries, including zero-paid ranks.
        payout,_,_ = payouts_for_scores(np.array([[100.]]),np.array([[100.,100.,100.]]),contest)
        self.assertEqual(payout[0,0],7.5)

    def test_exposure_integer_limits(self):
        pool = [list(range(8)),list(range(8,16)),list(range(16,24))]
        metrics = [{'ev':v} for v in [5,4,3]]
        chosen = select_portfolio(self.players,pool,metrics,2,.5,'gpp',np.ones(60))
        self.assertEqual(set(chosen),{0,1})
        with self.assertRaises(ValueError):
            select_portfolio(self.players,pool,metrics,2,.2,'gpp',np.ones(60))

    def test_history_as_of_and_walkforward(self):
        rows = []
        for i in range(6):
            game = NOW+timedelta(days=i)
            rows.append(dict(player_id='a',game_date=game,available_at=game+timedelta(hours=3),minutes=30.,dk_points=33.))
        base = forecast_history(rows,'a',NOW+timedelta(days=4))
        rows[4]['dk_points']=10000
        self.assertEqual(base,forecast_history(rows,'a',NOW+timedelta(days=4)))
        metrics = walk_forward(rows[:4])
        self.assertEqual(metrics['evaluated_rows'],1)
        self.assertEqual(metrics['minutes_mae'],0)
        with self.assertRaises(ValueError):
            history_rows('player_id,game_date,available_at,minutes,dk_points\na,2026-10-20T20:00:00Z,2026-10-20T19:00:00Z,30,40')

    def test_import_validation(self):
        csv = 'id,name,team,game,positions,salary,start,minutes,fppm\na,Name,ATL,ATL-BOS,PG/SG,6000,2026-10-20T22:00:00Z,30,1.1'
        self.assertEqual(parse_players(csv,NOW)[0].positions,['PG','SG'])
        with self.assertRaises(ValueError):
            parse_players(csv+'\n'+csv.splitlines()[1],NOW)
        with self.assertRaises(ValueError):
            RunRequest(as_of=datetime(2026,1,1))
        with self.assertRaises(ValueError):
            RunRequest(current_lineup={},entries=1)

    def test_reject_future_snapshot(self):
        self.players[0].source_published_at=NOW+timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError,'not available'):
            run(RunRequest(players=self.players,as_of=NOW,candidates=1,entries=1,simulations=100))

    def test_late_swap_preserves_exact_started_slots(self):
        objective = np.array([p.minutes*p.fppm for p in self.players])
        original = solve(self.players,objective,Rules(),as_of=NOW)
        current = dict(zip(SLOTS,[self.players[i].id for i in original]))
        after = NOW+timedelta(hours=2,minutes=1)
        request = RunRequest(players=self.players,as_of=after,current_lineup=current,
            candidates=2,entries=1,simulations=100,contest=Contest(size=6,entry_fee=10,payouts=[40]))
        result = run(request)
        for slot,i in zip(SLOTS,original):
            if self.players[i].start <= after:
                self.assertEqual(result['lineups'][0]['slots'][slot],self.players[i].id)
        request.contest.late_swap=False
        with self.assertRaisesRegex(ValueError,'does not support'):
            run(request)

    def test_api_pipeline_persistence_export_and_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ',{'NBADFS_DB':str(Path(directory)/'test.sqlite')}):
            client = TestClient(app)
            self.assertEqual(client.get('/api/health').json()['status'],'ok')
            self.assertEqual(client.get('/').status_code,200)
            response = client.post('/api/run',json={'as_of':NOW.isoformat(),'entries':2,'candidates':5,'simulations':100,
                'contest':{'size':11,'entry_fee':10,'payouts':[50,30,20]}})
            self.assertEqual(response.status_code,200,response.text)
            result = response.json()
            self.assertTrue(result['synthetic'])
            self.assertEqual(len(result['lineups']),2)
            record = client.get('/api/runs/'+result['run_id']).json()
            self.assertEqual(len(record['request']['players']),60)
            self.assertEqual(record['request']['seed'],42)
            exported = client.get('/api/runs/'+result['run_id']+'/export')
            self.assertEqual(len(exported.text.splitlines()),3)
            self.assertEqual(exported.text.splitlines()[0],','.join(SLOTS))
            self.assertEqual(client.get('/api/runs/no-such-run/export').status_code,404)
            bad = client.post('/api/run',json={'entries':20,'candidates':1})
            self.assertEqual(bad.status_code,422)


if __name__ == '__main__':
    unittest.main()
