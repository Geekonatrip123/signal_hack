"""Invariant tests for the parts Part 0.2 says to review by hand."""

from __future__ import annotations

import pytest

from palimpsest.checker import check_eeo, escalation_reasons, evidence_table, outcome_counts
from palimpsest.engine import Orchestrator, recover
from palimpsest.journal import Journal, LeaseUnavailable
from palimpsest.scenarios import (
    ALERT,
    make_ctx,
    scenario_compfail,
    scenario_compretry,
    scenario_crash,
    scenario_poison,
    scenario_redelivery,
    scenario_residue,
    scenario_zombie,
)
from palimpsest.tools import EFFECT_TYPES
from palimpsest.types import ToolResult, effect_key, workflow_id_for
from palimpsest.world import FaultConfig, GroundTruthLedger, InProcessWorld


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "t.db")


def _ctx(db, **_):
    return make_ctx(db)


# ------------------------------------------------------------------ GATE 2


def test_poison_palimpsest_pages_the_right_rota_once(db):
    ctx = _ctx(db)
    res = scenario_poison(ctx, "palimpsest", {})
    board = res["state"]["scoreboard"]

    assert res["result"]["outcome"] == "completed"
    assert (board["tickets"], board["posts"], board["pages"]) == (1, 1, 1)
    assert [p["rota"] for p in res["state"]["phones"]] == ["rota-Y"]
    # Gross shows the work that was done and then undone; net shows the world.
    assert board["gross"]["create_ticket"] == 2
    assert res["verdict"]["pass"], res["verdict"]["unexplained"]
    ctx.close()


def test_poison_pinned_livelocks_and_loses_the_signal(db):
    ctx = _ctx(db)
    res = scenario_poison(ctx, "pinned", {})
    board = res["state"]["scoreboard"]

    assert res["result"]["outcome"] == "livelocked"
    assert (board["tickets"], board["posts"], board["pages"]) == (1, 1, 0)
    assert not res["verdict"]["pass"]
    assert any(v["clause"] == "no_loss" for v in res["verdict"]["unexplained"])
    ctx.close()


def test_poison_naive_duplicates_and_never_cleans_up(db):
    ctx = _ctx(db)
    res = scenario_poison(ctx, "naive", {})
    board = res["state"]["scoreboard"]

    assert (board["tickets"], board["posts"]) == (2, 2)
    assert not res["verdict"]["pass"]
    assert any(v["clause"] == "clean_abandonment" for v in res["verdict"]["unexplained"])
    ctx.close()


# ------------------------------------------------------- barrier scope (2.2)


def test_barrier_scope_catches_an_aunt_not_just_a_sibling(db):
    """v1's rule said 'abandoned sibling'."""
    j = Journal(db)
    wf = "wf-scope"

    b0 = j.create_branch(wf)
    b1 = j.create_branch(wf, b0.branch_id, None, 1)
    b2 = j.create_branch(wf, b1.branch_id, None, 2)  # b1 is b2's parent, b0 its aunt

    for branch in (b0, b1):
        j.append(
            wf, branch.branch_id, 4, "RESULT",
            tool_name="create_ticket", effect_type=EFFECT_TYPES["create_ticket"],
            args={}, key=effect_key(wf, branch.branch_id, 4),
            result=ToolResult("ok"),
        )
        j.set_branch_status(branch.branch_id, "abandoned")

    pending = j.uncompensated(wf)
    branches = {p.branch_id for p in pending}
    assert b0.branch_id in branches, "the aunt's uncompensated effect must block the gate"
    assert b1.branch_id in branches
    assert b2.branch_id not in branches
    j.close()


# -------------------------------------------------- compensation order (2.6)


def test_compensation_runs_in_reverse_execution_order(db):
    ctx = _ctx(db)
    res = scenario_poison(ctx, "palimpsest", {})
    recs = ctx.journal.records(res["workflow_id"])

    drained = [
        r.tool_name
        for r in recs
        if r.kind == "COMP_RESULT" and r.result and r.result.status == "ok"
    ]
    # Delete the channel post before closing the ticket it references, or you leave a live.
    assert drained == ["post_to_channel", "create_ticket"]
    assert res["verdict"]["pass"]
    ctx.close()


