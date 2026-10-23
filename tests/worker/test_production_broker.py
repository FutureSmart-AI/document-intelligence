"""Production may use ECS workload credentials without embedding static keys."""
import importlib
import os
import unittest
from unittest.mock import patch

from kombu import Connection
from services import celery_setup_env


class ProductionBrokerTests(unittest.TestCase):
    def broker(self, explicit, access, secret):
        with patch.dict(os.environ, {'CELERY_BROKER_URL': explicit}), patch.object(celery_setup_env, 'MODE', 'PROD'), patch.object(celery_setup_env, 'env_file', '.env.prod'), patch.object(celery_setup_env, 'SQS_AWS_ACCESS_KEY', access), patch.object(celery_setup_env, 'SQS_AWS_SECRET_KEY', secret):
            from celery_workers import config
            return importlib.reload(config).BROKER_URL

    def test_production_role_url_keeps_aws_credentials_out_of_broker(self):
        url = self.broker('sqs://', None, None)
        connection = Connection(url)
        self.assertIsNone(connection.userid)
        self.assertIsNone(connection.password)
        self.assertNotIn('None', url)

    def test_existing_explicit_credentials_are_preserved_when_no_override(self):
        url = self.broker('', 'test-access', 'test/secret+value')
        connection = Connection(url)
        self.assertEqual(connection.userid, 'test-access')
        self.assertEqual(connection.password, 'test/secret+value')


if __name__ == '__main__':
    unittest.main()
