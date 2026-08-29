from __future__ import annotations

import time

from .journal import Journal
from .tools import EFFECT_TYPES, trace_for
from .types import Alert, ToolResult, effect_key, workflow_id_for

MAX_FORK_DEPTH = 3
MAX_COMP_ATTEMPTS = 3
BARRIER_DEADLINE_S = 5.0
TIMEOUT_S = 2.0


class Escalation(Exception):
    def __init__(self, reason: str, detail: dict):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class Orchestrator:
    def __init__(self, journal: Journal, world, owner: str = "orch-a", mode: str = "palimpsest"):
        self.j = journal
        self.world = world
        self.owner = owner
        self.mode = mode
        self.epoch = 1

    def acquire(self, workflow_id: str) -> None:
        self.epoch = self.j.acquire_lease(workflow_id, self.owner)

    def barrier_check(self, workflow_id: str) -> tuple[bool, list]:
        pending = self.j.uncompensated(workflow_id)
        return (len(pending) == 0, pending)

    def compensate_abandoned(self, workflow_id: str, branch_id: str) -> bool:
        deadline = time.time() + BARRIER_DEADLINE_S
        for attempt in range(MAX_COMP_ATTEMPTS):
            pending = self.j.uncompensated(workflow_id)
            if not pending:
                return True
            for rec in sorted(pending, key=lambda r: r.seq, reverse=True):
                if time.time() > deadline:
                    return False
                self.j.append(
                    workflow_id,
                    rec.branch_id,
                    rec.seq,
                    "COMP_INTENT",
                    tool_name=rec.tool_name,
                    effect_type=rec.effect_type,
                    args=rec.args,
                    key=rec.key,
                    epoch=self.epoch,
                )
                res = self.world.compensate(rec.tool_name, rec.args, rec.key, self.epoch, TIMEOUT_S)
                self.j.append(
                    workflow_id,
                    rec.branch_id,
                    rec.seq,
                    "COMP_RESULT",
                    tool_name=rec.tool_name,
                    effect_type=rec.effect_type,
                    args=rec.args,
                    key=rec.key,
                    epoch=self.epoch,
                    result=res,
                    detail={"attempt": attempt + 1},
                )
            time.sleep(0.05)

        return not self.j.uncompensated(workflow_id)

    def _mark_abandoned(self, workflow_id: str, branch_id: str) -> None:
        residue = [r for r in self.j.residue(workflow_id) if r.branch_id == branch_id]
        status = "abandoned_with_residue" if residue else "abandoned"
        self.j.set_branch_status(branch_id, status)
        self.j.append(
            workflow_id,
            branch_id,
            self.j.next_seq(branch_id),
            "BRANCH_ABANDONED",
            detail={"residue": [r.tool_name for r in residue], "status": status},
        )

    def _tried_alternatives(self, workflow_id: str) -> set[str]:
        out = set()
        for r in self.j.records(workflow_id):
            if r.kind == "RESULT" and r.tool_name == "classify" and r.args:
                out.add(r.args.get("severity"))
        return out

    def run(self, alert: Alert, severity: str = "P2") -> dict:
        workflow_id = workflow_id_for(alert.alert_id)
        self.acquire(workflow_id)

        branch = self.j.active_branch(workflow_id)
        if branch is None:
            branch = self.j.create_branch(workflow_id)

        while True:
            try:
                return self._run_branch(workflow_id, branch, alert, severity)
            except Escalation as e:
                self.j.append(
                    workflow_id,
                    branch.branch_id,
                    self.j.next_seq(branch.branch_id),
                    "ESCALATED",
                    detail={"reason": e.reason, **e.detail},
                )
                return {"outcome": "escalated", "reason": e.reason, "workflow_id": workflow_id}
            except _Diverge as d:
                if self.mode != "palimpsest":
                    return {"outcome": "failed", "reason": d.reason, "workflow_id": workflow_id}

                if branch.depth + 1 > MAX_FORK_DEPTH:
                    self.j.append(
                        workflow_id,
                        branch.branch_id,
                        self.j.next_seq(branch.branch_id),
                        "ESCALATED",
                        detail={"reason": "max fork depth reached", "depth": branch.depth},
                    )
                    return {"outcome": "escalated", "reason": "fork bound", "workflow_id": workflow_id}

                tried = self._tried_alternatives(workflow_id)
                alternatives = [a for a in ["P1", "P2", "P3"] if a not in tried]
                if not alternatives:
                    self.j.append(
                        workflow_id,
                        branch.branch_id,
                        self.j.next_seq(branch.branch_id),
                        "ESCALATED",
                        detail={"reason": "alternatives exhausted"},
                    )
                    return {"outcome": "escalated", "reason": "no alternatives", "workflow_id": workflow_id}

                self._mark_abandoned(workflow_id, branch.branch_id)
                new_branch = self.j.create_branch(
                    workflow_id, branch.branch_id, d.fork_point_record_id, branch.depth + 1
                )
                self.j.append(
                    workflow_id,
                    new_branch.branch_id,
                    0,
                    "BRANCH_FORKED",
                    detail={
                        "parent": branch.branch_id,
                        "fork_point": d.fork_point_record_id,
                        "reason": d.reason,
                        "new_severity": alternatives[0],
                        "depth": new_branch.depth,
                    },
                )
                branch, severity = new_branch, alternatives[0]

    def _run_branch(self, workflow_id: str, branch, alert: Alert, severity: str) -> dict:
        steps = trace_for(alert, severity)
        done = self.j.completed_results(branch.branch_id)
        classify_record_id = None

        for seq, step in enumerate(steps):
            tool = step["tool"]
            et = EFFECT_TYPES[tool]

            if seq in done:
                if tool == "classify":
                    classify_record_id = done[seq].record_id
                continue

            key = effect_key(workflow_id, branch.branch_id, seq)

            if et.reversibility == "irreversible" and self.mode == "palimpsest":
                ok, pending = self.barrier_check(workflow_id)
                if not ok:
                    self.j.append(
                        workflow_id,
                        branch.branch_id,
                        seq,
                        "BARRIER_BLOCKED",
                        tool_name=tool,
                        effect_type=et,
                        key=key,
                        epoch=self.epoch,
                        detail={
                            "reason": f"{len(pending)} uncompensated effects on abandoned branch",
                            "count": len(pending),
                            "effects": [p.tool_name for p in pending],
                        },
                    )
                    cleaned = self.compensate_abandoned(workflow_id, branch.branch_id)
                    if not cleaned:
                        still = self.j.uncompensated(workflow_id)
                        raise Escalation(
                            "compensation exhausted, barrier not satisfied",
                            {"uncompensated": [p.tool_name for p in still], "blocked_tool": tool},
                        )
                    for b in self.j.branches(workflow_id):
                        if b.status == "abandoned":
                            self.j.set_branch_status(b.branch_id, "compensated")
                    self.j.append(
                        workflow_id,
                        branch.branch_id,
                        seq,
                        "BARRIER_RELEASED",
                        tool_name=tool,
                        effect_type=et,
                        key=key,
                        epoch=self.epoch,
                        detail={"count": 0},
                    )

            rid = self.j.append(
                workflow_id, branch.branch_id, seq, "INTENT",
                tool_name=tool, effect_type=et, args=step["args"], key=key, epoch=self.epoch,
            )

            res = self.world.execute(tool, step["args"], key, self.epoch, TIMEOUT_S)

            if res.status == "unknown":
                probe = self.world.probe(tool, key, TIMEOUT_S)
                res = ToolResult("ok", {"probed": "done"}) if probe == "done" else res
                if res.status == "unknown":
                    self.j.append(
                        workflow_id, branch.branch_id, seq, "RESULT",
                        tool_name=tool, effect_type=et, args=step["args"], key=key,
                        epoch=self.epoch, result=res, detail={"probe": probe},
                    )
                    raise Escalation(
                        f"{tool} in unknown state, bounded ambiguity surfaced",
                        {"key": key, "tool": tool, "probe": probe},
                    )

            result_rid = self.j.append(
                workflow_id, branch.branch_id, seq, "RESULT",
                tool_name=tool, effect_type=et, args=step["args"], key=key,
                epoch=self.epoch, result=res,
            )

            if tool == "classify":
                classify_record_id = result_rid

            if res.status == "failed":
                if et.reversibility == "irreversible":
                    raise _Diverge(res.error or "irreversible step failed", classify_record_id)
                raise Escalation(f"{tool} failed: {res.error}", {"tool": tool})

        return {"outcome": "completed", "severity": severity, "workflow_id": workflow_id,
                "branch_id": branch.branch_id}


class _Diverge(Exception):
    def __init__(self, reason: str, fork_point_record_id: int | None):
        super().__init__(reason)
        self.reason = reason
        self.fork_point_record_id = fork_point_record_id