def test_gate_records_carry_the_reason_the_ui_renders(db):
    ctx = _ctx(db)
    res = scenario_poison(ctx, "palimpsest", {})
    recs = ctx.journal.records(res["workflow_id"])

    blocked = [r for r in recs if r.kind == "BARRIER_BLOCKED"]
    released = [r for r in recs if r.kind == "BARRIER_RELEASED"]
    assert blocked and released
    assert blocked[0].detail["count"] == 2
    assert sorted(blocked[0].detail["effects"]) == ["create_ticket", "post_to_channel"]
    assert res["state"]["barrier"]["state"] == "released"
    ctx.close()


# ----------------------------------------------------- bounded divergence (2.5)


def test_fork_bound_escalates_instead_of_livelocking(db):
    ctx = _ctx(db)
    ctx.set_faults(empty_rotas={"rota-X", "rota-Y", "rota-Z"})

    out = recover(
        ctx.journal, ctx.world, ALERT, "P2", mode="palimpsest",
        owner="orch-test", max_fork_depth=1, max_attempts=3,
    )
    assert out["outcome"] == "escalated"
    assert "fork bound" in out["reason"]

    verdict = check_eeo(ctx.journal, ctx.ledger, out["workflow_id"], out["outcome"])
    assert verdict["clauses"]["no_loss"], "escalation is a terminal outcome, not a loss"
    ctx.close()


# ------------------------------------------------ bounded barrier (2.3), 7.6


def test_compensation_failure_escalates_rather_than_hanging(db):
    ctx = _ctx(db)
    res = scenario_compfail(ctx, "palimpsest", {"barrier_deadline_s": 0.5})

    assert res["result"]["outcome"] == "escalated"
    assert res["state"]["barrier"]["state"] == "escalated"
    assert res["state"]["scoreboard"]["pages"] == 0

    # The shortfall is real and it is surfaced, which is what makes it explained rather.
    assert res["verdict"]["pass"], res["verdict"]["unexplained"]
    assert any(
        v["clause"] == "clean_abandonment" and v["explained"]
        for v in res["verdict"]["violations"]
    )
    escalations = res["state"]["escalations"]
    assert escalations and escalations[0]["detail"]["uncompensated"]
    ctx.close()


# ------------------------------------------------------------- residue (2.4)


def test_residue_does_not_block_the_barrier_and_is_superseded(db):
    ctx = _ctx(db)
    res = scenario_residue(ctx, "palimpsest", {})
    board = res["state"]["scoreboard"]

    # The abandoned branch carries a page that cannot be undone.
    assert res["state"]["residue"], "an irreversible effect was stranded, record it"
    statuses = {b["status"] for b in res["state"]["branches"]}
    assert "abandoned_with_residue" in statuses

    # The barrier still held for the two compensatable effects, and still lifted.
    assert (board["tickets"], board["posts"]) == (1, 1)
    assert res["state"]["barrier"]["state"] == "released"

    # Two phones ring, and the second one explains the first.
    assert board["pages"] == 2
    assert res["state"]["phones"][1]["supersedes"]["rota"] == "rota-X"
    assert res["verdict"]["pass"], res["verdict"]["unexplained"]
    ctx.close()


def test_naive_double_buzz_has_no_supersede_and_fails_clause_1(db):
    ctx = _ctx(db)
    res = scenario_residue(ctx, "naive", {})
    board = res["state"]["scoreboard"]

    assert (board["tickets"], board["posts"], board["pages"]) == (2, 2, 2)
    assert res["state"]["phones"][1]["supersedes"] is None
    assert any(v["clause"] == "no_duplication" for v in res["verdict"]["unexplained"])
    ctx.close()


# ------------------------------------------------------ crash recovery (2.7 c1)


@pytest.mark.parametrize(
    "phase", ["before_intent", "after_intent", "after_effect", "after_result"]
)
def test_crash_at_a_boundary_never_duplicates_an_effect(tmp_path, phase):
    ctx = make_ctx(str(tmp_path / f"c-{phase}.db"))
    res = scenario_crash(ctx, "palimpsest", {"crash_seq": 4, "crash_phase": phase})

    keys = [e["key"] for e in ctx.ledger.effects(workflow_id=res["workflow_id"])]
    assert len(keys) == len(set(keys)), "an idempotency key committed twice"
    assert not any(
        v["clause"] == "no_duplication" for v in res["verdict"]["unexplained"]
    )
    ctx.close()


