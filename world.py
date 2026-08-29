from __future__ import annotations

import random
import threading
import time

from .tools import COMPENSATIONS, EFFECT_TYPES
from .types import ProbeStatus, ToolResult


class GroundTruthLedger:
    def __init__(self):
        self.entries: list[dict] = []
        self.lock = threading.Lock()

    def record(self, tool_name: str, key: str, args: dict, kind: str = "effect") -> None:
        with self.lock:
            self.entries.append(
                {"ts": time.time(), "tool": tool_name, "key": key, "args": dict(args), "kind": kind}
            )

    def effects(self, tool_name: str | None = None) -> list[dict]:
        with self.lock:
            return [
                e
                for e in self.entries
                if e["kind"] == "effect" and (tool_name is None or e["tool"] == tool_name)
            ]

    def compensations(self) -> list[dict]:
        with self.lock:
            return [e for e in self.entries if e["kind"] == "compensation"]

    def counts(self, net: bool = True) -> dict:
        with self.lock:
            comped = {e["key"] for e in self.entries if e["kind"] == "compensation"}
            out = {}
            for e in self.entries:
                if e["kind"] != "effect":
                    continue
                if net and e["key"] in comped:
                    continue
                out[e["tool"]] = out.get(e["tool"], 0) + 1
            return out

    def gross_counts(self) -> dict:
        return self.counts(net=False)


class FaultConfig:
    def __init__(self):
        self.latency_s = 0.0
        self.jitter_s = 0.0
        self.fail_tools: set[str] = set()
        self.timeout_tools: set[str] = set()
        self.late_delivery_tools: set[str] = set()
        self.late_delivery_delay_s = 8.0
        self.down_services: set[str] = set()
        self.empty_rotas: set[str] = set()


class InProcessWorld:
    def __init__(self, ledger: GroundTruthLedger, faults: FaultConfig | None = None):
        self.ledger = ledger
        self.faults = faults or FaultConfig()
        self.applied: dict[str, dict] = {}
        self.epoch_seen: dict[str, int] = {}
        self.pending_late: list[threading.Timer] = []

    def _fence(self, key: str, epoch: int) -> bool:
        seen = self.epoch_seen.get(key, 0)
        if epoch < seen:
            return False
        self.epoch_seen[key] = max(seen, epoch)
        return True

    def _delay(self):
        d = self.faults.latency_s
        if self.faults.jitter_s:
            d += random.uniform(0, self.faults.jitter_s)
        if d:
            time.sleep(d)

    def execute(self, tool_name: str, args: dict, key: str, epoch: int, timeout_s: float) -> ToolResult:
        if not self._fence(key, epoch):
            return ToolResult("failed", error=f"stale epoch {epoch}")

        if key in self.applied:
            return ToolResult("ok", self.applied[key], None)

        self._delay()

        if tool_name in self.faults.down_services:
            return ToolResult("failed", error=f"{tool_name} unavailable")

        if tool_name in self.faults.fail_tools:
            return ToolResult("failed", error=f"{tool_name} rejected the request")

        if tool_name == "page_oncall" and args.get("rota") in self.faults.empty_rotas:
            return ToolResult("failed", error=f"no responder on {args['rota']}")

        if tool_name in self.faults.timeout_tools:
            if tool_name in self.faults.late_delivery_tools:
                self._schedule_late(tool_name, args, key)
            return ToolResult("unknown", error="timeout")

        value = {"tool": tool_name, "args": args, "key": key}
        self.applied[key] = value
        self.ledger.record(tool_name, key, args)
        return ToolResult("ok", value, None)

    def _schedule_late(self, tool_name: str, args: dict, key: str) -> None:
        def land():
            if key in self.applied:
                return
            self.applied[key] = {"tool": tool_name, "args": args, "key": key, "late": True}
            self.ledger.record(tool_name, key, args)

        t = threading.Timer(self.faults.late_delivery_delay_s, land)
        t.daemon = True
        t.start()
        self.pending_late.append(t)

    def compensate(self, tool_name: str, args: dict, key: str, epoch: int, timeout_s: float) -> ToolResult:
        comp = COMPENSATIONS.get(tool_name)
        if comp is None:
            return ToolResult("failed", error=f"{tool_name} has no inverse")
        if not self._fence(key, epoch):
            return ToolResult("failed", error=f"stale epoch {epoch}")
        self._delay()
        if tool_name in self.faults.down_services:
            return ToolResult("failed", error=f"{tool_name} unavailable for compensation")
        self.applied.pop(key, None)
        self.ledger.record(comp, key, args, kind="compensation")
        return ToolResult("ok", {"compensated": key}, None)

    def probe(self, tool_name: str, key: str, timeout_s: float) -> ProbeStatus:
        et = EFFECT_TYPES[tool_name]
        if et.observability == "unobservable":
            return "unknown"
        return "done" if key in self.applied else "not_done"
