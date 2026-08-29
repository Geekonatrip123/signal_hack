#!/usr/bin/env python3
"""Launch the HTTP topology: three effect services plus the ground-truth ledger (3.1)."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

from palimpsest.http_world import DEFAULT_PORTS

SERVICES = ["ledger", "ticket", "channel", "pager"]


def serve_one(name: str, port: int, ledger_url: str, host: str) -> None:
    import uvicorn

    from palimpsest.services import build_app

    app = build_app(name, ledger_url=ledger_url)
    print(f"[{name}] listening on http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def serve_all(host: str, ledger_url: str) -> int:
    procs: list[tuple[str, subprocess.Popen]] = []
    here = os.path.abspath(__file__)

    for name in SERVICES:
        cmd = [
            sys.executable, here,
            "--service", name,
            "--port", str(DEFAULT_PORTS[name]),
            "--host", host,
            "--ledger-url", ledger_url,
        ]
        p = subprocess.Popen(cmd)
        procs.append((name, p))
        print(f"  {name:<8} pid {p.pid}  http://{host}:{DEFAULT_PORTS[name]}")
        # The ledger must be up before the effect services try to write to it.
        time.sleep(0.6 if name == "ledger" else 0.25)

    print("\n  all four up. ctrl-c to stop, or kill one pid to break a service.")
    print("  then:  python demo.py --world http\n")

    try:
        while True:
            time.sleep(1.0)
            for name, p in procs:
                if p.poll() is not None:
                    print(f"  [{name}] exited with {p.returncode}")
    except KeyboardInterrupt:
        print("\n  stopping...")
    finally:
        for _name, p in procs:
            if p.poll() is None:
                try:
                    p.send_signal(signal.SIGTERM)
                except Exception:
                    p.kill()
        for _name, p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PALIMPSEST effect services")
    ap.add_argument("--service", choices=SERVICES, default=None,
                    help="run a single service in the foreground")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ledger-url", default=None)
    args = ap.parse_args(argv)

    ledger_url = args.ledger_url or f"http://{args.host}:{DEFAULT_PORTS['ledger']}"

    try:
        import uvicorn  # noqa: F401
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
    except ImportError as e:
        print(f"the HTTP topology needs the optional deps: {e}", file=sys.stderr)
        print("  pip install -r requirements.txt", file=sys.stderr)
        return 2

    if args.service:
        serve_one(args.service, args.port or DEFAULT_PORTS[args.service], ledger_url, args.host)
        return 0
    return serve_all(args.host, ledger_url)


if __name__ == "__main__":
    sys.exit(main())
