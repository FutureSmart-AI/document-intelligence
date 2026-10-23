import os
import unittest
from unittest.mock import Mock, patch

from celery_utils.shared.credentials_utils import get_api_key, platform_fallback_allowed


class StandaloneCredentialTests(unittest.TestCase):
    @patch.dict(os.environ, {"BYOK_PLATFORM_FALLBACK": "false"})
    def test_terminal_owner_failure_cannot_switch_to_another_model_key(self):
        from celery_utils.enrichment.llm_retry import invoke_with_retry
        class Rejected(Exception):
            status_code = 401
        llm = Mock()
        llm.invoke.side_effect = Rejected('invalid_api_key')
        fallback = Mock()
        with self.assertRaises(Rejected):
            invoke_with_retry(llm, [{'role': 'user', 'content': 'synthetic fixture'}], max_retries=1, key_type='owner-key', fallback=fallback)
        fallback.llm.invoke.assert_not_called()

    @patch.dict(os.environ, {"BYOK_PLATFORM_FALLBACK": "false"})
    @patch('celery_utils.shared.credentials_utils.requests.get')
    def test_rejected_owner_key_never_uses_installation_key(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"is_valid": False, "api_key": ""}
        with self.assertRaises(ValueError):
            get_api_key('owner@example.test', 'openai', 'must-not-use')
        self.assertFalse(platform_fallback_allowed('owner@example.test'))

    @patch.dict(os.environ, {"BYOK_PLATFORM_FALLBACK": "false"})
    @patch('celery_utils.shared.credentials_utils.requests.get')
    def test_broken_callback_never_uses_installation_key(self, get):
        get.return_value = Mock(status_code=500)
        with self.assertRaises(ValueError):
            get_api_key('owner@example.test', 'openai', 'must-not-use')

    @patch.dict(os.environ, {"BYOK_PLATFORM_FALLBACK": "false"})
    @patch('celery_utils.shared.credentials_utils.requests.get')
    def test_absent_owner_key_uses_explicit_installation_key(self, get):
        get.return_value = Mock(status_code=404)
        self.assertEqual(get_api_key('owner@example.test', 'openai', 'installation-key'), ('installation-key', 'platform-key'))


if __name__ == '__main__':
    unittest.main()
