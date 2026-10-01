"""Print the OpenAPI document (committed as `docs/api/openapi.json`; `make openapi` refreshes it).

The contract test `tests/unit/test_openapi.py` fails when the API changes without a refresh.
"""

import json
import sys
from typing import Any

from samvidhan.api.main import create_app
from samvidhan.core.config import Settings


def openapi_document(settings: Settings) -> dict[str, Any]:
    return create_app(settings).openapi()  # no lifespan: nothing is loaded


def main() -> int:
    settings = Settings(_env_file=None)
    sys.stdout.write(json.dumps(openapi_document(settings), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
