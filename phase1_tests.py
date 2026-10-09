"""HTTP integration tests. Run only against a fresh, isolated test database.

Start the API with MONGO_DB=candidate_phase1_test, then run this file.
Set TEST_BASE_URL and PHASE1_ALLOW_TEST_WRITES=1 to allow fixture writes.
No sample seed is needed. Test records remain until the disposable database is removed.
"""
import json
import os
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

BASE = os.getenv('TEST_BASE_URL')
IST = timezone(timedelta(hours=5, minutes=30))


def request(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


def milliseconds(day, clock):
    return int(datetime.fromisoformat(f'{day}T{clock}').replace(tzinfo=IST).timestamp() * 1000)


def employee(code, **changes):
    body = dict(emp_code=code, name='Test User', email='test@example.com',
                department='QA', joined_on='2026-01-01')
    body.update(changes)
    return body


class Phase1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BASE or os.getenv('PHASE1_ALLOW_TEST_WRITES') != '1':
            raise unittest.SkipTest(
                'Set TEST_BASE_URL and PHASE1_ALLOW_TEST_WRITES=1 only for a disposable API database')
        for code in ('EMP9901', 'EMP9902', 'EMP9903'):
            status, body = request('POST', '/employees', employee(code))
            if status != 201:
                raise AssertionError(f'Use a fresh test database: {status}, {body}')
        status, _ = request('POST', '/employees', employee('EMP9904', department='Night',
                             shift_start='22:00', shift_end='06:00'))
        assert status == 201

    def test_health(self):
        self.assertEqual(request('GET', '/health'), (200, {'status': 'ok'}))

    def test_employee_validation(self):
        for changes in ({'joined_on': '2026-02-30'}, {'joined_on': '20260101'},
                        {'joined_on': 'bad'}, {'email': 'a@b'}, {'email': 'a b@c.com'},
                        {'emp_code': 'BAD'}, {'shift_start': '09:30', 'shift_end': '09:30'}):
            with self.subTest(changes=changes):
                status, body = request('POST', '/employees', employee('EMP9910', **changes))
                self.assertEqual(status, 422)
                self.assertIn('detail', body)

    def test_duplicate_employee(self):
        self.assertEqual(request('POST', '/employees', employee('EMP9901')),
                         (409, {'detail': 'emp_code already exists'}))

    def test_employee_pages(self):
        status, body = request('GET', '/employees?department=QA&page_size=2')
        self.assertEqual(status, 200)
        self.assertEqual(body['total'], 3)
        self.assertEqual([e['emp_code'] for e in body['items']], ['EMP9901', 'EMP9902'])
        for item in body['items']:
            self.assertNotIn('_id', item)
            self.assertIsInstance(item['created_at'], int)
            self.assertEqual(item['created_at'] % 1000, 0)
        _, body = request('GET', '/employees?department=QA&page_size=2&page=2')
        self.assertEqual([e['emp_code'] for e in body['items']], ['EMP9903'])
        self.assertEqual(body['total'], 3)
        self.assertEqual(request('GET', '/employees?department=')[1]['total'], 0)

    def test_invalid_punches(self):
        for value in (None, 1783312500000.0, 1783312500, '1783312500000', True, 4102444800001):
            with self.subTest(value=value):
                status, body = request('POST', '/attendance/punch-in',
                                       {'emp_code': 'EMP9901', 'punched_at': value})
                self.assertEqual(status, 422)
                self.assertIn('detail', body)
        self.assertEqual(request('POST', '/attendance/punch-in', {'emp_code': 'EMP9999'}),
                         (404, {'detail': 'employee not found'}))
        self.assertEqual(request('POST', '/attendance/punch-in',
                                 {'emp_code': 'EMP9901', 'status': 'ABSENT'})[0], 422)

    def test_omitted_timestamp(self):
        status, body = request('POST', '/attendance/punch-in', {'emp_code': 'EMP9903'})
        self.assertEqual(status, 201)
        self.assertIsInstance(body['punch_in'], int)
        self.assertEqual(body['punch_in'] % 1000, 0)
        self.assertEqual(body['status'], 'PRESENT')

    def test_punch_boundaries_and_listing(self):
        for code, clock, late in (('EMP9901', '09:40:00.999', 0),
                                  ('EMP9902', '09:40:01', 10),
                                  ('EMP9903', '10:05:00', 35)):
            stamp = milliseconds('2026-08-04', clock)
            status, body = request('POST', '/attendance/punch-in',
                                   {'emp_code': code, 'punched_at': stamp, 'work_hours': 999})
            self.assertEqual(status, 201)
            self.assertEqual(body, dict(emp_code=code, date='2026-08-04', status='PRESENT',
                punch_in=stamp // 1000 * 1000, punch_out=None, work_hours=None,
                late_minutes=late, overtime_minutes=0, half_day=False, history=[]))
        stamp = milliseconds('2026-08-05', '05:50:00')
        self.assertEqual(request('POST', '/attendance/punch-in',
                         {'emp_code': 'EMP9904', 'punched_at': stamp})[1]['date'], '2026-08-04')
        self.assertEqual(request('POST', '/attendance/punch-in',
                         {'emp_code': 'EMP9901', 'punched_at': stamp})[0], 201)
        self.assertEqual(request('POST', '/attendance/punch-in',
                         {'emp_code': 'EMP9904', 'punched_at': stamp})[0], 409)
        status, body = request('GET', '/attendance?page_size=3&date_to=2026-08-05')
        self.assertEqual(status, 200)
        self.assertEqual(body['total'], 5)
        self.assertEqual([(r['date'], r['emp_code']) for r in body['items']],
                         [('2026-08-05', 'EMP9901'), ('2026-08-04', 'EMP9901'), ('2026-08-04', 'EMP9902')])
        _, body = request('GET', '/attendance?emp_code=EMP9901&date_to=2026-08-04')
        self.assertEqual(body['total'], 1)
        self.assertEqual(len(body['items']), 1)
        self.assertEqual(request('GET', '/attendance?emp_code=')[1]['total'], 0)

    def test_query_validation(self):
        for path in ('/employees?page=0', '/employees?page_size=101', '/attendance?page_size=0',
                     '/attendance?status=BAD', '/attendance?date_from=2026-02-30',
                     '/attendance?date_from=20260804', '/attendance?date_from=',
                     '/attendance?date_to=2026-02-30', '/attendance?date_to=20260804',
                     '/attendance?date_to=1783312500000', '/attendance?date_to=',
                     '/attendance?date_from=2026-08-05&date_to=2026-08-04'):
            with self.subTest(path=path):
                status, body = request('GET', path)
                self.assertEqual(status, 422)
                self.assertIn('detail', body)
                self.assertIsInstance(body['detail'], list)
                self.assertTrue(body['detail'])
                for error in body['detail']:
                    self.assertIsInstance(error, dict)
                    self.assertIsInstance(error['loc'], list)
                    self.assertIsInstance(error['msg'], str)
                    self.assertIsInstance(error['type'], str)

    def test_concurrent_inserts(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda _: request('POST', '/employees', employee('EMP9920', department='Race'))[0], range(4)))
        self.assertEqual(sorted(statuses), [201, 409, 409, 409])
        body = {'emp_code': 'EMP9920', 'punched_at': milliseconds('2026-09-01', '09:30:00')}
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda _: request('POST', '/attendance/punch-in', body)[0], range(4)))
        self.assertEqual(sorted(statuses), [201, 409, 409, 409])


if __name__ == '__main__':
    unittest.main(verbosity=2)
