#!/usr/bin/env python3
"""Serve the dashboard (Part 6)."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PALIMPSEST dashboard")
    ap.add_argument("--db-dir", default=".palimpsest")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--allow-control", action="store_true",
                    help="mount POST /api/run so the page can launch scenarios itself")
    ap.add_argument("--live-db", default=".palimpsest/shared.db",
                    help="journal written by run_orchestrator.py; shown as the live"
                         " distributed pane. '' to hide it")
    args = ap.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print("the dashboard needs fastapi + uvicorn:  pip install -r requirements.txt",
              file=sys.stderr)
        return 2

    from palimpsest.dashboard import create_app

    panes = {
        mode: os.path.join(args.db_dir, f"demo-{mode}.db")
        for mode in ("pinned", "naive", "palimpsest")
    }
    os.makedirs(args.db_dir, exist_ok=True)

    print(f"  dashboard  http://{args.host}:{args.port}")
    print(f"  reading    {args.db_dir}/demo-*.db")
    if args.live_db:
        print(f"  live pane  {args.live_db}"
              f"   (python run_orchestrator.py --db {args.live_db})")
    if args.allow_control:
        print("  control    ON  -- the page can launch scenarios itself")
    else:
        print("  control    off -- restart with --allow-control for the Run button")
    print(f"  drive it   python demo.py --db-dir {args.db_dir} --slow 0.35\n")

    uvicorn.run(
        create_app(panes, allow_control=args.allow_control,
                   live_db=args.live_db or None),
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
