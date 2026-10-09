"""Phase 3 HTTP and real query-plan checks. Run with verify_phase3.py.

Fixtures are written only after explicit disposable-test settings and an API/database
identity check. The runner removes its own container, not individual collections.
"""
import json
import os
import unittest
from datetime import datetime, timezone, date, timedelta
from urllib.parse import urlencode

from pymongo import MongoClient
from phase2_tests import request


class Phase3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv('PHASE3_ALLOW_TEST_WRITES') != '1' or not all(os.getenv(key) for key in (
                'TEST_BASE_URL', 'TEST_MONGO_URI', 'TEST_MONGO_DB')):
            raise unittest.SkipTest('Use verify_phase3.py with a disposable MongoDB')
        cls.client = MongoClient(os.environ['TEST_MONGO_URI'], tz_aware=True)
        cls.addClassCleanup(cls.client.close)
        cls.db = cls.client[os.environ['TEST_MONGO_DB']]
        status, body = request('POST', '/employees', dict(emp_code='EMP83000', name='Analytics A',
            email='p3@example.com', department='P3', joined_on='2030-04-01'))
        assert status == 201, body
        assert cls.db.employees.find_one({'emp_code': 'EMP83000'}) is not None
        now = datetime.now(timezone.utc).replace(microsecond=0)
        people = [('EMP83001', 'P3', '2030-04-01'), ('EMP83002', 'P3', '2030-04-15'),
                  ('EMP83003', 'P3', '2030-04-30'), ('EMP83004', 'P3', '2030-05-01'),
                  ('EMP83005', 'P3Empty', '2030-04-01'), ('EMP83006', 'P3Other', '2030-04-01'),
                  ('EMP83007', 'P3Future', '2030-05-01'), ('EMP83008', 'P3Round', '2030-04-01')]
        cls.db.employees.insert_many([dict(emp_code=code, name=code, department=dept,
            joined_on=joined, email='p3@example.com', shift_start='09:30', shift_end='18:30',
            created_at=now) for code, dept, joined in people])
        def log(code, day, status='PRESENT', **values):
            return dict(emp_code=code, date='2030-04-' + day, status=status, **values)
        cls.db.attendance_logs.insert_many([
            log('EMP83000', '01', work_hours=8.12, late_minutes=30, overtime_minutes=40),
            log('EMP83000', '02', 'WFH', work_hours=3.51, half_day=True, late_minutes=10),
            log('EMP83000', '03', 'LEAVE'), log('EMP83000', '04', 'ABSENT'),
            log('EMP83000', '06', 'ON_DUTY', work_hours=9.12, late_minutes=60, overtime_minutes=30),
            log('EMP83001', '01', work_hours=8.13, late_minutes=50),
            log('EMP83001', '02', work_hours=None), log('EMP83001', '07', 'LEAVE'),
            log('EMP83002', '15', 'ON_DUTY', work_hours=9, late_minutes=50),
            log('EMP83003', '30', late_minutes=20),
            log('EMP83006', '01', work_hours=8, late_minutes=80),
            log('EMP83008', '01', work_hours=8.12), log('EMP83008', '02', work_hours=8.13),
            log('EMP830099', '01', late_minutes=1000),
        ])

    def get(self, path, **parameters):
        status, body = request('GET', path + '?' + urlencode(parameters))
        self.assertEqual(status, 200, body)
        if not path.startswith('/admin/explain/'):
            self.assertNotIn('_id', json.dumps(body))
        return body

    def monthly(self, code='EMP83000', month='2030-04'):
        return self.get('/analytics/employees/' + code + '/monthly', month=month)

    def summary(self, department='P3', month='2030-04'):
        return self.get('/analytics/departments/summary', month=month, department=department)['items']

    def trend(self, start='2030-04-01', end='2030-04-07', department='P3'):
        return self.get('/analytics/departments/' + department + '/trend', **{'from':start, 'to':end})['items']

    def test_monthly_stored_values_weekends_and_legacy(self):
        self.assertEqual(self.monthly(), dict(emp_code='EMP83000', month='2030-04', working_days=22,
            present_days=1.5, leave_days=1, late_count=3, total_late_minutes=100,
            total_overtime_minutes=70, attendance_pct=6.82))
        self.assertEqual(self.monthly('EMP83001')['present_days'], 2)
        self.assertEqual(self.monthly('EMP83001')['leave_days'], 1)

    def test_joining_boundaries_and_no_logs(self):
        self.assertEqual(self.monthly('EMP83002')['working_days'], 12)
        self.assertEqual(self.monthly('EMP83003')['working_days'], 1)
        self.assertEqual(self.monthly('EMP83004')['working_days'], 0)
        self.assertIsNone(self.monthly('EMP83004')['attendance_pct'])
        self.assertEqual(self.monthly('EMP83005')['present_days'], 0)
        self.assertEqual(self.monthly('EMP83005')['attendance_pct'], 0)
        self.assertEqual(self.monthly('EMP83000', '2030-03')['working_days'], 0)

    def test_department_weighted_average_and_headcount(self):
        self.assertEqual(self.summary(), [dict(department='P3', headcount=4, present_days=5.5,
            avg_work_hours=7.58, late_count=6, total_late_minutes=220, leave_count=2, on_duty_count=2)])
        self.assertEqual(self.summary('P3Empty'), [dict(department='P3Empty', headcount=1, present_days=0,
            avg_work_hours=None, late_count=0, total_late_minutes=0, leave_count=0, on_duty_count=0)])
        self.assertEqual(self.summary('P3Future'), [])
        self.assertEqual(self.summary('missing'), [])

    def test_trend_half_up_rate_and_moving_average(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.db.employees.insert_many([dict(emp_code=f'EMP84{i:04}', name='Rounding',
            department='P3RateRound', joined_on='2030-04-01', email='round@example.com',
            shift_start='09:30', shift_end='18:30', created_at=now) for i in range(32)])
        self.db.attendance_logs.insert_one(dict(emp_code='EMP840000', date='2030-04-01', status='PRESENT'))
        rows = self.trend('2030-04-01', '2030-04-02', 'P3RateRound')
        self.assertEqual(rows[0]['attendance_rate'], .0313)  # 1 / 32 = .03125
        self.assertEqual(rows[1]['moving_avg_7d'], .0157)  # mean of rounded .0313 and zero
        self.db.employees.insert_many([dict(emp_code=f'EMP85{i:04}', name='Rounding',
            department='P3MovingRound', joined_on='2030-04-01', email='round@example.com',
            shift_start='09:30', shift_end='18:30', created_at=now) for i in range(16)])
        self.db.attendance_logs.insert_one(dict(emp_code='EMP850000', date='2030-04-01', status='PRESENT'))
        rows = self.trend('2030-04-01', '2030-04-02', 'P3MovingRound')
        self.assertEqual(rows[1]['moving_avg_7d'], .0313)  # (.0625 + 0) / 2

    def test_half_up_average(self):
        self.assertEqual(self.summary('P3Round')[0]['avg_work_hours'], 8.13)

    def test_summary_sorted_and_empty_period(self):
        rows = self.get('/analytics/departments/summary', month='2030-04')['items']
        self.assertEqual([r['department'] for r in rows], sorted(r['department'] for r in rows))
        self.assertEqual(self.get('/analytics/departments/summary', month='1900-01')['items'], [])
        self.assertEqual(self.get('/analytics/leaderboard/late', month='1900-01')['items'], [])

    def test_competition_ranking_and_cutoff(self):
        rows = self.get('/analytics/leaderboard/late', month='2030-04', department='P3', limit=2)['items']
        self.assertEqual([(r['emp_code'], r['rank'], r['total_late_minutes']) for r in rows],
            [('EMP83000', 1, 100), ('EMP83001', 2, 50), ('EMP83002', 2, 50)])
        rows = self.get('/analytics/leaderboard/late', month='2030-04', department='P3', limit=4)['items']
        self.assertEqual([r['rank'] for r in rows], [1, 2, 2, 4])
        rows = self.get('/analytics/leaderboard/late', month='2030-04')['items']
        self.assertEqual([r['rank'] for r in rows], [1, 2, 3, 3, 5])
        self.assertNotIn('EMP830099', [r['emp_code'] for r in rows])
        self.assertEqual(self.get('/analytics/leaderboard/late', month='2030-04', department='missing')['items'], [])

    def test_trend_calendar_weekends_rates_and_window(self):
        rows = self.trend()
        self.assertEqual([r['date'] for r in rows], ['2030-04-0' + str(i) for i in range(1, 8)])
        self.assertEqual([r['headcount'] for r in rows], [2] * 7)
        self.assertEqual([r['present_count'] for r in rows], [2, 1.5, 0, 0, 0, 1, 0])
        self.assertEqual([r['attendance_rate'] for r in rows], [1, .75, 0, 0, 0, None, None])
        self.assertEqual([r['moving_avg_7d'] for r in rows], [1, .875, .5833, .4375, .35, .35, .35])
        self.assertEqual(rows[5]['late_count'], 1)
        self.assertFalse(rows[5]['is_working_day'])
        self.assertEqual(self.trend('2030-04-02', '2030-04-02')[0]['moving_avg_7d'], .75)
        rows = self.trend('2030-04-01', '2030-04-08')
        self.assertEqual(rows[-1]['moving_avg_7d'], .15)

    def test_trend_headcount_join_dates(self):
        self.assertEqual([r['headcount'] for r in self.trend('2030-04-14', '2030-04-15')], [2, 3])
        self.assertEqual([r['headcount'] for r in self.trend('2030-04-29', '2030-05-01')], [3, 4, 5])
        rows = self.trend(department='P3Empty')
        self.assertEqual([r['attendance_rate'] for r in rows], [0, 0, 0, 0, 0, None, None])
        rows = self.trend(department='P3Future')
        self.assertEqual([r['headcount'] for r in rows], [0] * 7)
        self.assertTrue(all(r['attendance_rate'] is None and r['moving_avg_7d'] is None for r in rows))
        rows = self.trend('2030-04-06', '2030-04-07', 'P3Empty')
        self.assertTrue(all(r['moving_avg_7d'] is None for r in rows))
        self.assertEqual(len(self.trend('2030-01-01', '2030-04-02')), 92)

    def test_invalid_parameters_and_missing_entities(self):
        cases = [('/analytics/employees/missing/monthly?month=2030-04', 404),
            ('/analytics/departments/missing/trend?from=2030-04-01&to=2030-04-01', 404),
            ('/analytics/departments/P3/trend?from=2030-04-02&to=2030-04-01', 422),
            ('/analytics/departments/P3/trend?from=2030-01-01&to=2030-04-03', 422),
            ('/analytics/departments/P3/trend?from=2030-02-30&to=2030-04-01', 422),
            ('/analytics/departments/P3/trend?from=20300401&to=2030-04-01', 422),
            ('/analytics/departments/P3/trend?from=2030-04-01', 422),
            ('/analytics/leaderboard/late?month=2030-04&limit=0', 422),
            ('/analytics/leaderboard/late?month=2030-04&limit=51', 422),
            ('/analytics/leaderboard/late?month=2030-04&limit=1.5', 422)]
        for path in ('/analytics/employees/EMP83000/monthly', '/analytics/departments/summary', '/analytics/leaderboard/late'):
            cases.extend((path + suffix, 422) for suffix in ('', '?month=2030-13', '?month=0000-01', '?month=2030-4'))
        for path, expected in cases:
            with self.subTest(path=path):
                status, body = request('GET', path)
                self.assertEqual(status, expected, body)
                self.assertIsInstance(body['detail'], list if expected == 422 else str)

    def test_z_large_dataset_plans_and_read_only_reports(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.db.employees.insert_many([dict(emp_code=f'EMP{900000+i}', name='Scale fixture',
            department='P3Scale', joined_on='2031-01-01', email='scale@example.com',
            shift_start='09:30', shift_end='18:30', created_at=now) for i in range(1000)])
        for batch in range(10):
            logs = [dict(emp_code=f'EMP{900000+i}', date=(date(2031, 1, 1) + timedelta(days=offset)).isoformat(),
                status='PRESENT', work_hours=8.12, late_minutes=11)
                for i in range(batch * 100, (batch + 1) * 100) for offset in range(100)]
            self.db.attendance_logs.insert_many(logs)
        before = (self.db.employees.count_documents({}), self.db.attendance_logs.count_documents({}))
        cases = [
            ('attendance_list', dict(date_from='2031-04-01', date_to='2031-04-10', page=2, page_size=20)),
            ('attendance_list', dict()),
            ('employee_monthly', dict(emp_code='EMP900000', month='2031-04')),
            ('department_summary', dict(month='2031-04')),
            ('department_summary', dict(month='2031-04', department='P3Scale')),
            ('late_leaderboard', dict(month='2031-04', department='P3Scale', limit=1)),
            ('department_trend', dict(department='P3Scale', **{'from':'2031-04-01', 'to':'2031-04-07'})),
        ]
        for endpoint, parameters in cases:
            with self.subTest(endpoint=endpoint, parameters=parameters):
                body = self.get('/admin/explain/' + endpoint, **parameters)
                plan = json.dumps(body['explain'])
                self.assertIn('IXSCAN', plan)
                self.assertNotIn('COLLSCAN', plan)
                def check_scans(value):
                    if isinstance(value, dict):
                        if 'collectionScans' in value: self.assertEqual(value['collectionScans'], 0)
                        for child in value.values(): check_scans(child)
                    elif isinstance(value, list):
                        for child in value: check_scans(child)
                check_scans(body['explain'])
        monthly = self.monthly('EMP900000', '2031-04')
        self.assertEqual(monthly['late_count'], 10)
        self.assertEqual(monthly['present_days'], 8)
        summary = self.summary('P3Scale', '2031-04')[0]
        self.assertEqual(summary['headcount'], 1000)
        self.assertEqual(summary['present_days'], 8000)
        self.assertEqual(summary['avg_work_hours'], 8.12)
        rows = self.get('/analytics/leaderboard/late', month='2031-04', department='P3Scale', limit=1)['items']
        self.assertEqual(len(rows), 1000)
        self.assertTrue(all(row['rank'] == 1 for row in rows))
        self.assertEqual([row['emp_code'] for row in rows], sorted(row['emp_code'] for row in rows))
        rows = self.trend('2031-04-01', '2031-04-07', 'P3Scale')
        self.assertTrue(all(row['headcount'] == 1000 and row['present_count'] == 1000 for row in rows))
        self.assertEqual(before, (self.db.employees.count_documents({}), self.db.attendance_logs.count_documents({})))
        print('100,000-log fixture: 7 real executionStats plans indexed; no root or lookup collection scans.')

    def test_explain_required_parameters(self):
        for endpoint in ('employee_monthly', 'department_summary', 'late_leaderboard', 'department_trend', 'unknown'):
            with self.subTest(endpoint=endpoint):
                status, body = request('GET', '/admin/explain/' + endpoint)
                self.assertEqual(status, 422, body)
        for invalid in ('2030-13', '2030-4', 'bad', '0000-01'):
            self.assertEqual(request('GET', '/admin/explain/department_summary?month=' + invalid)[0], 422)
        for query in ('department=P3&from=2030-04-01', 'department=P3&from=2030-04-02&to=2030-04-01',
                      'department=P3&from=2030-01-01&to=2030-04-03'):
            self.assertEqual(request('GET', '/admin/explain/department_trend?' + query)[0], 422)

    def test_real_explain_operations_and_plans(self):
        from app.main import analytics_operation, attendance_find_operation
        cases = [
            ('attendance_list', dict(emp_code='EMP83000', date_from='2030-04-01', date_to='2030-04-30', status='PRESENT', page=2, page_size=1)),
            ('attendance_list', dict(date_from='2030-04-01', date_to='2030-04-30')),
            ('employee_monthly', dict(emp_code='EMP83000', month='2030-04')),
            ('department_summary', dict(month='2030-04')),
            ('department_summary', dict(month='2030-04', department='P3')),
            ('late_leaderboard', dict(month='2030-04', department='P3', limit=2)),
            ('department_trend', dict(department='P3', **{'from':'2030-04-01', 'to':'2030-04-07'})),
        ]
        for endpoint, parameters in cases:
            with self.subTest(endpoint=endpoint, parameters=parameters):
                body = self.get('/admin/explain/' + endpoint, **parameters)
                self.assertEqual(set(body), {'endpoint', 'collection', 'explain'})
                explanation = body['explain']
                self.assertEqual(explanation['ok'], 1)
                self.assertIn('executionStats', json.dumps(explanation))
                self.assertIn('IXSCAN', json.dumps(explanation))
                self.assertNotIn('COLLSCAN', json.dumps(explanation))
                # $lookup reports its own collection scans separately from the root plan.
                def check_scans(value):
                    if isinstance(value, dict):
                        if 'collectionScans' in value:
                            self.assertEqual(value['collectionScans'], 0)
                        for child in value.values(): check_scans(child)
                    elif isinstance(value, list):
                        for child in value: check_scans(child)
                check_scans(explanation)
                if endpoint == 'attendance_list':
                    expected = attendance_find_operation(parameters.get('emp_code'), parameters.get('date_from'),
                        parameters.get('date_to'), parameters.get('status'), parameters.get('page', 1), parameters.get('page_size', 20))
                else:
                    args = dict(parameters)
                    if 'from' in args: args['start'] = args.pop('from')
                    if 'to' in args: args['end'] = args.pop('to')
                    collection, pipeline, hint = analytics_operation(endpoint, **args)
                    expected = dict(aggregate=collection, pipeline=pipeline, cursor={}, hint=hint)
                # Compare the operation received by MongoDB, allowing Decimal128's JSON encoding.
                from bson import json_util
                expected = json.loads(json_util.dumps(expected))
                command = explanation['command']
                for key, value in expected.items(): self.assertEqual(command[key], value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
