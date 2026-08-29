#!/usr/bin/env python3
"""One orchestrator, as its own OS process (3.1, 3.4)."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from palimpsest.checker import check_eeo, explain, write_verdict
from palimpsest.engine import LEASE_TTL_S, Orchestrator, recover
from palimpsest.ingest import alert_source
from palimpsest.journal import Journal, LeaseUnavailable
from palimpsest.types import Alert, workflow_id_for

_STOP = False


def _install_signal_handlers():
    def stop(_sig, _frm):
        global _STOP
        _STOP = True
        print("\n  [stopping after the current alert]", flush=True)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, stop)
        except Exception:
            pass


def build_world(kind: str, ledger_holder: dict):
    if kind == "http":
        from palimpsest.http_world import HttpLedger, HttpWorld

        world = HttpWorld()
        health = world.health()
        down = [s for s, v in health["services"].items() if v != "up"]
        if down:
            print(f"  WARNING: services down: {down}. Start them: python run_services.py")
        ledger_holder["ledger"] = HttpLedger()
        return world

    from palimpsest.world import FaultConfig, GroundTruthLedger, InProcessWorld

    ledger = GroundTruthLedger()
    ledger_holder["ledger"] = ledger
    return InProcessWorld(ledger, FaultConfig())


def handle(alert: Alert, journal, world, args, ledger) -> dict:
    def narrate(ev: dict) -> None:
        print(
            f"    [{args.owner}] {ev['kind']}"
            f" {ev.get('tool') or ev.get('reason') or ''}",
            flush=True,
        )

    started = time.time()
    out = recover(
        journal,
        world,
        alert,
        args.severity,
        mode=args.mode,
        owner=args.owner,
        max_attempts=args.max_attempts,
        lease_ttl_s=args.lease_ttl,
        on_event=narrate if args.narrate else None,
    )
    elapsed = time.time() - started

    outcome = out.get("outcome")
    if outcome == "not_leader":
        # The other orchestrator holds the lease.
        print(f"  {alert.alert_id}: not leader ({out.get('reason', '')[:60]})", flush=True)
        return out

    verdict = check_eeo(journal, ledger, out.get("workflow_id", ""), outcome=outcome or "")
    # Beside the journal, for the dashboard's live pane.
    write_verdict(args.db, verdict, mode=args.mode)
    flag = "PASS" if verdict["pass"] else "FAIL"
    print(
        f"  {alert.alert_id}: {outcome:<11} rota={out.get('rota') or '-':<8}"
        f" {elapsed * 1000:7.0f}ms  EEO {flag}",
        flush=True,
    )
    if not verdict["pass"]:
        print(explain(verdict), flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PALIMPSEST orchestrator process")
    ap.add_argument("--owner", default="orch-a", help="lease owner id; make it unique")
    ap.add_argument("--db", default=".palimpsest/shared.db",
                    help="shared journal; both orchestrators must point at the same file")
    ap.add_argument("--world", default="http", choices=["http", "inprocess"])
    ap.add_argument("--source", default="redis", choices=["redis", "inprocess"])
    ap.add_argument("--mode", default="palimpsest", choices=["palimpsest", "pinned", "naive"])
    ap.add_argument("--severity", default="P2")
    ap.add_argument("--lease-ttl", type=float, default=LEASE_TTL_S)
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--claim-idle-ms", type=int, default=8000,
                    help="reclaim entries a dead consumer left pending for this long")
    ap.add_argument("--poll-s", type=float, default=1.0)
    ap.add_argument("--once", action="store_true", help="drain what is queued, then exit")
    ap.add_argument("--narrate", action="store_true")
    ap.add_argument("--empty-rota", default="rota-X",
                    help="poison the demo rota (in-process world only); '' to disable")
    args = ap.parse_args(argv)

    _install_signal_handlers()
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)

    holder: dict = {}
    world = build_world(args.world, holder)
    ledger = holder["ledger"]

    if args.world == "inprocess" and args.empty_rota:
        world.faults.empty_rotas = {args.empty_rota}

    journal = Journal(args.db)
    source = alert_source(
        use_redis=(args.source == "redis"), consumer=args.owner, verbose=True
    )

    print(f"\n  orchestrator {args.owner}  pid {os.getpid()}")
    print(f"  journal  {args.db}")
    print(f"  world    {args.world}   source {getattr(source, 'name', args.source)}")
    print(f"  mode     {args.mode}   lease ttl {args.lease_ttl}s\n", flush=True)

    handled = 0
    try:
        while not _STOP:
            work = 0

            # Entries some other consumer claimed and never acknowledged.
            for alert in source.claim_stale(args.claim_idle_ms):
                print(f"  reclaimed {alert.alert_id} from a stalled consumer", flush=True)
                out = handle(alert, journal, world, args, ledger)
                if out.get("outcome") != "not_leader":
                    source.ack(alert.alert_id)
                work += 1
                handled += 1

            for alert in source.consume():
                out = handle(alert, journal, world, args, ledger)
                # Ack only after the handler returns.
                if out.get("outcome") != "not_leader":
                    source.ack(alert.alert_id)
                work += 1
                handled += 1
                if _STOP:
                    break

            if args.once and work == 0:
                break
            if work == 0:
                time.sleep(args.poll_s)
    except KeyboardInterrupt:
        pass
    finally:
        stats = source.stats() if hasattr(source, "stats") else {}
        print(f"\n  {args.owner}: handled {handled} alert(s)")
        if stats:
            print(f"  stream depth {stats.get('depth')}  lag {stats.get('lag')}"
                  f"  pending {len(stats.get('pending') or [])}")
        lease = journal.lease_info(workflow_id_for("a-1001"))
        if lease:
            print(f"  last demo-workflow lease: {lease.owner} @ epoch {lease.epoch}")
        journal.close()
        source.close()
        if hasattr(world, "close"):
            world.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
