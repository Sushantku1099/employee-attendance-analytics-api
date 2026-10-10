"""Run Phase 1/2/3 checks against a new disposable MongoDB container.

Requires Docker, an already available mongo:7 image, and installed requirements.
Never connects to user databases. Stops only the container and API it starts.
"""
import asyncio
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

from pymongo import MongoClient

# Fail on contract drift before starting Docker or connecting to a database.
subprocess.run([sys.executable, '-B', 'openapi_contract_tests.py'], check=True)

container_name = 'candidate-phase3-' + uuid.uuid4().hex[:12]
server = None
mongo = None
started_container = False
with tempfile.TemporaryFile(mode='w+') as log:
    try:
        subprocess.run(['docker', 'image', 'inspect', 'mongo:7'], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['docker', 'run', '--rm', '-d', '--name', container_name,
            '-p', '127.0.0.1::27017', 'mongo:7'], check=True, stdout=subprocess.DEVNULL)
        started_container = True
        address = subprocess.check_output(['docker', 'port', container_name, '27017/tcp'], text=True).strip()
        uri = 'mongodb://' + address
        mongo = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=500)
        deadline = time.monotonic() + 30
        while True:
            try:
                mongo.admin.command('ping')
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)
        database_name = 'phase3_review'
        assert database_name not in mongo.list_database_names()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, MONGO_URI=uri, MONGO_DB=database_name,
            TEST_MONGO_URI=uri, TEST_MONGO_DB=database_name,
            TEST_BASE_URL=f'http://127.0.0.1:{port}', PHASE1_ALLOW_TEST_WRITES='1',
            PHASE2_ALLOW_TEST_WRITES='1', PHASE3_ALLOW_TEST_WRITES='1', PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable, '-B', '-m', 'uvicorn', 'app.main:app', '--port', str(port)]
        start = time.monotonic()
        server = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        while True:
            try:
                with urllib.request.urlopen(env['TEST_BASE_URL'] + '/health', timeout=1) as response:
                    assert response.status == 200 and json.load(response) == {'status':'ok'}
                break
            except OSError:
                if time.monotonic() - start > 20 or server.poll() is not None:
                    log.seek(0)
                    raise AssertionError(log.read())
                time.sleep(.1)
        print(f'Isolated MongoDB {mongo.server_info()["version"]}; API ready in {time.monotonic()-start:.2f}s', flush=True)
        # Check a truly empty database before any suite creates fixtures.
        for path in ('/analytics/departments/summary?month=2030-04', '/analytics/leaderboard/late?month=2030-04'):
            with urllib.request.urlopen(env['TEST_BASE_URL'] + path) as response:
                assert json.load(response) == {'month': '2030-04', 'items': []}
        print('Empty-database HTTP checks: 2 passed, 0 failed', flush=True)
        for filename in ('openapi_tests.py', 'phase3_schema_tests.py', 'static_helper_tests.py', 'phase2_unit_tests.py',
                         'phase1_tests.py', 'phase2_tests.py', 'phase3_tests.py'):
            subprocess.run([sys.executable, '-B', filename], env=env, check=True)
        os.environ.update(env)
        sys.path.insert(0, str(Path.cwd()))
        from app.main import app, db, lifespan, client
        async def indexes():
            async with lifespan(app):
                async with lifespan(app):
                    assert db.employees.index_information()['emp_code_1']['unique']
                    assert 'joined_on_1' in db.employees.index_information()
                    assert 'department_1_joined_on_1' in db.employees.index_information()
                    info = db.attendance_logs.index_information()
                    assert info['emp_code_1_date_1']['unique']
                    assert 'date_-1_emp_code_1' in info
                    assert 'date_1' in info
                    assert 'emp_code_1_punch_in_-1' in info
        asyncio.run(indexes())
        print('Startup/index checks: 7 passed, 0 failed; repeated lifespan successful', flush=True)
        with urllib.request.urlopen(env['TEST_BASE_URL'] + '/openapi.json') as response:
            assert json.load(response) == app.openapi()
        print('Served schema check: 1 passed, 0 failed', flush=True)
        # Inspect only this disposable instance for a representative punch-out query.
        plan = db.attendance_logs.find({'emp_code':'EMP7001', 'punch_in':{'$lte':datetime.now(timezone.utc)}}).sort('punch_in', -1).limit(1).explain()
        plan_text = json.dumps(plan, default=str)
        assert 'IXSCAN' in plan_text and 'COLLSCAN' not in plan_text
        print('Punch-out small-fixture query plan: IXSCAN, no COLLSCAN', flush=True)
        client.close()
    finally:
        if server is not None:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        if mongo is not None:
            mongo.close()
        if started_container:
            subprocess.run(['docker', 'stop', container_name], check=True, stdout=subprocess.DEVNULL)
            print('Stopped test server and removed only this run\'s --rm MongoDB container.', flush=True)
