from __future__ import annotations

import os
import sys

from palimpsest import (
    Alert,
    FaultConfig,
    GroundTruthLedger,
    InProcessWorld,
    Journal,
    Orchestrator,
    check_eeo,
    evidence_table,
)
from palimpsest.types import workflow_id_for

ALERT = Alert("a-1001", "payments-api", "prometheus", {"msg": "error rate spike"})


def run_mode(mode: str, db: str) -> dict:
    if os.path.exists(db):
        os.remove(db)
    j = Journal(db)
    ledger = GroundTruthLedger()
    faults = FaultConfig()
    faults.empty_rotas = {"rota-X"}
    world = InProcessWorld(ledger, faults)
    orch = Orchestrator(j, world, owner=f"orch-{mode}", mode=mode)

    if mode == "pinned":
        for _ in range(3):
            orch.run(ALERT, "P2")
        out = {"outcome": "livelocked", "workflow_id": workflow_id_for(ALERT.alert_id)}
    elif mode == "naive":
        out = orch.run(ALERT, "P2")
        wf = workflow_id_for(ALERT.alert_id)
        b = j.active_branch(wf)
        if b:
            j.set_branch_status(b.branch_id, "abandoned")
        world.applied.clear()
        b2 = j.create_branch(wf, depth=0)
        out = orch._run_branch(wf, b2, ALERT, "P1")
    else:
        out = orch.run(ALERT, "P2")

    verdict = check_eeo(j, ledger, out.get("workflow_id", ""))
    return {"mode": mode, "result": out, "counts": ledger.counts(),
            "gross": ledger.gross_counts(), "verdict": verdict, "journal": j}


def summarise(r: dict) -> None:
    c = r["counts"]
    print(f"\n=== {r['mode'].upper()} ===")
    print(f"  outcome:       {r['result'].get('outcome')}")
    print(f"  tickets:       {c.get('create_ticket', 0)}")
    print(f"  channel posts: {c.get('post_to_channel', 0)}")
    print(f"  pages sent:    {c.get('page_oncall', 0)}")
    g = r["gross"]
    if g != c:
        print(f"  (gross before compensation: {g.get('create_ticket',0)}/"
              f"{g.get('post_to_channel',0)}/{g.get('page_oncall',0)})")
    for e in r["journal"].records(r["result"].get("workflow_id", "")):
        if e.kind in ("BRANCH_FORKED", "BRANCH_ABANDONED", "BARRIER_BLOCKED",
                      "BARRIER_RELEASED", "ESCALATED"):
            print(f"    {e.kind:20} {e.detail}")


if __name__ == "__main__":
    results = []
    for mode in ["pinned", "naive", "palimpsest"]:
        try:
            r = run_mode(mode, f"/tmp/pal-{mode}.db")
            summarise(r)
            if mode == "palimpsest":
                results.append(r["verdict"])
        except Exception as exc:
            print(f"\n=== {mode.upper()} ===\n  halted: {exc}")
    print()
    print("baselines are expected to violate EEO; that is the point of showing them")
    print(evidence_table(results))
