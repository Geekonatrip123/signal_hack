from __future__ import annotations

from .journal import Journal
from .world import GroundTruthLedger


def check_eeo(journal: Journal, ledger: GroundTruthLedger, workflow_id: str) -> dict:
    recs = journal.records(workflow_id)
    effects = ledger.effects()
    comps = ledger.compensations()
    violations = []

    seen = {}
    for e in effects:
        seen[e["key"]] = seen.get(e["key"], 0) + 1
    for key, n in seen.items():
        if n > 1:
            violations.append(("no_duplication", f"key {key} committed {n} times"))

    pages = [e for e in effects if e["tool"] == "page_oncall"]
    rotas = {}
    for p in pages:
        rotas[p["args"].get("rota")] = rotas.get(p["args"].get("rota"), 0) + 1
    if len([r for r in rotas if rotas[r] > 0]) > 1:
        violations.append(("no_duplication", f"paged multiple rotas: {rotas}"))

    kinds = {r.kind for r in recs}
    terminal = bool(pages) or "ESCALATED" in kinds
    if not terminal:
        violations.append(("no_loss", "workflow ended with neither action nor escalation"))

    pending = journal.uncompensated(workflow_id)
    if pending:
        violations.append(
            ("clean_abandonment", f"{len(pending)} uncompensated: {[p.tool_name for p in pending]}")
        )

    active = {b.branch_id for b in journal.branches(workflow_id) if b.status == "active"}
    comp_keys = {c["key"] for c in comps}
    for r in recs:
        if r.kind == "RESULT" and r.branch_id in active and r.key in comp_keys:
            violations.append(("clean_abandonment", f"compensated live effect {r.tool_name}"))

    ba = [r for r in recs if r.kind == "RESULT" and r.result and r.result.status == "unknown"]
    if len(ba) > 1:
        violations.append(("bounded_ambiguity", f"{len(ba)} effects in unknown state"))

    return {
        "workflow_id": workflow_id,
        "pass": not violations,
        "violations": violations,
        "ba_surfaced": len(ba),
        "counts": ledger.counts(),
    }


def evidence_table(results: list[dict]) -> str:
    total = len(results)
    by_clause = {"no_duplication": 0, "no_loss": 0, "clean_abandonment": 0, "bounded_ambiguity": 0}
    ba = 0
    for r in results:
        ba += r["ba_surfaced"]
        for clause, _ in r["violations"]:
            by_clause[clause] = by_clause.get(clause, 0) + 1
    lines = [
        "CRASH SWEEP RESULTS",
        f"  total runs:                {total}",
        f"  EEO clause 1 (no dup):     pass {total - by_clause['no_duplication']} / {total}",
        f"  EEO clause 2 (no loss):    pass {total - by_clause['no_loss']} / {total}",
        f"  EEO clause 3 (clean abd):  pass {total - by_clause['clean_abandonment']} / {total}",
        f"  BA surfaced (unknown):     {ba}",
        f"  unexplained violations:    {sum(by_clause.values())}",
    ]
    return "\n".join(lines)
