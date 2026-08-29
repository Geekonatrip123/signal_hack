from __future__ import annotations

import json
import sqlite3
import time
import uuid

from .types import Branch, BranchStatus, EffectType, JournalRecord, RecordKind, ToolResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    workflow_id TEXT NOT NULL,
    branch_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    tool_name   TEXT,
    effect_type TEXT,
    args        TEXT,
    key         TEXT,
    epoch       INTEGER,
    result      TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_wf ON records(workflow_id);
CREATE INDEX IF NOT EXISTS idx_records_branch ON records(branch_id, seq);

CREATE TABLE IF NOT EXISTS branches (
    branch_id            TEXT PRIMARY KEY,
    workflow_id          TEXT NOT NULL,
    parent_branch_id     TEXT,
    fork_point_record_id INTEGER,
    depth                INTEGER NOT NULL,
    status               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_branches_wf ON branches(workflow_id);

CREATE TABLE IF NOT EXISTS leases (
    scope   TEXT PRIMARY KEY,
    owner   TEXT NOT NULL,
    epoch   INTEGER NOT NULL,
    expires REAL NOT NULL
);
"""


def _dumps(x):
    return None if x is None else json.dumps(x)


def _loads(x):
    return None if x is None else json.loads(x)


class Journal:
    def __init__(self, path: str = "palimpsest.db"):
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)

    def append(
        self,
        workflow_id: str,
        branch_id: str,
        seq: int,
        kind: RecordKind,
        tool_name: str | None = None,
        effect_type: EffectType | None = None,
        args: dict | None = None,
        key: str | None = None,
        epoch: int | None = None,
        result: ToolResult | None = None,
        detail: dict | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO records (ts, workflow_id, branch_id, seq, kind, tool_name,"
            " effect_type, args, key, epoch, result, detail)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                time.time(),
                workflow_id,
                branch_id,
                seq,
                kind,
                tool_name,
                _dumps(effect_type.to_dict() if effect_type else None),
                _dumps(args),
                key,
                epoch,
                _dumps(result.to_dict() if result else None),
                _dumps(detail),
            ),
        )
        return cur.lastrowid

    def _row_to_record(self, r) -> JournalRecord:
        return JournalRecord(
            record_id=r[0],
            ts=r[1],
            workflow_id=r[2],
            branch_id=r[3],
            seq=r[4],
            kind=r[5],
            tool_name=r[6],
            effect_type=EffectType.from_dict(_loads(r[7])),
            args=_loads(r[8]),
            key=r[9],
            epoch=r[10],
            result=ToolResult.from_dict(_loads(r[11])),
            detail=_loads(r[12]),
        )

    def records(self, workflow_id: str) -> list[JournalRecord]:
        rows = self.conn.execute(
            "SELECT * FROM records WHERE workflow_id=? ORDER BY record_id", (workflow_id,)
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def branch_records(self, branch_id: str) -> list[JournalRecord]:
        rows = self.conn.execute(
            "SELECT * FROM records WHERE branch_id=? ORDER BY record_id", (branch_id,)
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def create_branch(
        self,
        workflow_id: str,
        parent_branch_id: str | None = None,
        fork_point_record_id: int | None = None,
        depth: int = 0,
    ) -> Branch:
        bid = "br-" + uuid.uuid4().hex[:10]
        self.conn.execute(
            "INSERT INTO branches VALUES (?,?,?,?,?,?)",
            (bid, workflow_id, parent_branch_id, fork_point_record_id, depth, "active"),
        )
        return Branch(bid, parent_branch_id, fork_point_record_id, depth, "active")

    def branches(self, workflow_id: str) -> list[Branch]:
        rows = self.conn.execute(
            "SELECT branch_id, parent_branch_id, fork_point_record_id, depth, status"
            " FROM branches WHERE workflow_id=?",
            (workflow_id,),
        ).fetchall()
        return [Branch(*r) for r in rows]

    def active_branch(self, workflow_id: str) -> Branch | None:
        for b in self.branches(workflow_id):
            if b.status == "active":
                return b
        return None

    def set_branch_status(self, branch_id: str, status: BranchStatus) -> None:
        self.conn.execute("UPDATE branches SET status=? WHERE branch_id=?", (status, branch_id))

    def next_seq(self, branch_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(seq) FROM records WHERE branch_id=? AND kind IN ('INTENT','RESULT')",
            (branch_id,),
        ).fetchone()
        return 0 if row[0] is None else row[0] + 1

    def completed_results(self, branch_id: str) -> dict[int, JournalRecord]:
        out = {}
        for r in self.branch_records(branch_id):
            if r.kind == "RESULT":
                out[r.seq] = r
        return out

    def uncompensated(self, workflow_id: str) -> list[JournalRecord]:
        recs = self.records(workflow_id)
        dead = {
            b.branch_id
            for b in self.branches(workflow_id)
            if b.status in ("abandoned", "abandoned_with_residue")
        }
        compensated = {
            r.key for r in recs if r.kind == "COMP_RESULT" and r.result and r.result.status == "ok"
        }
        out = []
        for r in recs:
            if r.kind != "RESULT" or r.branch_id not in dead:
                continue
            if not r.effect_type or r.effect_type.reversibility != "compensatable":
                continue
            if not r.result or r.result.status != "ok":
                continue
            if r.key in compensated:
                continue
            out.append(r)
        return out

    def residue(self, workflow_id: str) -> list[JournalRecord]:
        dead = {
            b.branch_id
            for b in self.branches(workflow_id)
            if b.status in ("abandoned", "abandoned_with_residue")
        }
        return [
            r
            for r in self.records(workflow_id)
            if r.kind == "RESULT"
            and r.branch_id in dead
            and r.effect_type
            and r.effect_type.reversibility == "irreversible"
            and r.result
            and r.result.status in ("ok", "unknown")
        ]

    def acquire_lease(self, scope: str, owner: str, ttl: float = 5.0) -> int:
        now = time.time()
        row = self.conn.execute("SELECT owner, epoch, expires FROM leases WHERE scope=?", (scope,)).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO leases VALUES (?,?,?,?)", (scope, owner, 1, now + ttl))
            return 1
        cur_owner, epoch, expires = row
        if cur_owner == owner or now > expires:
            epoch += 1
            self.conn.execute(
                "UPDATE leases SET owner=?, epoch=?, expires=? WHERE scope=?",
                (owner, epoch, now + ttl, scope),
            )
            return epoch
        raise RuntimeError(f"lease held by {cur_owner} until {expires}")
