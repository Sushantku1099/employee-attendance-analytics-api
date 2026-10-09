"""HTTP tests for Phase 2. Use only an explicitly disposable API/database.

Requires TEST_BASE_URL, PHASE2_ALLOW_TEST_WRITES=1, TEST_MONGO_URI and TEST_MONGO_DB.
The MongoDB settings must point to the same disposable database as the API.
"""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

BASE = os.getenv('TEST_BASE_URL')
IST = timezone(timedelta(hours=5, minutes=30))


def request(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def stamp(clock, day='2026-08-04'):
    return int(datetime.fromisoformat(day + 'T' + clock).replace(tzinfo=IST).timestamp() * 1000)


class Phase2Tests(unittest.TestCase):
    number = 7000

    @classmethod
    def setUpClass(cls):
        if not BASE or os.getenv('PHASE2_ALLOW_TEST_WRITES') != '1':
            raise unittest.SkipTest('Set test URL and PHASE2_ALLOW_TEST_WRITES=1 for a disposable API')
        if not os.getenv('TEST_MONGO_URI') or not os.getenv('TEST_MONGO_DB'):
            raise unittest.SkipTest('Set explicit disposable MongoDB fixture settings')
        cls.client = MongoClient(os.environ['TEST_MONGO_URI'], tz_aware=True, serverSelectionTimeoutMS=2000)
        cls.addClassCleanup(cls.client.close)
        cls.db = cls.client[os.environ['TEST_MONGO_DB']]

    def setUp(self):
        type(self).number += 1
        self.code = f'EMP{self.number}'
        self.day = '2026-08-04'
        status, body = request('POST', '/employees', dict(emp_code=self.code, name='Phase Two',
            email='phase2@example.com', department='Phase2', joined_on='2026-01-01'))
        self.assertEqual(status, 201, body)
        # Refuse mismatched API/database settings before direct fixture writes.
        self.assertIsNotNone(self.db.employees.find_one({'emp_code': self.code}))

    def punch_in(self, clock='09:30:00', day=None):
        status, body = request('POST', '/attendance/punch-in',
            {'emp_code': self.code, 'punched_at': stamp(clock, day or self.day)})
        self.assertEqual(status, 201, body)
        return body

    def punch_out(self, clock='19:00:00', day=None):
        return request('POST', '/attendance/punch-out',
            {'emp_code': self.code, 'punched_at': stamp(clock, day or self.day)})

    def correct(self, **changes):
        body = dict(reason='Clock was wrong', regularized_by='hr.test')
        body.update(changes)
        return request('PATCH', f'/attendance/{self.code}/{self.day}', body)

    def current(self):
        status, body = request('GET', f'/attendance?emp_code={self.code}&date_from={self.day}&date_to={self.day}')
        self.assertEqual(status, 200)
        self.assertEqual(body['total'], 1)
        return body['items'][0]

    def test_punch_out_success_and_duplicate(self):
        self.punch_in('09:40:01.900')
        status, body = self.punch_out('19:00:00.999')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['punch_out'], stamp('19:00:00'))
        self.assertEqual(body['work_hours'], 9.33)
        self.assertEqual(body['late_minutes'], 10)
        self.assertEqual(body['overtime_minutes'], 30)
        self.assertFalse(body['half_day'])
        self.assertEqual(body['history'], [])
        self.assertNotIn('_id', body)
        self.assertEqual(self.punch_out()[0], 409)

    def test_overnight_punch_out(self):
        self.db.employees.update_one({'emp_code': self.code}, {'$set': {'shift_start':'22:00', 'shift_end':'06:00'}})
        self.punch_in('21:55:00')
        status, body = self.punch_out('06:40:00', '2026-08-05')
        self.assertEqual(status, 200, body)
        self.assertEqual((body['date'], body['work_hours'], body['overtime_minutes']), (self.day, 8.75, 40))

    def test_duration_boundaries(self):
        self.punch_in()
        self.assertEqual(self.punch_out('09:30:00.999')[0], 422)
        self.assertEqual(self.punch_out('09:30:01', '2026-08-05')[0], 422)
        status, body = self.punch_out('09:30:00', '2026-08-05')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['work_hours'], 24.0)

    def test_half_day_uses_rounded_hours(self):
        self.punch_in()
        status, body = self.punch_out('13:59:41')
        self.assertEqual(status, 200)
        self.assertEqual((body['work_hours'], body['half_day']), (4.49, True))
        status, body = self.correct(punch_out=stamp('13:59:42'))
        self.assertEqual(status, 200, body)
        self.assertEqual((body['work_hours'], body['half_day']), (4.5, False))

    def test_overtime_boundary(self):
        self.punch_in()
        status, body = self.punch_out('18:59:59')
        self.assertEqual(status, 200)
        self.assertEqual(body['overtime_minutes'], 0)
        status, body = self.correct(punch_out=stamp('19:00:00.999'))
        self.assertEqual(status, 200)
        self.assertEqual(body['overtime_minutes'], 30)
        self.assertEqual(body['punch_out'], stamp('19:00:00'))

    def test_unknown_and_missing_records(self):
        self.assertEqual(self.punch_out()[0], 404)
        self.assertEqual(self.correct(status='WFH')[0], 404)
        self.assertEqual(request('POST', '/attendance/punch-out', {'emp_code':'EMP7999'})[0], 404)
        self.assertEqual(request('PATCH', '/attendance/EMP7999/2026-08-04',
            {'reason':'Missing person', 'regularized_by':'HR', 'status':'WFH'})[0], 404)
        self.punch_in()
        self.assertEqual(self.punch_out('09:29:59')[0], 404)

    def test_invalid_timestamps_and_bodies(self):
        self.punch_in()
        for value in (None, True, '1783312500000', 1783312500000.0, 1783312500, 4102444800001):
            with self.subTest(value=value):
                self.assertEqual(request('POST', '/attendance/punch-out',
                    {'emp_code':self.code, 'punched_at':value})[0], 422)
                for field in ('punch_in', 'punch_out'):
                    self.assertEqual(self.correct(**{field:value})[0], 422)
        for changes in ({'status':None}, {'status':'BAD'}, {'reason':'tiny'}, {'regularized_by':''},
                        {'reason':'x' * 201}, {'regularized_by':'x' * 51}):
            with self.subTest(changes=changes):
                self.assertEqual(self.correct(**changes)[0], 422)
        self.assertEqual(request('POST', '/attendance/punch-out', {})[0], 422)
        self.assertEqual(request('PATCH', f'/attendance/{self.code}/{self.day}', {})[0], 422)
        self.assertIsNone(self.current()['punch_out'])

    def test_omitted_punch_out_timestamp(self):
        now = datetime.now(IST) - timedelta(seconds=3)
        self.day = now.date().isoformat()
        self.punch_in(now.strftime('%H:%M:%S'))
        status, body = request('POST', '/attendance/punch-out', {'emp_code': self.code})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['punch_out'] % 1000, 0)
        self.assertGreater(body['punch_out'], body['punch_in'])

    def test_latest_record_includes_closed_records(self):
        self.punch_in(day='2026-08-03')
        self.punch_in()
        self.assertEqual(self.punch_out()[0], 200)
        self.assertEqual(self.punch_out('20:00:00')[0], 409)
        earlier = self.db.attendance_logs.find_one({'emp_code':self.code, 'date':'2026-08-03'})
        self.assertIsNone(earlier['punch_out'])

    def test_latest_eligible_record_before_newer_punch(self):
        self.punch_in(day='2026-08-03')
        self.punch_in()
        status, body = self.punch_out('19:00:00', '2026-08-03')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['date'], '2026-08-03')
        self.assertIsNone(self.current()['punch_out'])

    def test_correction_history_and_recomputed_fields(self):
        self.punch_in('10:05:00')
        self.assertEqual(self.punch_out('19:10:00')[0], 200)
        status, body = self.correct(punch_in=stamp('09:28:00.900'), work_hours=999)
        self.assertEqual(status, 200, body)
        self.assertEqual((body['work_hours'], body['late_minutes'], body['overtime_minutes']), (9.7, 0, 40))
        entry = body['history'][0]
        self.assertEqual((entry['by'], entry['reason']), ('hr.test', 'Clock was wrong'))
        self.assertEqual(entry['at'] % 1000, 0)
        self.assertEqual(entry['changes'], {
            'punch_in': {'from':stamp('10:05:00'), 'to':stamp('09:28:00')},
            'late_minutes': {'from':35, 'to':0}, 'work_hours': {'from':9.08, 'to':9.7}})
        status, body = self.correct(status='ON_DUTY')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['history']), 2)
        self.assertEqual(body['history'][0], entry)
        self.assertEqual(body['history'][1]['changes'], {'status': {'from':'PRESENT', 'to':'ON_DUTY'}})
        stored = self.db.attendance_logs.find_one({'emp_code':self.code, 'date':self.day})
        for instant in (stored['punch_in'], stored['punch_out'], stored['history'][0]['at'],
                        stored['history'][0]['changes']['punch_in']['to']):
            self.assertEqual(instant.utcoffset().total_seconds(), 0)
            self.assertEqual(instant.microsecond, 0)

    def test_correction_recomputes_stored_values(self):
        self.punch_in()
        self.assertEqual(self.punch_out()[0], 200)
        self.db.attendance_logs.update_one({'emp_code':self.code, 'date':self.day},
            {'$set':{'work_hours':1.0, 'late_minutes':99, 'overtime_minutes':0, 'half_day':True}})
        status, body = self.correct(status='PRESENT')
        self.assertEqual(status, 200, body)
        self.assertEqual((body['work_hours'], body['late_minutes'], body['overtime_minutes'], body['half_day']),
                         (9.5, 0, 30, False))
        self.assertEqual(set(body['history'][0]['changes']),
                         {'work_hours', 'late_minutes', 'overtime_minutes', 'half_day'})

    def test_leave_absence_and_return_to_presence(self):
        self.punch_in('10:05:00')
        self.punch_out()
        self.assertEqual(self.correct(status='LEAVE', punch_in=stamp('09:30:00'))[0], 422)
        status, body = self.correct(status='LEAVE')
        self.assertEqual(status, 200)
        for field in ('punch_in', 'punch_out', 'work_hours'):
            self.assertIsNone(body[field])
        self.assertEqual((body['late_minutes'], body['overtime_minutes'], body['half_day']), (0, 0, False))
        self.assertEqual(self.correct(status='PRESENT')[0], 422)
        self.assertEqual(self.correct(punch_in=stamp('09:30:00'))[0], 422)
        self.assertEqual(self.correct(status='ABSENT')[0], 200)
        status, body = self.correct(status='WFH', punch_in=stamp('09:40:00.999'))
        self.assertEqual(status, 200, body)
        self.assertEqual((body['late_minutes'], body['work_hours'], body['half_day']), (0, None, False))
        status, body = self.correct(punch_in=stamp('09:40:01'))
        self.assertEqual(status, 200)
        self.assertEqual(body['late_minutes'], 10)

    def test_correction_constraints_and_no_op(self):
        self.punch_in()
        for changes in ({}, {'work_hours':999}, {'status':'PRESENT'}, {'punch_in':stamp('09:30:00.999')},
                        {'punch_out':stamp('09:30:00')}, {'punch_out':stamp('09:30:01', '2026-08-05')},
                        {'punch_in':stamp('09:30:00', '2026-08-05')}):
            with self.subTest(changes=changes):
                self.assertEqual(self.correct(**changes)[0], 422)
        self.assertEqual(self.current()['history'], [])
        self.assertEqual(self.correct(punch_out=stamp('09:30:00', '2026-08-05'))[0], 200)
        for invalid_date in ('2026-02-30', '20260804', 'bad'):
            self.assertEqual(request('PATCH', f'/attendance/{self.code}/{invalid_date}',
                {'reason':'Wrong clock', 'regularized_by':'HR', 'status':'WFH'})[0], 422)

    def test_overnight_correction_date(self):
        self.db.employees.update_one({'emp_code':self.code}, {'$set':{'shift_start':'22:00', 'shift_end':'06:00'}})
        self.punch_in('22:00:00')
        status, body = self.correct(punch_in=stamp('05:50:00', '2026-08-05'))
        self.assertEqual(status, 200, body)
        self.assertEqual(body['date'], self.day)
        self.assertEqual(self.correct(punch_in=stamp('06:00:00', '2026-08-05'))[0], 422)

    def test_legacy_record(self):
        self.punch_in()
        self.db.attendance_logs.update_one({'emp_code':self.code, 'date':self.day},
            {'$unset':{'history':'', 'half_day':'', 'late_minutes':'', 'overtime_minutes':'', 'punch_out':''}})
        status, body = self.correct(status='WFH')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['history'][0]['changes'], {'status':{'from':'PRESENT', 'to':'WFH'}})
        self.assertFalse(body['half_day'])
        self.assertEqual(self.punch_out()[0], 200)
        self.assertEqual(len(self.current()['history']), 1)

    def test_punch_out_on_legacy_missing_fields(self):
        self.punch_in()
        self.db.attendance_logs.update_one({'emp_code':self.code, 'date':self.day},
            {'$unset':{'history':'', 'half_day':'', 'punch_out':''}})
        status, body = self.punch_out()
        self.assertEqual(status, 200, body)
        self.assertEqual(body['history'], [])
        self.assertFalse(body['half_day'])
        stored = self.db.attendance_logs.find_one({'emp_code':self.code, 'date':self.day})
        self.assertNotIn('history', stored)

    def parallel(self, calls):
        barrier = threading.Barrier(len(calls))
        def run(call):
            barrier.wait(timeout=10)
            return call()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(run, calls))

    def test_concurrent_punch_out(self):
        self.punch_in()
        responses = self.parallel([lambda: self.punch_out() for _ in range(6)])
        self.assertEqual(sorted(status for status, _ in responses), [200, 409, 409, 409, 409, 409])
        self.assertEqual(self.current()['history'], [])

    def test_concurrent_corrections_preserve_history(self):
        self.punch_in()
        calls = [lambda i=i: self.correct(punch_in=stamp(f'09:30:{i:02d}'), reason=f'Correction {i}') for i in range(1, 7)]
        responses = self.parallel(calls)
        self.assertTrue(all(status in (200, 409) for status, _ in responses), responses)
        successful = [body['history'][-1]['reason'] for status, body in responses if status == 200]
        record = self.current()
        self.assertGreater(len(successful), 0)
        self.assertEqual(len(record['history']), len(successful))
        self.assertEqual({entry['reason'] for entry in record['history']}, set(successful))
        previous = stamp('09:30:00')
        for entry in record['history']:
            self.assertEqual(entry['changes']['punch_in']['from'], previous)
            previous = entry['changes']['punch_in']['to']
        self.assertEqual(record['punch_in'], previous)

    def test_identical_concurrent_corrections(self):
        self.punch_in()
        responses = self.parallel([lambda: self.correct(status='WFH') for _ in range(6)])
        statuses = [status for status, _ in responses]
        self.assertEqual(statuses.count(200), 1)
        self.assertTrue(all(status in (200, 409, 422) for status in statuses), responses)
        record = self.current()
        self.assertEqual(record['status'], 'WFH')
        self.assertEqual(len(record['history']), 1)
        self.assertEqual(self.correct(status='WFH')[0], 422)
        self.assertEqual(len(self.current()['history']), 1)

    def test_punch_out_racing_correction(self):
        self.punch_in()
        responses = self.parallel([lambda: self.punch_out(), lambda: self.correct(status='WFH')])
        self.assertTrue(all(status in (200, 409) for status, _ in responses), responses)
        record = self.current()
        if responses[0][0] == 200:
            self.assertEqual(record['punch_out'], stamp('19:00:00'))
            self.assertEqual(record['work_hours'], 9.5)
        if responses[1][0] == 200:
            self.assertEqual(record['status'], 'WFH')
            self.assertEqual(len(record['history']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
