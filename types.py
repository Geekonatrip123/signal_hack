from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterator, Literal, Protocol

Reversibility = Literal["pure", "idempotent", "compensatable", "irreversible"]
Observability = Literal["observable", "unobservable"]
ResultStatus = Literal["ok", "failed", "unknown"]
ProbeStatus = Literal["done", "not_done", "unknown"]
BranchStatus = Literal["active", "abandoned", "compensated", "abandoned_with_residue"]

RecordKind = Literal[
    "INTENT",
    "RESULT",
    "COMP_INTENT",
    "COMP_RESULT",
    "BRANCH_FORKED",
    "BRANCH_ABANDONED",
    "BRANCH_COMPENSATED",
    "BARRIER_BLOCKED",
    "BARRIER_RELEASED",
    "ESCALATED",
    "LATE_DELIVERY_SUPPRESSED",
]


@dataclass(frozen=True)
class EffectType:
    reversibility: Reversibility
    observability: Observability
    externally_visible: bool = False

    def to_dict(self) -> dict:
        return {
            "reversibility": self.reversibility,
            "observability": self.observability,
            "externally_visible": self.externally_visible,
        }

    @staticmethod
    def from_dict(d: dict | None) -> "EffectType | None":
        if d is None:
            return None
        return EffectType(d["reversibility"], d["observability"], d["externally_visible"])


@dataclass(frozen=True)
class ToolResult:
    status: ResultStatus
    value: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "value": self.value, "error": self.error}

    @staticmethod
    def from_dict(d: dict | None) -> "ToolResult | None":
        if d is None:
            return None
        return ToolResult(d["status"], d.get("value"), d.get("error"))


@dataclass(frozen=True)
class JournalRecord:
    record_id: int
    ts: float
    workflow_id: str
    branch_id: str
    seq: int
    kind: RecordKind
    tool_name: str | None = None
    effect_type: EffectType | None = None
    args: dict | None = None
    key: str | None = None
    epoch: int | None = None
    result: ToolResult | None = None
    detail: dict | None = None


@dataclass(frozen=True)
class Branch:
    branch_id: str
    parent_branch_id: str | None
    fork_point_record_id: int | None
    depth: int
    status: BranchStatus


@dataclass(frozen=True)
class Alert:
    alert_id: str
    service: str
    source: str
    payload: dict


class World(Protocol):
    def execute(self, tool_name: str, args: dict, key: str, epoch: int, timeout_s: float) -> ToolResult: ...
    def probe(self, tool_name: str, key: str, timeout_s: float) -> ProbeStatus: ...


class AlertSource(Protocol):
    def consume(self) -> Iterator[Alert]: ...
    def ack(self, alert_id: str) -> None: ...


def effect_key(workflow_id: str, branch_id: str, seq: int) -> str:
    raw = f"{workflow_id}|{branch_id}|{seq}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def workflow_id_for(alert_id: str) -> str:
    return "wf-" + hashlib.sha256(alert_id.encode()).hexdigest()[:16]
