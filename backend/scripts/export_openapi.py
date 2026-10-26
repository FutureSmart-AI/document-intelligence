"""Export this checkout's API contract without starting a server or using a database."""
import json
import sys
from pathlib import Path

from main import app

if __name__ == '__main__':
    destination = Path(sys.argv[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(app.openapi(), indent=2) + '\n', encoding='utf-8')