# ----------------------------------------------------- at-least-once (3.2, 1.4)


def test_redelivered_alert_lands_on_one_workflow(db):
    ctx = _ctx(db)
    res = scenario_redelivery(ctx, "palimpsest", {})

    assert res["deliveries"] == 2
    assert res["workflows"] == 1
    assert res["state"]["scoreboard"]["tickets"] == 1
    assert res["verdict"]["pass"], res["verdict"]["unexplained"]
    ctx.close()


# ------------------------------------------------- bounded ambiguity (2.7, 3.3)


def test_zombie_page_is_caught_on_the_key_not_paged_twice(db):
    ctx = _ctx(db)
    res = scenario_zombie(ctx, "palimpsest", {"flush": True})

    pages = ctx.ledger.effects("page_oncall", workflow_id=res["workflow_id"])
    assert len(pages) == 1, "the late delivery must not become a second page"
    assert res["state"]["late_deliveries"], "the suppression must be journaled"
    assert res["state"]["escalations"], "the unknown must have been surfaced first"
    assert res["verdict"]["pass"], res["verdict"]["unexplained"]
    ctx.close()


def test_unknown_page_that_never_lands_stays_surfaced(db):
    """No probe, no inverse: the honest outcome is an escalation, not a guess."""
    ctx = _ctx(db)
    ctx.set_faults(timeout_tools={"page_oncall"})

    out = recover(
        ctx.journal, ctx.world, ALERT, "P1", mode="palimpsest",
        owner="orch-test", max_attempts=2,
    )
    assert out["outcome"] == "escalated"
    assert "bounded ambiguity" in out["reason"]

    verdict = check_eeo(ctx.journal, ctx.ledger, out["workflow_id"], out["outcome"])
    assert verdict["ba_surfaced"] == 1
    assert verdict["clauses"]["bounded_ambiguity"]
    assert verdict["clauses"]["no_loss"]
    ctx.close()


# ------------------------------------------------------------- fencing (3.4)


def test_stale_epoch_is_refused_by_the_effect_layer():
    """A deposed leader is rejected, not trusted to stand down on its own."""
    ledger = GroundTruthLedger()
    world = InProcessWorld(ledger, FaultConfig())
    wf = workflow_id_for("a-1001")

    fresh = world.execute(
        "create_ticket", {"service": "payments-api"}, effect_key(wf, "br-b", 4), 2, 1.0
    )
    assert fresh.status == "ok"

    stale = world.execute(
        "page_oncall", {"rota": "rota-X"}, effect_key(wf, "br-a", 6), 1, 1.0
    )
    assert stale.status == "failed"
    assert "stale epoch" in stale.error

    # Fencing is per workflow, not per key: a different workflow is unaffected.
    other = workflow_id_for("a-2002")
    ok = world.execute("page_oncall", {"rota": "rota-Y"}, effect_key(other, "br-c", 6), 1, 1.0)
    assert ok.status == "ok"


def test_concurrent_lease_acquisition_never_issues_a_duplicate_epoch(db):
    """Two orchestrators racing for one lease must not both win with the same epoch."""
    import threading

    j = Journal(db)
    j.acquire_lease("wf-race", "orch-seed", ttl=0.0)  # expired immediately

    granted: list[int] = []
    refused: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def contend(i: int) -> None:
        start.wait()
        try:
            epoch = j.acquire_lease("wf-race", f"orch-{i}", ttl=0.0)
            with lock:
                granted.append(epoch)
        except LeaseUnavailable as e:
            with lock:
                refused.append(str(e))

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert granted, "somebody must be able to take an expired lease"
    assert len(granted) == len(set(granted)), (
        f"duplicate epoch issued to concurrent acquirers: {sorted(granted)}"
    )
    # Epochs are the fencing token, so they must also be monotonic.
    assert sorted(granted) == list(range(min(granted), min(granted) + len(granted)))
    j.close()


def test_key_is_stable_across_epochs():
    wf = workflow_id_for("a-1001")
    assert effect_key(wf, "br-1", 6) == effect_key(wf, "br-1", 6)
    assert effect_key(wf, "br-1", 6) != effect_key(wf, "br-2", 6)
    assert effect_key(wf, "br-1", 6).startswith(wf + ":")


