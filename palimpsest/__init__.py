"""PALIMPSEST: divergence-safe durable execution for agent decisioning."""

from .audit import post_mortem, write_post_mortem
from .bench import benchmark, format_benchmark
from .checker import (
    check_eeo,
    escalation_reasons,
    evidence_table,
    explain,
    format_escalations,
    outcome_counts,
)
from .engine import (
    MAX_COMP_ATTEMPTS,
    MAX_FORK_DEPTH,
    BARRIER_DEADLINE_S,
    CrashPolicy,
    Escalation,
    Orchestrator,
    ProcessCrash,
    recover,
)
from .ingest import InProcessAlertSource, RedisAlertSource, alert_source, drain
from .journal import Journal, LeaseUnavailable
from .scenarios import ALERT, SCENARIOS, Ctx, make_ctx, run_scenario
from .tools import COMPENSATIONS, EFFECT_TYPES, ROTA_FOR_SEVERITY, STEP_LABELS, trace_for
from .types import (
    Alert,
    Branch,
    EffectType,
    JournalRecord,
    ToolResult,
    effect_key,
    workflow_from_key,
    workflow_id_for,
)
from .view import derive_state, render_terminal
from .world import FaultConfig, GroundTruthLedger, InProcessWorld

__version__ = "1.0.0"

__all__ = [
    "ALERT",
    "BARRIER_DEADLINE_S",
    "COMPENSATIONS",
    "EFFECT_TYPES",
    "MAX_COMP_ATTEMPTS",
    "MAX_FORK_DEPTH",
    "ROTA_FOR_SEVERITY",
    "SCENARIOS",
    "STEP_LABELS",
    "Alert",
    "Branch",
    "CrashPolicy",
    "Ctx",
    "EffectType",
    "Escalation",
    "FaultConfig",
    "GroundTruthLedger",
    "InProcessAlertSource",
    "InProcessWorld",
    "Journal",
    "JournalRecord",
    "LeaseUnavailable",
    "Orchestrator",
    "ProcessCrash",
    "RedisAlertSource",
    "ToolResult",
    "alert_source",
    "benchmark",
    "check_eeo",
    "derive_state",
    "drain",
    "effect_key",
    "escalation_reasons",
    "evidence_table",
    "explain",
    "format_benchmark",
    "format_escalations",
    "make_ctx",
    "outcome_counts",
    "post_mortem",
    "recover",
    "render_terminal",
    "run_scenario",
    "trace_for",
    "workflow_from_key",
    "workflow_id_for",
    "write_post_mortem",
]
