"""Phase 2 model, schema and update checks without database reads or writes."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (UTC, PunchOutIn, RegularizeIn, app, update_attendance,
                      validate_punch_duration)


class Phase2UnitTests(unittest.TestCase):
    def test_punch_out_model(self):
        self.assertIsNone(PunchOutIn(emp_code='EMP0001').punched_at)
        for value in (None, True, '1783312500000', 1783312500000.0, 1783312500, 4102444800001):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                PunchOutIn(emp_code='EMP0001', punched_at=value)
        self.assertEqual(PunchOutIn(emp_code='EMP0001', punched_at=1783312500999).punched_at, 1783312500999)

    def test_correction_model(self):
        required = dict(reason='Wrong clock', regularized_by='HR')
        body = RegularizeIn(**required, work_hours=999)
        self.assertEqual(body.model_fields_set, {'reason', 'regularized_by'})
        for field in ('punch_in', 'punch_out', 'status'):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                RegularizeIn(**required, **{field:None})
        for value in (True, '1783312500000', 1783312500000.0, 1783312500):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                RegularizeIn(**required, punch_in=value)
        self.assertEqual(RegularizeIn(**required, status='LEAVE').status, 'LEAVE')

    def test_duration_validation(self):
        start = datetime(2026, 8, 4, tzinfo=UTC)
        for seconds in (-1, 0, 86401):
            with self.subTest(seconds=seconds), self.assertRaises(HTTPException) as error:
                validate_punch_duration(start, start + timedelta(seconds=seconds))
            self.assertEqual(error.exception.status_code, 422)
            self.assertIsInstance(error.exception.detail, list)
        validate_punch_duration(start, start + timedelta(seconds=1))
        validate_punch_duration(start, start + timedelta(hours=24))

    def test_update_matches_snapshot_and_appends_history(self):
        record = dict(emp_code='EMP0001', date='2026-08-04', status='PRESENT', punch_out=None,
                      history=[{'reason':'Earlier change'}])
        entry = dict(reason='New change')
        with patch('app.main.db') as database:
            database.attendance_logs.find_one_and_update.return_value = {'status':'WFH'}
            self.assertEqual(update_attendance(record, {'status':'WFH'}, entry), {'status':'WFH'})
            filters, update = database.attendance_logs.find_one_and_update.call_args.args
            self.assertEqual(filters['emp_code'], record['emp_code'])
            self.assertEqual(filters['date'], record['date'])
            self.assertEqual(filters['punch_out'], {'$eq':None, '$exists':True})
            self.assertEqual(filters['half_day'], {'$exists':False})
            self.assertEqual(filters['history'], {'$eq':record['history'], '$exists':True})
            self.assertEqual(update, {'$set':{'status':'WFH'}, '$push':{'history':entry}})
            self.assertNotIn('_id', filters)

    def test_stale_update_conflicts(self):
        with patch('app.main.db') as database:
            database.attendance_logs.find_one_and_update.return_value = None
            with self.assertRaises(HTTPException) as error:
                update_attendance({'emp_code':'EMP0001', 'date':'2026-08-04'}, {'status':'WFH'})
            self.assertEqual(error.exception.status_code, 409)

    def test_punch_out_does_not_append_history(self):
        with patch('app.main.db') as database:
            update_attendance({'emp_code':'EMP0001', 'date':'2026-08-04'}, {'punch_out':None})
            update = database.attendance_logs.find_one_and_update.call_args.args[1]
            self.assertNotIn('$push', update)

    def test_phase2_schema(self):
        schema = app.openapi()
        models = schema['components']['schemas']
        self.assertEqual(models['PunchOutIn']['required'], ['emp_code'])
        self.assertEqual(set(models['RegularizeIn']['required']), {'reason', 'regularized_by'})
        for name, fields in (('PunchOutIn', ('punched_at',)), ('RegularizeIn', ('punch_in', 'punch_out'))):
            for field in fields:
                value = models[name]['properties'][field]
                self.assertEqual(value['type'], 'integer')
                self.assertEqual(value['format'], 'int64')
                self.assertNotIn('anyOf', value)
        for path, method in (('/attendance/punch-out', 'post'), ('/attendance/{emp_code}/{date}', 'patch')):
            operation = schema['paths'][path][method]
            self.assertEqual(set(operation['responses']), {'200', '404', '409', '422'})
            self.assertTrue(operation['requestBody']['required'])
        parameters = schema['paths']['/attendance/{emp_code}/{date}']['patch']['parameters']
        self.assertTrue(all(p['required'] for p in parameters))
        self.assertEqual(next(p for p in parameters if p['name'] == 'date')['schema']['format'], 'date')


if __name__ == '__main__':
    unittest.main(verbosity=2)
