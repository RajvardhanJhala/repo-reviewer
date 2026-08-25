"""Run the webhook receiver + local dashboard: python -m api [--port 8000]"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from api.webhook import create_app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="api")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--repo", type=Path, default=Path("."),
                    help="checkout the dashboard reviews and answers questions about")
    ap.add_argument("--data", type=Path, default=None)
    args = ap.parse_args()
    data = args.data or Path("data") / args.repo.resolve().name
    print(f"dashboard: http://127.0.0.1:{args.port}/  (repo={args.repo.resolve()})")
    create_app(repo_path=args.repo, data_dir=data).run(host="127.0.0.1", port=args.port)
