"""Compare the supplied contract with generated schemas, without MongoDB.

Documentation text, operation IDs, tags, titles and examples do not affect wire
schemas. OpenAPI version/info metadata differ between 3.0 and FastAPI's 3.1.
References, singleton allOf, nullable and const are normalized to their equivalent
schema forms. Model names may differ; their complete reachable fields are compared.
This checks schema structure, not runtime business rules or error body contents.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml


class NoDatabase:
    def __getitem__(self, name):
        return self

    def __getattr__(self, name):
        raise AssertionError('Contract checks must not access MongoDB: ' + name)


# Importing main normally creates a client with background connection threads.
# Replace it before import; lifespan and HTTP endpoints are never invoked here.
with patch('pymongo.MongoClient', return_value=NoDatabase()):
    from app.main import app


METADATA = {'title', 'description', 'example', 'examples', 'summary', 'operationId', 'tags'}
METHODS = {'get', 'post', 'put', 'patch', 'delete', 'options', 'head', 'trace'}


def normalize(value, document, mapping=False):
    if isinstance(value, list):
        return [normalize(item, document) for item in value]
    if not isinstance(value, dict):
        return value
    if mapping:
        return {key: normalize(item, document) for key, item in value.items()}
    value = dict(value)
    while '$ref' in value:
        target = document
        reference = value.pop('$ref')
        if not reference.startswith('#/'):
            raise ValueError('Only local references are supported: ' + reference)
        for part in reference[2:].split('/'):
            target = target[part.replace('~1', '/').replace('~0', '~')]
        value = {**target, **value}
    if len(value.get('allOf', [])) == 1:
        value = {**normalize(value.pop('allOf')[0], document), **value}
    nullable = value.pop('nullable', False)
    value = {key: normalize(item, document, key in ('properties', 'content', 'headers', 'encoding'))
             for key, item in value.items() if key not in METADATA}
    if 'const' in value:
        value['enum'] = [value.pop('const')]
    for key in ('required', 'enum'):
        if isinstance(value.get(key), list):
            value[key] = sorted(value[key])
    # An untyped nullable schema already accepts any value, including null.
    if nullable and value:
        read_only = value.pop('readOnly', None)
        value = {'anyOf': [value, {'type': 'null'}]}
        if read_only is not None:
            value['readOnly'] = read_only
    for key in ('anyOf', 'oneOf', 'allOf'):
        if key in value:
            value[key] = sorted(value[key], key=lambda item: json.dumps(item, sort_keys=True))
    return value


def contract_groups(document):
    groups = {}
    groups[('paths',)] = sorted(document['paths'])
    for path, path_item in document['paths'].items():
        methods = METHODS.intersection(path_item)
        groups[(path, 'methods')] = sorted(methods)
        for method in methods:
            operation = path_item[method]
            prefix = (path, method)
            parameters = {}
            for parameter in path_item.get('parameters', []) + operation.get('parameters', []):
                parameter = normalize(parameter, document)
                parameter.setdefault('required', False)
                identity = (parameter['in'], parameter['name'])
                if identity in parameters:
                    raise ValueError('Duplicate parameter: ' + str(identity))
                parameters[identity] = parameter
            groups[prefix + ('parameters',)] = parameters
            groups[prefix + ('status_codes',)] = sorted(operation['responses'])
            # Include absent bodies too, so an unexpected request body fails.
            groups[prefix + ('request',)] = normalize(operation.get('requestBody'), document)
            for code, response in operation['responses'].items():
                groups[prefix + ('response ' + code,)] = normalize(response, document)
    return groups


MISSING = '<missing>'


def differences(expected, actual, path=()):
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = []
        for key in sorted(expected.keys() | actual.keys(), key=str):
            result.extend(differences(expected.get(key, MISSING), actual.get(key, MISSING), path + (key,)))
        return result
    return [] if expected == actual else [(path, expected, actual)]


def unexpected_differences(expected, actual):
    found = differences(contract_groups(expected), contract_groups(actual))
    # Group keys are tuples; flatten them for a precise, readable exception path.
    found = [(path[0] + path[1:], before, after) for path, before, after in found]
    return found


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.supplied = yaml.safe_load(Path(__file__).with_name('openapi.yaml').read_text())
        cls.generated = app.openapi()

    def test_complete_contract(self):
        unexpected = unexpected_differences(self.supplied, self.generated)
        self.assertEqual(unexpected, [], '\n'.join(map(str, unexpected)))
        print('Contract comparison: zero normalized differences; no schema exceptions.')

    def test_joined_on_schema_has_no_extra_constraints(self):
        schema = self.generated['components']['schemas']['EmployeeIn']['properties']['joined_on']
        self.assertEqual(normalize(schema, self.generated), {'type': 'string', 'format': 'date'})
        for field in ('joined_on', 'name'):
            with self.subTest(field=field):
                document = copy.deepcopy(self.generated)
                document['components']['schemas']['EmployeeIn']['properties'][field]['pattern'] = r'^\d{4}-\d{2}-\d{2}$'
                self.assertTrue(unexpected_differences(self.supplied, document))

    def test_nested_references_preserve_constraints(self):
        document = {'components': {'schemas': {
            'Code': {'type': 'string', 'minLength': 3, 'pattern': '^EMP'},
            'Alias': {'$ref': '#/components/schemas/Code'},
            'Record': {'type': 'object', 'required': ['code'], 'properties': {
                'code': {'$ref': '#/components/schemas/Alias'}}},
        }}}
        self.assertEqual(normalize({'$ref': '#/components/schemas/Record'}, document),
                         {'type': 'object', 'required': ['code'], 'properties': {
                             'code': {'type': 'string', 'minLength': 3, 'pattern': '^EMP'}}})

    def test_detects_contract_mutations(self):
        mutations = [
            lambda doc: doc['paths'].pop('/health'),
            lambda doc: doc['paths']['/employees'].pop('get'),
            lambda doc: doc['paths']['/employees']['get']['parameters'].pop(),
            lambda doc: doc['paths']['/employees']['post']['responses'].pop('409'),
            lambda doc: doc['components']['schemas']['EmployeeIn']['properties']['joined_on'].update(pattern='.*'),
            lambda doc: doc['components']['schemas']['EmployeeIn']['properties']['name'].update(maxLength=101),
            lambda doc: doc['components']['schemas']['EmployeeIn']['required'].remove('email'),
            lambda doc: doc['components']['schemas']['Employee']['properties'].pop('email'),
            lambda doc: doc['paths']['/employees']['get']['parameters'][1]['schema'].update(minimum=0),
            lambda doc: doc['paths']['/employees']['get']['parameters'][1].update(required=True),
            lambda doc: doc['components']['schemas']['AttendanceRecord']['properties']['work_hours'].pop('anyOf'),
            lambda doc: doc['components']['schemas']['AttendanceRecord']['required'].remove('history'),
            lambda doc: doc['components']['schemas']['Employee']['properties']['created_at'].update(minimum=0),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                document = copy.deepcopy(self.generated)
                mutate(document)
                self.assertTrue(unexpected_differences(self.supplied, document))

    def test_metadata_named_fields_are_preserved(self):
        schema = {'type': 'object', 'properties': {'description': {'type': 'string'}, 'title': {'type': 'integer'}}}
        self.assertEqual(normalize(schema, {}), schema)

    def test_equivalent_schema_forms(self):
        self.assertEqual(normalize({'type': 'number', 'nullable': True}, {}),
                         normalize({'anyOf': [{'type': 'null'}, {'type': 'number'}]}, {}))
        self.assertEqual(normalize({'const': 'ok'}, {}), {'enum': ['ok']})
        self.assertEqual(normalize({'allOf': [{'type': 'integer'}], 'readOnly': True}, {}),
                         {'type': 'integer', 'readOnly': True})


if __name__ == '__main__':
    unittest.main(verbosity=2)
