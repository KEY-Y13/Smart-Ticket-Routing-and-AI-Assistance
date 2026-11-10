"""Generate review-only MySQL SQL. This script never connects to a MySQL server."""

import argparse
import io
from pathlib import Path

from alembic.config import Config

from alembic import command

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError("拒绝覆盖已有审查 SQL")
    root = Path(__file__).resolve().parents[1]
    buffer = io.StringIO()
    cfg = Config(str(root / "alembic.ini"), output_buffer=buffer)
    command.upgrade(cfg, "head", sql=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        handle.write("-- REVIEW ONLY: not executed. No DROP/DELETE/TRUNCATE.\n")
        handle.write("-- Select the dedicated NEW empty database before applying.\n")
        handle.write(buffer.getvalue())
    print(args.out)