# --------------------------------------------------------------- replay (1.2)


def test_escalation_rate_is_derived_not_asserted(tmp_path):
    """7.6 quotes the escalation rate as a measured number."""
    clean = make_ctx(str(tmp_path / "clean.db"))
    clean_res = scenario_poison(clean, "palimpsest", {})

    broken = make_ctx(str(tmp_path / "broken.db"))
    broken_res = scenario_compfail(broken, "palimpsest", {"barrier_deadline_s": 0.5})

    verdicts = [clean_res["verdict"], broken_res["verdict"]]
    counts = outcome_counts(verdicts)
    assert counts == {"completed": 1, "escalated": 1}

    reasons = escalation_reasons(verdicts)
    assert sum(reasons.values()) == 1
    assert any("compensation exhausted" in r for r in reasons)

    table = evidence_table(verdicts, sweep=True)
    assert "escalation rate:           50.0%" in table
    assert "neither:                 0 / 2" in table

    assert outcome_counts([clean_res["verdict"]]) == {"completed": 1}
    assert "escalation rate:           0.0%" in evidence_table(
        [clean_res["verdict"]], sweep=True
    )

    # Two runs is not a sample.
    scenario_table = evidence_table(verdicts, sweep=False)
    assert "EEO VERDICT" in scenario_table
    assert "50.0%" not in scenario_table
    assert "not a rate at this sample size" in scenario_table

    clean.close()
    broken.close()


def test_compensation_retry_succeeds_when_the_outage_is_transient(db):
    """The other half of the bounded barrier."""
    ctx = _ctx(db)
    res = scenario_compretry(ctx, "palimpsest", {"barrier_deadline_s": 2.0})

    assert res["result"]["outcome"] == "completed"
    assert res["state"]["barrier"]["state"] == "released"
    assert res["state"]["scoreboard"]["pages"] == 1

    recs = ctx.journal.records(res["workflow_id"])
    comps = [r for r in recs if r.kind == "COMP_RESULT"]
    failed = [r for r in comps if r.result and r.result.status == "failed"]
    ok = [r for r in comps if r.result and r.result.status == "ok"]

    assert failed, "the first attempt must actually have failed"
    assert {r.tool_name for r in ok} == {"create_ticket", "post_to_channel"}
    # The ticket compensation succeeded on a later attempt than the one that failed.
    ticket_ok = [r for r in ok if r.tool_name == "create_ticket"][0]
    assert (ticket_ok.detail or {}).get("attempt", 1) > 1
    assert res["verdict"]["pass"], res["verdict"]["unexplained"]
    ctx.close()


def test_divergence_reads_alternatives_from_the_decision(db):
    """2.5 selects from *the decision's* alternatives list."""
    import palimpsest.tools as tools_mod

    ctx = _ctx(db)
    ctx.set_faults(empty_rotas={"rota-X"})

    original = tools_mod.trace_for

    def ranked_p3_first(alert, severity, supersede=None):
        steps = original(alert, severity, supersede=supersede)
        steps[2]["alternatives"] = ["P3", "P1", "P2"]
        return steps

    monkey = ranked_p3_first
    import palimpsest.engine as engine_mod

    engine_mod.trace_for = monkey
    try:
        out = recover(
            ctx.journal, ctx.world, ALERT, "P2", mode="palimpsest",
            owner="orch-test", max_attempts=3,
        )
    finally:
        engine_mod.trace_for = original

    # P2 was tried and poisoned; the trace ranks P3 above P1, so recovery must pick P3 ->.
    assert out["outcome"] == "completed"
    assert out["rota"] == "rota-Z", out
    ctx.close()


def test_replay_skips_committed_steps_but_retries_failed_ones(db):
    """A failed step is not a completed step."""
    ctx = _ctx(db)
    ctx.set_faults(empty_rotas={"rota-X"})
    orch = Orchestrator(ctx.journal, ctx.world, owner="orch-test", mode="pinned")

    first = orch.run(ALERT, "P2")
    assert first["outcome"] == "replay_failed"

    branch = ctx.journal.active_branch(first["workflow_id"])
    done = ctx.journal.completed_results(branch.branch_id)
    assert set(done) == {0, 1, 2, 3, 4, 5}, "the failed page must not count as done"
    ctx.close()
