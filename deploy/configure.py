"""Generate private installation secrets; provider keys come from environment only."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
PROFILES = Path(__file__).resolve().with_name('model-profiles.json')


def load_profile(name):
    catalog = json.loads(PROFILES.read_text(encoding='utf-8'))
    name = name or catalog['default']
    if name not in catalog['profiles']:
        raise SystemExit(f'Unknown model profile {name!r}; choose one of {sorted(catalog["profiles"])}')
    return name, catalog['profiles'][name]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', help='Model profile from deploy/model-profiles.json (default: openai-v1)')
    args = parser.parse_args()
    target = ROOT / '.env'
    if target.exists():
        raise SystemExit('.env exists; preserve your installation secrets and edit it directly')
    profile_name, profile = load_profile(args.profile)
    missing = [key for key in profile['required_keys'] if not os.getenv(key)]
    if missing:
        raise SystemExit(f'Model profile {profile_name} needs {", ".join(missing)} in your environment')
    values = {
        'FASTAPI_SECRET_KEY': secrets.token_urlsafe(32),
        'POSTGRES_PASSWORD': secrets.token_urlsafe(24),
        'STANDALONE_OWNER_EMAIL': os.getenv('STANDALONE_OWNER_EMAIL', 'owner@example.test'),
        'STANDALONE_PASSWORD': os.getenv('STANDALONE_PASSWORD') or secrets.token_urlsafe(24),
        'USAGE_CREDENTIALS_MASTER_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'USAGE_CREDENTIALS_HASH_SALT': secrets.token_urlsafe(32),
        'CONNECTOR_SECRET_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'OPENAI_API_KEY': os.environ['OPENAI_API_KEY'],
        'GROQ_API_KEY': os.getenv('GROQ_API_KEY', ''),
        'ANTHROPIC_API_KEY': os.getenv('ANTHROPIC_API_KEY', ''),
        'MODEL_PROFILE': profile_name,
        **profile['settings'],
    }
    for key in ['LLAMA_PARSE_API_KEY', 'FIRECRAWL_API_KEY']:
        values[key] = os.getenv(key, '')
    if any('\n' in v or '\r' in v for v in values.values()):
        raise SystemExit('Configuration values must be single-line')
    target.write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')
    target.chmod(0o600)
    print(f'Created private .env with model profile {profile_name}. '
          'Read STANDALONE_PASSWORD there to sign into the UI.')
