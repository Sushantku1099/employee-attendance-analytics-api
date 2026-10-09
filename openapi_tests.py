"""Focused schema/runtime regressions. Run: python -B openapi_tests.py.

Uses actual production models and app.openapi(); does not connect to MongoDB.
"""
import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import EmployeeIn, PunchInIn, app, list_attendance, parse_query_date


class OpenAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = app.openapi()

    def resolve(self, schema):
        if '$ref' in schema:
            return self.schema['components']['schemas'][schema['$ref'].split('/')[-1]]
        return schema

    def response(self, path, method, code):
        return self.resolve(self.schema['paths'][path][method]['responses'][code]
                            ['content']['application/json']['schema'])

    def test_request_fields(self):
        employee = self.schema['components']['schemas']['EmployeeIn']
        self.assertEqual(set(employee['required']), {'emp_code', 'name', 'email', 'department', 'joined_on'})
        self.assertEqual(employee['properties']['joined_on']['type'], 'string')
        self.assertEqual(employee['properties']['joined_on']['format'], 'date')
        punch = self.schema['components']['schemas']['PunchInIn']
        self.assertEqual(punch['required'], ['emp_code'])
        timestamp = punch['properties']['punched_at']
        self.assertEqual(timestamp['type'], 'integer')
        self.assertEqual(timestamp['format'], 'int64')
        self.assertEqual(timestamp['minimum'], 100000000000)
        self.assertEqual(timestamp['maximum'], 4102444800000)
        self.assertNotIn('anyOf', timestamp)
        self.assertNotIn('default', timestamp)
        for path in ('/employees', '/attendance/punch-in'):
            self.assertTrue(self.schema['paths'][path]['post']['requestBody']['required'])

    def test_query_fields(self):
        for path in ('/employees', '/attendance'):
            parameters = {p['name']: p for p in self.schema['paths'][path]['get']['parameters']}
            for name, parameter in parameters.items():
                self.assertFalse(parameter['required'])
                self.assertNotIn('anyOf', parameter['schema'])
                self.assertEqual(parameter['schema']['type'], 'integer' if name in ('page', 'page_size') else 'string')
            self.assertEqual(parameters['page']['schema']['minimum'], 1)
            self.assertEqual(parameters['page']['schema']['default'], 1)
            self.assertEqual(parameters['page_size']['schema']['maximum'], 100)
            self.assertEqual(parameters['page_size']['schema']['default'], 20)
        for name in ('date_from', 'date_to'):
            self.assertEqual(parameters[name]['schema']['format'], 'date')
        self.assertEqual(set(parameters['status']['schema']['enum']),
                         {'PRESENT', 'ABSENT', 'LEAVE', 'WFH', 'ON_DUTY'})

    def test_response_codes(self):
        expected = {('/health', 'get'): {'200', '503'},
                    ('/employees', 'post'): {'201', '409', '422'},
                    ('/employees', 'get'): {'200', '422'},
                    ('/attendance/punch-in', 'post'): {'201', '404', '409', '422'},
                    ('/attendance', 'get'): {'200', '422'}}
        for (path, method), codes in expected.items():
            with self.subTest(path=path, method=method):
                responses = self.schema['paths'][path][method]['responses']
                self.assertEqual(set(responses), codes)
                for code in codes:
                    body = self.response(path, method, code)
                    self.assertEqual(body['type'], 'object')
                    if int(code) >= 400:
                        self.assertEqual(body['required'], ['detail'])
                        self.assertEqual(body['properties']['detail']['type'], 'array' if code == '422' else 'string')

    def test_response_fields(self):
        employee = self.response('/employees', 'post', '201')
        expected_employee = {'emp_code', 'name', 'email', 'department', 'shift_start', 'shift_end', 'joined_on', 'created_at'}
        self.assertEqual(set(employee['required']), expected_employee)
        self.assertEqual(set(employee['properties']), expected_employee)
        self.assertEqual(employee['properties']['joined_on']['format'], 'date')
        self.assertTrue(employee['properties']['created_at']['readOnly'])
        record = self.response('/attendance/punch-in', 'post', '201')
        expected_record = {'emp_code', 'date', 'status', 'punch_in', 'punch_out', 'work_hours',
                           'late_minutes', 'overtime_minutes', 'half_day', 'history'}
        self.assertEqual(set(record['required']), expected_record)
        self.assertEqual(set(record['properties']), expected_record)
        self.assertEqual(record['properties']['date']['format'], 'date')
        for name in ('punch_in', 'punch_out', 'work_hours'):
            self.assertIn({'type': 'null'}, record['properties'][name]['anyOf'])
        for name in ('work_hours', 'late_minutes', 'overtime_minutes', 'half_day'):
            self.assertTrue(record['properties'][name]['readOnly'])
        history = self.resolve(record['properties']['history']['items'])
        self.assertEqual(set(history['required']), {'at', 'by', 'reason', 'changes'})
        self.assertEqual(history['properties']['at']['format'], 'int64')
        for path in ('/employees', '/attendance'):
            page = self.response(path, 'get', '200')
            self.assertEqual(set(page['required']), {'items', 'total', 'page', 'page_size'})
            self.assertEqual(page['properties']['items']['type'], 'array')
        health = self.response('/health', 'get', '200')
        self.assertEqual(health['required'], ['status'])
        self.assertEqual(health['properties']['status']['const'], 'ok')

    def test_joined_on_runtime(self):
        body = dict(emp_code='EMP0001', name='A', email='a@b.com', department='QA')
        self.assertEqual(EmployeeIn(**body, joined_on='2024-02-29').joined_on, '2024-02-29')
        for value in ('2026-02-29', '20260201', '2026-2-01', '', None, 1783312500000):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                EmployeeIn(**body, joined_on=value)

    def test_query_date_runtime(self):
        self.assertIsNone(parse_query_date(None))
        self.assertEqual(parse_query_date('2024-02-29').isoformat(), '2024-02-29')
        for value in ('2026-02-29', '20260201', '2026-2-01', '', '1783312500000'):
            with self.subTest(value=value), self.assertRaises(HTTPException) as caught:
                parse_query_date(value)
            self.assertEqual(caught.exception.status_code, 422)

    def test_date_error_bodies(self):
        for field in ('date_from', 'date_to'):
            with self.subTest(field=field), self.assertRaises(HTTPException) as caught:
                list_attendance(date_from='2026-02-30' if field == 'date_from' else None,
                                date_to='2026-02-30' if field == 'date_to' else None)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertIsInstance(caught.exception.detail, list)
            self.assertEqual(caught.exception.detail[0]['loc'], ['query', field])
            self.assertIsInstance(caught.exception.detail[0]['msg'], str)
        with self.assertRaises(HTTPException) as caught:
            list_attendance(date_from='2026-08-05', date_to='2026-08-04')
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIsInstance(caught.exception.detail, list)
        self.assertEqual(caught.exception.detail[0]['loc'], ['query', 'date_to'])

    def test_timestamp_runtime(self):
        self.assertIsNone(PunchInIn(emp_code='EMP0001').punched_at)
        for value in (None, '1783312500000', 1783312500000.0, True, 1783312500):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                PunchInIn(emp_code='EMP0001', punched_at=value)
        for value in (100000000000, 4102444800000):
            self.assertEqual(PunchInIn(emp_code='EMP0001', punched_at=value).punched_at, value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
