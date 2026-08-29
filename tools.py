from __future__ import annotations

from .types import EffectType

PURE = EffectType("pure", "unobservable")
IDEMPOTENT = EffectType("idempotent", "observable")
COMPENSATABLE = EffectType("compensatable", "observable")
COMPENSATABLE_VISIBLE = EffectType("compensatable", "observable", True)
IRREVERSIBLE = EffectType("irreversible", "unobservable")

EFFECT_TYPES: dict[str, EffectType] = {
    "fetch_alerts": PURE,
    "fetch_service_context": PURE,
    "classify": PURE,
    "write_dedupe_marker": IDEMPOTENT,
    "create_ticket": COMPENSATABLE,
    "post_to_channel": COMPENSATABLE_VISIBLE,
    "page_oncall": IRREVERSIBLE,
    "update_status_page": COMPENSATABLE_VISIBLE,
}

COMPENSATIONS: dict[str, str] = {
    "create_ticket": "close_ticket",
    "post_to_channel": "delete_channel_post",
    "update_status_page": "revert_status_page",
}

ROTA_FOR_SEVERITY = {"P1": "rota-Y", "P2": "rota-X", "P3": "rota-Z"}


def trace_for(alert, severity: str) -> list[dict]:
    incident_id = f"inc-{alert.alert_id}"
    rota = ROTA_FOR_SEVERITY[severity]
    return [
        {"tool": "fetch_alerts", "args": {"source": alert.source}},
        {"tool": "fetch_service_context", "args": {"service": alert.service}},
        {
            "tool": "classify",
            "args": {"alert_id": alert.alert_id, "severity": severity},
            "decision": True,
            "alternatives": ["P1", "P2", "P3"],
        },
        {"tool": "write_dedupe_marker", "args": {"incident_id": incident_id}},
        {"tool": "create_ticket", "args": {"service": alert.service, "severity": severity}},
        {"tool": "post_to_channel", "args": {"incident": incident_id, "severity": severity}},
        {"tool": "page_oncall", "args": {"rota": rota, "severity": severity}},
        {"tool": "update_status_page", "args": {"incident": incident_id, "state": "investigating"}},
    ]
