"""The dashboard server (Part 6)."""

from __future__ import annotations

import os
import threading

from .audit import post_mortem
from .checker import read_verdict
from .journal import Journal
from .view import derive_state

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

DEFAULT_PANES = {
    "pinned": "demo-pinned.db",
    "naive": "demo-naive.db",
    "palimpsest": "demo-palimpsest.db",
}


# Scenarios the distributed path can genuinely express. The others need engine
# parameters run_orchestrator.py does not expose -- a divergence trigger (residue),
# a crash policy (crash), or faults injected partway through a run (compfail,
# compretry) -- so they are refused rather than silently run as poison.
_NO_FAULTS = {
    "fail_tools": [], "timeout_tools": [], "late_delivery_tools": [],
    "down_services": [], "fail_compensation_tools": [], "empty_rotas": [],
    "latency_s": 0.0, "jitter_s": 0.0,
}
LIVE_SCENARIOS = {
    "poison": dict(_NO_FAULTS, empty_rotas=["rota-X"]),
    "zombie": dict(_NO_FAULTS, timeout_tools=["page_oncall"],
                   late_delivery_tools=["page_oncall"], late_delivery_delay_s=8.0),
    "redelivery": dict(_NO_FAULTS),
}


def _scenario_names() -> list[str]:
    """Advertised on /api/health so the page can populate its dropdown from the
    registry rather than a hardcoded copy that drifts.  Imported lazily and
    defensively: listing scenarios must not be able to break the read path."""
    try:
        from .scenarios import SCENARIOS

        return list(SCENARIOS)
    except Exception:
        return []


def _open_read_only(db: str) -> Journal:
    """Read-only handle, so the dashboard cannot touch a journal even by accident."""
    try:
        return Journal(db, read_only=True)
    except Exception:
        return Journal(db)


def _pane_state(mode: str, db: str) -> dict:
    if not os.path.exists(db):
        return {"mode": mode, "db": db, "missing": True, "empty": True}
    journal = _open_read_only(db)
    try:
        wf = journal.latest_workflow()
        if wf is None:
            return {"mode": mode, "db": db, "missing": False, "empty": True}
        state = derive_state(journal, wf, mode=mode)
        state["db"] = db
        state["missing"] = False
        # Grading needs the ledger, which this process does not have. Display only.
        state["verdict"] = read_verdict(db, workflow_id=wf)
        return state
    finally:
        journal.close()


def create_app(panes: dict[str, str] | None = None, allow_control: bool = False,
               live_db: str | None = None):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, PlainTextResponse

    panes = panes or dict(DEFAULT_PANES)
    app = FastAPI(title="palimpsest-dashboard")
    lock = threading.Lock()
    running: dict[str, str] = {}

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))

    @app.get("/api/state")
    def state():
        out = {
            "panes": {mode: _pane_state(mode, db) for mode, db in panes.items()},
            "order": list(panes),
        }
        # The distributed run writes a different journal (run_orchestrator --db).
        if live_db:
            live = _pane_state("palimpsest", live_db)
            live["is_live"] = True
            out["live"] = live
        return out

    @app.get("/api/audit", response_class=PlainTextResponse)
    def audit(mode: str):
        db = panes.get(mode)
        if not db or not os.path.exists(db):
            raise HTTPException(404, f"no journal for mode {mode}")
        journal = _open_read_only(db)
        try:
            wf = journal.latest_workflow()
            if wf is None:
                raise HTTPException(404, "journal is empty")
            return post_mortem(journal, wf, mode=mode)
        finally:
            journal.close()

    @app.get("/api/health")
    def health():
        return {
            "panes": {m: {"db": db, "exists": os.path.exists(db)} for m, db in panes.items()},
            "control_enabled": allow_control,
            "scenarios": sorted(_scenario_names()),
            "live_scenarios": sorted(LIVE_SCENARIOS),
            "live_db": live_db,
            "live_exists": bool(live_db and os.path.exists(live_db)),
            "running": dict(running),
        }

    if allow_control:
        # Convenience only.  The read path above never consults this.
        from .scenarios import SCENARIOS, run_scenario

        @app.post("/api/run")
        def run(scenario: str = "poison", step_pause_s: float = 0.0):
            if scenario not in SCENARIOS:
                raise HTTPException(400, f"unknown scenario {scenario}")

            def worker():
                for mode, db in panes.items():
                    with lock:
                        running[mode] = scenario
                    try:
                        run_scenario(
                            scenario, mode, db, opts={"step_pause_s": step_pause_s}
                        )
                    finally:
                        with lock:
                            running.pop(mode, None)

            threading.Thread(target=worker, daemon=True).start()
            return {"launched": scenario, "panes": list(panes)}

        @app.post("/api/run_live")
        def run_live(scenario: str = "poison", mode: str = "palimpsest",
                     count: int = 1, fresh: bool = True):
            """Drive the DISTRIBUTED path: producer -> Redis -> orchestrator -> HTTP."""
            import subprocess
            import sys

            if not live_db:
                raise HTTPException(400, "dashboard started without --live-db")
            if scenario not in LIVE_SCENARIOS:
                raise HTTPException(
                    400,
                    f"{scenario} cannot run over the distributed path: it needs engine"
                    f" parameters run_orchestrator.py does not expose."
                    f" Available: {', '.join(sorted(LIVE_SCENARIOS))}",
                )
            if mode not in ("palimpsest", "pinned", "naive"):
                raise HTTPException(400, f"unknown mode {mode}")

            def worker():
                with lock:
                    running["live"] = "distributed"
                try:
                    if fresh:
                        # Reset the oracle too, or it counts every earlier run as a duplicate.
                        try:
                            from .http_world import HttpLedger, HttpWorld

                            world = HttpWorld()
                            oracle = HttpLedger()
                            world.reset()
                            oracle.reset()
                            # Always the full set, so a previous scenario's faults
                            # are cleared rather than left to bleed into this run.
                            for svc in ("ticket", "channel", "pager"):
                                world.set_faults(svc, LIVE_SCENARIOS[scenario])
                            world.close()
                            oracle.close()
                        except Exception:
                            # Services down: the orchestrator below will say so.
                            pass
                        for suffix in ("", "-wal", "-shm", ".verdict.json"):
                            try:
                                os.remove(live_db + suffix)
                            except OSError:
                                pass
                    py = sys.executable
                    pub = [py, "run_producer.py", "--demo", "--count", str(count)]
                    if scenario == "redelivery":
                        pub.append("--redeliver")
                    subprocess.run(pub, capture_output=True, timeout=60)
                    subprocess.run([py, "run_orchestrator.py", "--once", "--source", "redis",
                                    "--world", "http", "--db", live_db, "--mode", mode],
                                   capture_output=True, timeout=180)
                except Exception:
                    pass
                finally:
                    with lock:
                        running.pop("live", None)

            threading.Thread(target=worker, daemon=True).start()
            return {"launched": scenario, "mode": mode, "db": live_db}

    return app
