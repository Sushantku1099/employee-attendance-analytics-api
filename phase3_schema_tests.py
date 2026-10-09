"""Phase 3 schema assertions against the supplied contract, without a database."""
import unittest
from app.main import app, MONTH_PATTERN


class Phase3SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = app.openapi()
        cls.paths = {
            '/analytics/employees/{emp_code}/monthly': ({'emp_code', 'month'}, {'200', '404', '422'}),
            '/analytics/departments/summary': ({'month', 'department'}, {'200', '422'}),
            '/analytics/leaderboard/late': ({'month', 'department', 'limit'}, {'200', '422'}),
            '/analytics/departments/{department}/trend': ({'department', 'from', 'to'}, {'200', '404', '422'}),
            '/admin/explain/{endpoint}': ({'endpoint', 'emp_code', 'month', 'department', 'limit',
                'date_from', 'date_to', 'status', 'from', 'to', 'page', 'page_size'}, {'200', '422'}),
        }

    def resolve(self, value):
        if '$ref' in value:
            return self.schema['components']['schemas'][value['$ref'].split('/')[-1]]
        return value

    def test_parameters_and_status_codes(self):
        for path, (names, codes) in self.paths.items():
            with self.subTest(path=path):
                operation = self.schema['paths'][path]['get']
                self.assertEqual(set(operation['responses']), codes)
                self.assertEqual({p['name'] for p in operation['parameters']}, names)
                for parameter in operation['parameters']:
                    name, schema = parameter['name'], parameter['schema']
                    self.assertEqual(schema['type'], 'integer' if name in ('limit', 'page', 'page_size') else 'string')
                    self.assertNotIn('anyOf', schema)
                    if name in ('from', 'to', 'date_from', 'date_to'): self.assertEqual(schema['format'], 'date')
                    if name == 'month': self.assertEqual(schema['pattern'], MONTH_PATTERN)
                    expected_required = parameter['in'] == 'path' or (path != '/admin/explain/{endpoint}' and name in ('month', 'from', 'to'))
                    self.assertEqual(parameter['required'], expected_required)
                    if name in ('limit', 'page', 'page_size'):
                        self.assertEqual(schema['minimum'], 1)
                        self.assertEqual(schema['default'], {'limit':10, 'page':1, 'page_size':20}[name])
                        if name != 'page': self.assertEqual(schema['maximum'], 50 if name == 'limit' else 100)

    def test_response_fields_required_and_nullable(self):
        expected = {
            'EmployeeMonthly': ('emp_code month working_days present_days leave_days late_count total_late_minutes total_overtime_minutes attendance_pct', {'attendance_pct'}),
            'DepartmentSummary': ('month items', set()),
            'DepartmentSummaryItem': ('department headcount present_days avg_work_hours late_count total_late_minutes leave_count on_duty_count', {'avg_work_hours'}),
            'Leaderboard': ('month items', set()),
            'LeaderboardItem': ('rank emp_code name department total_late_minutes late_count', set()),
            'Trend': ('department items', set()),
            'TrendItem': ('date is_working_day headcount present_count late_count attendance_rate moving_avg_7d', {'attendance_rate', 'moving_avg_7d'}),
            'ExplainResponse': ('endpoint collection explain', set()),
        }
        for model, (fields, nullable) in expected.items():
            with self.subTest(model=model):
                schema = self.schema['components']['schemas'][model]
                self.assertEqual(set(schema['required']), set(fields.split()))
                self.assertEqual(set(schema['properties']), set(fields.split()))
                for name, field in schema['properties'].items():
                    self.assertEqual({'type':'null'} in field.get('anyOf', []), name in nullable)
        models = self.schema['components']['schemas']
        self.assertEqual(models['TrendItem']['properties']['date']['format'], 'date')
        self.assertEqual(models['LeaderboardItem']['properties']['rank']['minimum'], 1)
        self.assertTrue(models['ExplainResponse']['properties']['explain']['additionalProperties'])
        for path in self.paths:
            response = self.resolve(self.schema['paths'][path]['get']['responses']['200']['content']['application/json']['schema'])
            self.assertEqual(response['type'], 'object')
            validation = self.resolve(self.schema['paths'][path]['get']['responses']['422']['content']['application/json']['schema'])
            self.assertEqual(validation['properties']['detail']['type'], 'array')


if __name__ == '__main__':
    unittest.main(verbosity=2)
