"""Run the webhook receiver: python -m api [--port 8000]"""
from __future__ import annotations

import argparse
import logging

from api.webhook import create_app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="api")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    create_app().run(host="127.0.0.1", port=args.port)
