"""Export the current API contract without loading database credentials or connecting."""

import argparse
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.main import create_app
from nlp.artifacts import write_json

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(Settings(env="test", database_url=None, jwt_secret=None, llm_enabled=False))
    write_json(args.out, app.openapi())
    print(args.out)
