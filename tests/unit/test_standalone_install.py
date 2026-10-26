"""Installation auth boundary: real signed sessions, no hosted Auth dependency."""
import os
import importlib
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from utils.auth.standalone import router
from utils.auth.session_config import session_cookie_name
from kombu import Connection


class StandaloneSessionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"STANDALONE_OWNER_EMAIL": "owner@example.test", "STANDALONE_PASSWORD": "test-password"})
        self.env.start()
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-signing-secret", session_cookie="di_session")
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def test_login_profile_and_logout(self):
        self.assertEqual(self.client.get('/auth/profile').status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={"password": "wrong"}).status_code, 401)
        response = self.client.post('/auth/login', json={"password": "test-password"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('httponly', response.headers['set-cookie'].lower())
        self.assertEqual(self.client.get('/auth/profile').json()['email'], 'owner@example.test')
        self.client.post('/auth/logout')
        self.assertEqual(self.client.get('/auth/profile').status_code, 401)

    def test_forged_cookie_is_refused(self):
        self.client.cookies.set('di_session', 'forged')
        self.assertEqual(self.client.get('/auth/profile').status_code, 401)


class HostedSessionCompatibilityTests(unittest.TestCase):
    @staticmethod
    def session_app(cookie_name, signing_key="unchanged-hosted-signing-key"):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key=signing_key, session_cookie=cookie_name)

        @app.post('/begin')
        def begin(request: Request):
            request.session['oauth_state'] = 'pending-connector-flow'
            return {'status': 'started'}

        @app.get('/state')
        def state(request: Request):
            return dict(request.session)

        return app

    def test_hosted_deployment_keeps_existing_signed_session(self):
        with TestClient(self.session_app('session')) as old:
            old.post('/begin')
            cookies = dict(old.cookies)
        with patch.dict(os.environ, {'AUTH_MODE': 'hosted'}), TestClient(self.session_app(session_cookie_name())) as candidate:
            candidate.cookies.update(cookies)
            self.assertEqual(candidate.get('/state').json(), {'oauth_state': 'pending-connector-flow'})

    def test_standalone_does_not_accept_hosted_session_cookie(self):
        with TestClient(self.session_app('session')) as old:
            old.post('/begin')
            cookies = dict(old.cookies)
        with patch.dict(os.environ, {'AUTH_MODE': 'standalone'}), TestClient(self.session_app(session_cookie_name())) as standalone:
            standalone.cookies.update(cookies)
            self.assertEqual(standalone.get('/state').json(), {})
            standalone.post('/begin')
            self.assertIn('di_session', standalone.cookies)

    def test_rotated_private_signing_key_rejects_previous_public_signature(self):
        with TestClient(self.session_app('session', 'previous-public-value')) as previous:
            previous.post('/begin')
            cookies = dict(previous.cookies)
        with TestClient(self.session_app('session', 'new-private-master')) as candidate:
            candidate.cookies.update(cookies)
            self.assertEqual(candidate.get('/state').json(), {})


class ProductionApiBrokerTests(unittest.TestCase):
    def broker(self, explicit, access, secret):
        from utils import setup_env
        with patch.dict(os.environ, {'MODE': 'PROD', 'ENV_FILE': '/dev/null', 'CELERY_BROKER_URL': explicit, 'SQS_AWS_ACCESS_KEY_ID': access, 'SQS_AWS_SECRET_ACCESS_KEY': secret}):
            url = importlib.reload(setup_env).BROKER_URL
        importlib.reload(setup_env)
        return url

    def test_production_producer_can_use_workload_credentials(self):
        connection = Connection(self.broker('sqs://', '', ''))
        self.assertIsNone(connection.userid)
        self.assertIsNone(connection.password)

    def test_production_producer_keeps_legacy_credentials_without_override(self):
        connection = Connection(self.broker('', 'test-access', 'test/secret+value'))
        self.assertEqual(connection.userid, 'test-access')
        self.assertEqual(connection.password, 'test/secret+value')


class GeneratedKeyRotationTests(unittest.TestCase):
    def hash_for_config(self, master, retained_pepper):
        from utils import setup_env
        from utils.db import api_key_ops
        try:
            with patch.dict(os.environ, {'ENV_FILE': '/dev/null', 'FASTAPI_SECRET_KEY': master, 'API_KEY_HASH_SECRET': retained_pepper}):
                importlib.reload(setup_env)
                importlib.reload(api_key_ops)
                return api_key_ops.hash_api_key('synthetic-existing-generated-key')
        finally:
            importlib.reload(setup_env)
            importlib.reload(api_key_ops)

    def test_rotation_preserves_persisted_generated_key_hash(self):
        previous = self.hash_for_config('previous-master', '')
        rotated = self.hash_for_config('new-private-master', 'previous-master')
        self.assertEqual(previous, rotated)
        self.assertNotEqual(rotated, self.hash_for_config('new-private-master', ''))

    def test_unconfigured_pepper_keeps_existing_hash_contract(self):
        import hashlib
        import hmac
        actual = self.hash_for_config('existing-installation-master', '')
        expected = hmac.new(b'existing-installation-master', b'synthetic-existing-generated-key', hashlib.sha256).hexdigest()
        self.assertEqual(actual, expected)


class StandaloneCredentialBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retired_public_value_does_not_authenticate_as_new_master(self):
        from utils.auth import dependencies
        with patch.object(dependencies, 'API_KEY_NAME', 'new-private-master'), patch.object(dependencies, 'get_auth_details', new_callable=AsyncMock, return_value=None), patch('utils.db.api_key_ops.validate_api_key', new_callable=AsyncMock, return_value=None):
            with self.assertRaises(dependencies.HTTPException) as rejected:
                await dependencies.get_api_key_or_session(Mock(), 'previous-public-value')
            self.assertEqual(rejected.exception.status_code, 401)

    async def test_unknown_owner_does_not_select_installation_key(self):
        from utils.usage.credentials import get_credential_for_knowledge
        with patch.dict(os.environ, {"BYOK_PLATFORM_FALLBACK": "false"}), patch('utils.usage.credentials._resolve_owner_id', new_callable=AsyncMock, return_value=None):
            with self.assertRaises(ValueError):
                await get_credential_for_knowledge('unknown-knowledge', 'openai')

    async def test_private_bucket_read_uses_workload_credentials(self):
        from utils.files import s3_utils
        body = AsyncMock()
        body.__aenter__.return_value = body
        body.read.return_value = b'synthetic fixture'
        client = AsyncMock()
        client.get_object.return_value = {'Body': body}
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch.object(s3_utils, 'BUCKET_NAME', 'install-test'), patch.object(s3_utils, 'get_s3_client', new_callable=AsyncMock, return_value=context), patch.object(s3_utils.httpx, 'AsyncClient', side_effect=AssertionError('No anonymous S3 request')):
            result = await s3_utils.read_file_bytes('https://install-test.s3.amazonaws.com/folder/my%20file.txt')
            self.assertEqual(result, b'synthetic fixture')
            client.get_object.assert_awaited_once_with(Bucket='install-test', Key='folder/my file.txt')

    async def test_master_key_does_not_resolve_hosted_session(self):
        from utils.auth import dependencies
        request = Mock()
        with patch.object(dependencies, 'API_KEY_NAME', 'test-master-key'), patch.object(dependencies, 'get_auth_details', new_callable=AsyncMock) as hosted:
            hosted.side_effect = AssertionError('Hosted identity must not be called')
            auth = await dependencies.get_api_key_or_session(request, 'test-master-key')
            self.assertEqual(auth['auth_type'], 'master_api_key')
            hosted.assert_not_called()

    async def test_invalid_stored_key_does_not_select_installation_key(self):
        from utils.usage.credentials import get_credential_for_owner
        session = AsyncMock()
        result = Mock()
        result.scalar_one_or_none.return_value = Mock(is_valid=False)
        session.execute.return_value = result
        context = AsyncMock()
        context.__aenter__.return_value = session
        with patch.dict(os.environ, {"BYOK_PLATFORM_FALLBACK": "false"}), patch('utils.db.session.async_session', return_value=context):
            with self.assertRaises(ValueError):
                await get_credential_for_owner('owner@example.test', 'openai')


if __name__ == '__main__':
    unittest.main()
