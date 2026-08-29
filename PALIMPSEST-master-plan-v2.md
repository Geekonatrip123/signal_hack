# PALIMPSEST

## Divergence-Safe Durable Execution for Agent Decisioning

Signal Labs AI HackDay, Hyderabad. Track: **Distributed Systems**.

This document replaces every earlier version. It is the only document.  
[https://docs.temporal.io/design-patterns/saga-pattern](https://docs.temporal.io/design-patterns/saga-pattern)

---

## REVISION NOTE: what changed from v1

Read this list once, then read the document. Every item below is a correction to a hole a judge would have found, or an interface change that is cheap now and expensive later.

**Design corrections**

1. **The barrier is now bounded.** v1's barrier could deadlock: if compensation fails, the barrier holds forever, the page never fires, and you have reproduced Option A, the exact failure you claim to fix. The barrier now escalates after a bounded number of compensation attempts. See 2.3.  
2. **Barrier scope widened.** v1 said "any abandoned *sibling* branch". Fork twice and the first abandoned branch becomes an aunt, not a sibling, and slips through. Now: any abandoned branch in the workflow. See 2.2.  
3. **Irreversible residue is now defined.** v1 had no answer for abandoning a branch that already committed an irreversible effect. New branch status `abandoned_with_residue`, new supersede annotation. See 2.4.  
4. **Divergence is now bounded.** v1 had no limit on forking. Without one you livelock, which is what you accuse pinned replay of. Max fork depth, then escalate. See 2.5.  
5. **Compensation order is now specified.** Reverse execution order, LIFO, saga-style. See 2.6.  
6. **EEO is restated as three checkable clauses.** v1's "matches some fault-free execution" is close to vacuous under a nondeterministic agent and would not have survived questioning. See 2.7.

**Interface corrections (Part 4\)**

7. `ToolResult.ok: bool` could not express "unknown". Your entire Bounded Ambiguity story had nowhere in the data model to live. Now `status: Literal["ok", "failed", "unknown"]`.  
8. `RecordKind` had no barrier events, so the dashboard could not render the gate from the journal without faking it. Added `BARRIER_BLOCKED`, `BARRIER_RELEASED`.  
9. `Branch.status` gained `abandoned_with_residue` and a `depth` field.  
10. `JournalRecord` gained `ts` and `detail`.  
11. `World` and `Tool` gained an explicit `timeout_s`. Timeouts are central to the pitch and were not in the type signature.

**Build order corrections**

12. **`World` gets two implementations from the first commit:** `InProcessWorld` and `HttpWorld`, switched by flag. This lets you land a crude end-to-end poison step before the HTTP topology exists, without the retrofit cost v1 correctly feared. See 3.6.  
13. **Zombie page delay is configurable.** 40 seconds of dead air is 13% of a five minute demo. Run 8 seconds on stage, 40 in the sweep. See 3.3.  
14. **The evidence table format is decided up front** so the EEO checker emits it directly instead of you assembling it by hand at the end. See 2.8.  
15. **The live model call is promoted from bonus to should-do.** It is an AI HackDay and a fully scripted agent invites "where is the agent". See 3.5.

**Pitch additions**

16. **Their vocabulary.** Signal Labs names four primitives: Signal Ingestion, Equilibrium Engine, Attention Routing, Institutional Memory. Two of them are yours. The poison step is an Attention Routing failure. The branch tree is Institutional Memory. Say both phrases. See 7.1.  
17. **New objection prepared:** "what if compensation itself fails, doesn't your barrier just hang". See 7.6.  
18. **The name is an argument.** A palimpsest is a manuscript where erased writing still shows through. That is the branch tree. See 7.1.

**Part 5** is now phase-and-gate based rather than clock based.

---

# PART 0: BEFORE YOU START

## 0.1 Setup checklist

- Registration confirmed for every team member.  
- Ask organisers what pre-work is allowed. This plan assumes zero pre-written code either way.  
- **Agree Part 4 as revised.** Argue about it once, then freeze. Save it to a file you can paste into any Claude Code session.  
- Assign roles A, B, C, D by name (5.1).  
- Environments: Python, SQLite, FastAPI, Docker for Redis, whatever UI stack D is fastest in.  
- Repo pushed with an empty module skeleton so import paths exist before anyone writes logic.  
- Name the deck owner. It cannot be nobody.  
- Everyone can state the problem in one sentence without notes.

## 0.2 Using Claude Code well

It is fast at specified work: dataclasses, SQLite schema, FastAPI services, the fault injector, the dashboard, the audit formatter. That is roughly half this build.

It does not help with integration, subtle recovery-correctness bugs, design judgment, or the demo. Realistic uplift is about 1.4x overall. Everyone in that room has it, so it is not an edge. What you point it at is.

- Paste Part 4 into every session, first. Interface drift between sessions is the main integration risk.  
- One session per module. Sessions holding the whole project lose track of which module they are editing.  
- Highest yield: effect services, fault injector, dashboard, audit formatter.  
- **Review by hand:** barrier logic, compensation driver, escalation policy. A subtle bug there costs you the demo.  
- When something fails, read the actual output rather than describing the failure and asking for a fix.  
- The EEO checker is your defense against generated code. See 2.8.

## 0.3 Permanently cut, non-negotiable

GPU divergence study. TLA+. DST campaign at volume. Speculative divergence. Determinism cost curve. Trace harvesting. Multiple real integrations. If someone starts one of these, stop them.

---

# PART 1: THE PROBLEM

## 1.1 What Signal Labs says they do

Signal detection, hypothesis reasoning, automated decisioning at scale. Their framing: a hundred systems generating alerts, models good enough to read all of it, and almost none of it reaching the right person in time. They call it an architecture problem, not an intelligence problem.

Their site names four primitives: Signal Ingestion, Equilibrium Engine, **Attention Routing**, **Institutional Memory**. The last two are what you are building. Use their words.

Build for the second half: automated decisioning. Detection and reasoning produce a recommendation. Decisioning means the system **acts**. It pages, files, suppresses, escalates, publishes. The moment it acts, every action is a side effect on the real world, and the question stops being "is the model right" and becomes "what happens when the process dies halfway through".

## 1.2 The gap

Durable execution solves crash recovery for ordinary programs. Temporal, Restate, DBOS, AWS Step Functions all do it via a journal: record intent before each step, result after, and on restart replay while feeding recorded results back in.

It has one hard requirement: **replay must be deterministic**. Temporal enforces this so strictly it will fail a workflow it detects as nondeterministic.

An LLM agent generates its own control flow. So you get two bad options:

**Option A, pin replay to the journaled decisions.** Determinism restored, but you are locked onto the path that just failed. If the agent misclassified at step 3, replay re-derives the same misclassification forever.

**Option B, re-run the agent fresh.** It can pick a better path, but steps 1 through 6 already happened and the fresh run does not know. Duplicate ticket, duplicate post, duplicate page.

Nobody gets both durability and adaptivity. That gap is the project.

## 1.3 Why this is their problem

Option A, in their own words: the signal never reaches the right person. Attention Routing fails silently while the incident burns.

Option B: duplicate pages and tickets. Alert fatigue, which is the noise problem they exist to remove.

Open the pitch with this:

> When an automated decisioning layer crashes mid-decision, it either loses the signal or duplicates it. Both are the exact failure you built the decisioning layer to eliminate. It is not an intelligence problem. It is an architecture problem.

The last sentence is theirs, taken verbatim from their site.

## 1.4 The demo domain: incident triage

| Step | Tool | Reversibility | Observability |
| :---- | :---- | :---- | :---- |
| 1 | `fetch_alerts(source)` | pure | n/a |
| 2 | `fetch_service_context(service)` | pure | n/a |
| 3 | `classify(alert)` (the model decision) | pure | n/a |
| 4 | `write_dedupe_marker(incident_id)` | idempotent | observable |
| 5 | `create_ticket(service, severity)` | compensatable | observable |
| 6 | `post_to_channel(incident)` | compensatable, externally visible | observable |
| 7 | `page_oncall(rota, severity)` | **irreversible** | **unobservable** |
| 8 | `update_status_page(...)` | compensatable, externally visible | observable |

Step 7 is the hard corner on purpose. You cannot un-ring a phone and cannot reliably query whether the page was delivered. Everything interesting happens between steps 6 and 7\.

**Why is dedupe at step 4 and not step 1?** Have this answer ready, it gets asked. Step 4 dedupes *incidents*, not alerts: it asks whether an open incident already exists for this service and severity, which requires the classification from step 3\. Alert-level redelivery from Redis is handled separately and earlier, by deterministic `workflow_id` derivation plus journal lookup. The marker protects against cross-process races on the same incident. The journal protects against redelivery of the same alert. Two different mechanisms for two different problems. Do not conflate them on stage.

**The poison step.** At step 3 the agent classifies P2 on `payments-api`, routing to rota X. Step 7 fails because rota X has nobody on call. The correct classification is P1, routing to rota Y.

- **Pinned replay:** journals P2, replays P2, pages rota X, fails, forever. Nobody reached.  
- **Naive re-run:** re-creates the ticket, re-posts to the channel, may still misclassify.  
- **PALIMPSEST:** diverges at step 3, compensates the abandoned branch, barrier holds before the irreversible page, re-classifies P1, pages rota Y.

---

# PART 2: THE SYSTEM

## 2.1 Three ideas

**The journal is a tree, not a log.** Recovery that wants a different path forks a branch and marks the old one abandoned. The journal records what we tried, not just what we did. This is Institutional Memory in their vocabulary: the system remembers the path it rejected and why.

**Tool calls are typed by effect.** `pure` / `idempotent` / `compensatable` / `irreversible`, crossed with `observable` / `unobservable`, plus an `externally_visible` flag. This makes recovery decidable instead of heuristic.

**The irreversible barrier.** Cleanup before commitment. Stated precisely in 2.2.

## 2.2 The barrier rule (CORRECTED)

> A branch may not execute an irreversible effect while **any abandoned branch in the same workflow** holds uncompensated compensatable effects.

**What changed and why it matters.** v1 said "abandoned *sibling* branch". Consider: fork at step 3, abandon branch B1. Continue, fork again at step 5, abandon branch B2. The active branch B3 is a sibling of B2 but B1 is now an aunt. Under the v1 rule, B1's uncompensated ticket and channel post would not block the page. That is a real correctness hole in a scenario a judge can construct in one line, and it is free to fix: the check is a query over all branches in the workflow with `status IN ('abandoned', 'abandoned_with_residue')`, not a sibling lookup.

One sentence, cheap to enforce, and still the technical heart of the pitch.

## 2.3 The bounded barrier (NEW, IMPORTANT)

This is the single most important addition in this revision. **Build it.**

A naive barrier deadlocks. Compensation calls the ticket service. The ticket service is down. Compensation fails. The barrier holds. The page never fires. You are now Option A: the signal never reached the right person while the incident burned. Your own failure mode, reproduced by your own fix. A judge will construct this and it undercuts the whole pitch if you have no answer.

**Policy.** The barrier is bounded, not absolute:

- Compensation is attempted up to `MAX_COMP_ATTEMPTS` times with backoff, or until `BARRIER_DEADLINE` elapses, whichever comes first.  
- On exhaustion, the workflow does **not** silently proceed and does **not** hang. It writes an `ESCALATED` record carrying the full branch tree, the uncompensated effect list, and the reason, and surfaces it to a human.  
- Escalation is a terminal outcome that satisfies the liveness clause of EEO (2.7). A workflow that escalates has not lost the signal. It has routed it to a human instead of a rota, which is a degraded but honest outcome.  
- The `ESCALATED` record already exists in `RecordKind`. You are adding a policy and a timer, not a new subsystem.

**Why this is a feature and not a caveat.** It is the third position between the two bad options. Pinned replay hangs silently. Naive re-run acts blindly. The bounded barrier acts when it can prove cleanup, escalates when it cannot, and never does either without a record. That is the sentence to use when asked.

**Demo value.** This is a sixth explosive moment if you build it: kill the ticket service, watch the barrier try to compensate, fail, and escalate to a human within the deadline rather than hanging. Roughly twenty minutes of work on top of the barrier you are already building.

## 2.4 Irreversible residue on abandoned branches (NEW)

v1 had no answer for this and the poison step conveniently avoids it: step 7 *fails* before committing. But a judge will ask the harder version. What if the page to rota X **succeeded**, and only afterwards you discover the misclassification? You now want to abandon a branch that carries an uncompensatable effect. You cannot undo it, so the barrier's precondition can never be satisfied, and a naive implementation blocks forever.

**Policy.**

- New branch status: `abandoned_with_residue`. The branch is abandoned; its compensatable effects are compensated normally; its irreversible effects are recorded as permanent residue.  
- **The barrier does not block on irreversible residue.** Blocking on an unsatisfiable condition is a deadlock by construction. It blocks only on *uncompensated compensatable* effects, which is what the rule in 2.2 already says. Make sure the implementation matches the wording.  
- Residue is surfaced. The new branch's page carries a supersede annotation: "supersedes page to rota X for incident I, issued under classification P2, superseded by P1". A second phone ringing is acceptable when the second ring explains the first. A second phone ringing with no context is the naive re-run failure.  
- The audit export shows residue explicitly. This is the honest position: compensation restores state, not history, and the branch tree is where the history lives.

Ten minutes to specify. Having the answer ready is worth more than the code.

## 2.5 Bounded divergence (NEW)

v1 said "divergent recovery picks from alternatives" with no termination condition. Without a bound, a workflow can fork, fail, fork, fail indefinitely. That is **livelock**, which is precisely the thing you accuse pinned replay of.

**Policy.**

- `Branch.depth` is tracked. Forking increments it.  
- Divergence policy on recovery: select the highest-ranked unattempted alternative from the decision's `alternatives` list, excluding any already tried on an abandoned ancestor.  
- At `MAX_FORK_DEPTH` (set it to 3), or when alternatives are exhausted, stop forking and `ESCALATED`.  
- The bound is journaled so the dashboard and audit export can show it.

Say the number out loud in the pitch. "We bound divergence at depth three and then escalate, because an unbounded search over irreversible actions is just the livelock we are criticising."

## 2.6 Compensation ordering (NEW)

Ref: [https://docs.temporal.io/design-patterns/saga-pattern](https://docs.temporal.io/design-patterns/saga-pattern)

Compensations fire in **reverse execution order** on the abandoned branch. LIFO, exactly as in sagas.

This is not cosmetic. Delete the channel post before closing the ticket it references, or you leave a live post pointing at a closed ticket. Anyone in the room who knows Garcia-Molina and Salem will ask, and "reverse order, same as sagas, we are their descendant" is a one-word-perfect answer.

The dashboard drain animation must run backwards for the same reason. If the visual drains forward it is teaching the wrong thing.

## 2.7 Correctness, restated (CORRECTED)

**v1's definition was weak.** "Committed irreversible effects match some fault-free execution" is close to vacuous when the agent is nondeterministic: if the agent could have chosen almost anything, almost any outcome matches *some* fault-free run. v1 correctly anticipated that a careful reader would question this, but the prepared answer did not have enough in it. Replace the definition with three clauses your checker can mechanically verify.

**Effect-Exactly-Once (EEO).** After crash, recovery, and quiescence:

1. **No duplication.** No irreversible effect commits more than once per logical decision point, identified by its idempotency key.  
2. **No loss.** Every workflow terminates in either a committed action or a surfaced escalation. Never silently, never still running.  
3. **Clean abandonment.** Every compensatable effect on an abandoned branch has a matching compensation, in reverse order, and no effect on an active branch is compensated.

All three are checkable against the ground-truth ledger. None of them appeals to a hypothetical reference execution.

"The committed effects correspond to some execution the agent could have produced fault-free" then becomes a *corollary* you mention in one sentence, rather than a definition you have to defend. That reordering is the whole fix.

**Bounded Ambiguity (BA).** At most one `irreversible` \+ `unobservable` effect may sit in unknown state per workflow, and it is surfaced, never silently resolved.

BA exists because exactly-once is impossible in that corner. Two generals. You confine the impossibility to one place and report it. Note that clause 2 above is what makes BA safe: an unknown page still terminates in a surfaced escalation, so the signal is never lost even when the effect status is not knowable.

## 2.8 The EEO checker and the evidence table

The ground-truth ledger plus the EEO checker is an oracle: it knows what actually happened to the world, independently of what the system journaled or believed. It catches plausible-looking generated code that is quietly wrong.

**Build it early.** Do not treat a green test suite Claude Code also wrote as evidence of anything. The checker comparing against ground truth is the thing you trust.

**Decide the output format now** so it emits your evidence slide directly instead of you assembling one by hand under time pressure. Target:

CRASH SWEEP RESULTS

  crash points swept:        N   (every step boundary x every branch state)

  fault modes:               M   (crash, timeout, partition, late-delivery)

  total runs:                N\*M

  EEO clause 1 (no dup):     pass X / X

  EEO clause 2 (no loss):    pass X / X

  EEO clause 3 (clean abd):  pass X / X

  BA surfaced (unknown):     Y    \<- expected, these are the hard corner

  unexplained violations:    0    \<- this is the number that matters

The last line is the one you read out loud. If it is not zero, you have found a real bug and you have found it in time.

---

# PART 3: ARCHITECTURE

## 3.1 Topology

        ┌────────────────┐

        │ ALERT PRODUCER │  synthetic alerts at configurable rate

        └───────┬────────┘

                ▼

        ┌────────────────────────┐

        │ REDIS STREAM alerts:\*  │  consumer group, at-least-once

        └───────┬────────────────┘

                │

      ┌─────────┴──────────────┐

      ▼                        ▼

┌──────────────┐        ┌──────────────┐

│ ORCHESTRATOR │◄lease─►│ ORCHESTRATOR │

│  A (leader)  │ epoch N│  B (standby) │

└──────┬───────┘        └──────────────┘

       │ shared journal

       ▼

┌──────────────┐

│ SQLite (WAL) │  branch tree, leases, fencing epoch

└──────┬───────┘

       │ HTTP, with injected latency and faults

  ┌────┼─────────────┬─────────────────┐

  ▼    ▼             ▼                 ▼

┌────────────┐ ┌──────────────┐ ┌──────────────┐

│ TICKET SVC │ │ CHANNEL SVC  │ │  PAGER SVC   │

│ compensat. │ │ compensat.   │ │ IRREVERSIBLE │

│ observable │ │ observable   │ │ UNOBSERVABLE │

└─────┬──────┘ └──────┬───────┘ └──────┬───────┘

      └───────────────┴────────────────┘

                      ▼

            ┌──────────────────┐

            │  GROUND-TRUTH    │  what ACTUALLY happened

            │     LEDGER       │

            └────────┬─────────┘

                     ▼

                EEO CHECKER

Six or seven processes on one or two laptops. The three effect services are one FastAPI app parameterised by effect type, deployed three times.

**Why each piece earns its place.** Separate effect services give real network boundaries, so crashes, timeouts, and partitions are real events rather than simulated ones. Two orchestrators turn process-crash survival into node-loss survival and introduce split-brain, which has a real answer. The ground-truth ledger sits outside everything and is queried only by the checker; it is an oracle, not a component.

## 3.2 Input stream

Redis Streams with a consumer group. One `docker run`, trivial Python client, and it gives you:

**At-least-once delivery.** The stream can hand you the same alert twice. Handled by deterministic `workflow_id` derivation plus journal lookup, not by the step-4 marker (see 1.4).

**Consumer groups.** Multiple orchestrators consume from one group. Pending-entry inspection shows alerts claimed but never acknowledged, which is your recovery signal after a node dies.

**Visible lag.** Your backpressure and scale metric, and it plots well.

Write the interface first with an in-process asyncio queue fallback. Do not discover a Docker problem with no plan B.

## 3.3 Latency, and why it is nearly free

Middleware on each effect service: configurable delay, jitter, fault mode. Twenty minutes.

**A timeout is the commit window.** When `page_oncall` times out you do not know whether the page went. That is exactly the unknown state BA handles, arriving through the front door instead of through injected crashes. This is why `ToolResult.status` must have an `unknown` variant (Part 4).

**Slow and crashed are indistinguishable.** Demonstrate the classic result rather than citing it.

**Late-arriving effects.** Build this deliberately. The pager times out, the orchestrator probes, gets `unknown`, escalates to a human. Later the page lands anyway. Show that you detect the zombie via the idempotency key rather than double-paging.

**CORRECTION: make the late-delivery window configurable.** v1 specified forty seconds. Forty seconds of dead air is thirteen percent of a five minute demo, spent watching nothing happen. Expose `LATE_DELIVERY_DELAY` in the same middleware you are already writing. Run **8 seconds on stage** and 40 in the sweep, and say "configured to forty seconds in the crash sweep, compressed here so we finish on time". Nobody minds and you buy back half a minute for the architecture slide, which carries more weight.

## 3.4 Split-brain and fencing

Two orchestrators means both might believe they lead after a partition. If both execute effects you page twice and your correctness claim collapses in exactly the scenario a judge will construct.

**A lease with a monotonically increasing epoch.** Leadership is a row in SQLite with owner and expiry. Acquiring or renewing increments the epoch.

**Keep the idempotency key stable across epochs** so retries collapse, but **carry the epoch as a fencing token** on every effect request.

**Effect services reject stale epochs.** Each service records the highest epoch seen per workflow and rejects anything lower. A deposed leader that wakes from a pause gets refused by the pager service, not trusted to stand down on its own.

Fifteen minutes on top of the lease. Say "fencing token" out loud when asked.

## 3.5 Agent hosting and compute

Compute needed: essentially none. A state machine, HTTP services, SQLite, Redis. Under a gigabyte of RAM across every process. Any laptop, no GPU on the critical path.

**Agent:** scripted decision traces. Each decision carries `chosen_tool`, `chosen_args`, and `alternatives`. Divergent recovery picks from `alternatives` under the bounded policy in 2.5.

**CORRECTION: promote the live model call.** v1 filed this as an optional last flourish. It is an AI HackDay, and a fully scripted agent invites "so where is the agent". Timebox to twenty minutes, step 3 only, in the opening thirty seconds of the demo, with the scripted trace cached and **tested** as fallback. The framing line does most of the work:

> The model chooses. We make the choosing survivable.

That single sentence positions the whole project correctly: you are not building a better classifier, you are building the layer that lets a classifier's output be acted on safely. Do not set up vLLM at the venue and do not depend on a VPN to a lab server for a component that is not load-bearing.

## 3.6 The World protocol, and why you can build the poison step early (NEW)

v1 said the HTTP topology must be in the core build because retrofitting boundaries later "changes every call site". That reasoning is correct but the conclusion is avoidable, and your own Part 4 already contains the fix: `World` is a Protocol.

**Write two implementations from the first commit:**

- `InProcessWorld`: direct calls to in-memory effect stubs plus the ground-truth ledger. No network, no Docker, no FastAPI.  
- `HttpWorld`: real HTTP to the three services, with fencing epoch, timeout, and fault middleware.

Selected by a single flag. Everything above `World` (journal, branch tree, barrier, compensation driver, escalation) is written once and never touched again when you switch.

**Why this matters.** It lets you land a crude but complete end-to-end poison step well before the HTTP services are finished, which massively de-risks the hard gate. If integration goes badly you still have a working three-pane demo running in-process, and the three-pane demo is what you are actually judged on. Switching to `HttpWorld` becomes a flag flip and a bug hunt rather than a rewrite.

This is the highest-leverage structural change in this revision after the bounded barrier.

---

# PART 4: FROZEN INTERFACES

Agree once, freeze, paste into every Claude Code session.

Reversibility \= Literal\["pure", "idempotent", "compensatable", "irreversible"\]

Observability \= Literal\["observable", "unobservable"\]

ResultStatus  \= Literal\["ok", "failed", "unknown"\]

ProbeStatus   \= Literal\["done", "not\_done", "unknown"\]

@dataclass(frozen=True)

class EffectType:

    reversibility: Reversibility

    observability: Observability

    externally\_visible: bool

@dataclass(frozen=True)

class ToolResult:

    status: ResultStatus

    value: Any

    error: str | None

class Tool(Protocol):

    name: str

    effect\_type: EffectType

    def execute(self, args: dict, key: str, epoch: int, timeout\_s: float) \-\> ToolResult: ...

    def compensate(self, args: dict, result: ToolResult, key: str, epoch: int, timeout\_s: float) \-\> ToolResult: ...

    def probe(self, key: str, timeout\_s: float) \-\> ProbeStatus: ...

RecordKind \= Literal\[

    "INTENT", "RESULT", "COMP\_INTENT", "COMP\_RESULT",

    "BRANCH\_FORKED", "BRANCH\_ABANDONED", "BRANCH\_COMPENSATED",

    "BARRIER\_BLOCKED", "BARRIER\_RELEASED",

    "ESCALATED", "LATE\_DELIVERY\_SUPPRESSED",

\]

@dataclass(frozen=True)

class JournalRecord:

    record\_id: int

    ts: float

    workflow\_id: str

    branch\_id: str

    seq: int

    kind: RecordKind

    tool\_name: str | None

    effect\_type: EffectType | None

    args: dict | None

    key: str | None

    epoch: int | None

    result: ToolResult | None

    detail: dict | None

@dataclass(frozen=True)

class Branch:

    branch\_id: str

    parent\_branch\_id: str | None

    fork\_point\_record\_id: int | None

    depth: int

    status: Literal\["active", "abandoned", "compensated", "abandoned\_with\_residue"\]

class World(Protocol):

    def execute(self, tool\_name: str, args: dict, key: str, epoch: int, timeout\_s: float) \-\> ToolResult: ...

    def probe(self, tool\_name: str, key: str, timeout\_s: float) \-\> ProbeStatus: ...

class AlertSource(Protocol):

    def consume(self) \-\> Iterator\[Alert\]: ...

    def ack(self, alert\_id: str) \-\> None: ...

`key = hash(workflow_id, branch_id, seq)`. Stable across epochs. Epoch travels separately as the fencing token.

Storage: SQLite in WAL mode.

## 4.1 What changed from v1, and why each change is load-bearing

1. **`ToolResult.ok: bool` became `ToolResult.status: ResultStatus`.** This is the most important line in the document. A timeout is not `ok=False`, which means definitely failed. It is genuinely unknown. With a boolean, Bounded Ambiguity has nowhere in the data model to live, and every timeout gets silently misclassified as a failure, which is exactly the bug that produces a double page. Everything downstream (probe policy, escalation, BA reporting, the checker) depends on this distinction existing in the type.  
     
2. **`BARRIER_BLOCKED` and `BARRIER_RELEASED` added to `RecordKind`.** Your governing dashboard rule is that the UI reads the journal and never drives execution. But in v1 the barrier holding and lifting was journaled nowhere, so the dashboard would have had to infer or fabricate the gate state. Since the gate is your hero visual, it must be journal-derived or it is a cartoon. The `detail` field carries the reason string and uncompensated count, which is what the gate's label renders.  
     
3. **`abandoned_with_residue` added to `Branch.status`.** See 2.4. Without it there is no representable state for a branch that committed an irreversible effect before being abandoned.  
     
4. **`Branch.depth` added.** Enforces the fork bound in 2.5.  
     
5. **`JournalRecord.ts` added.** Needed by the incident timer, the late-delivery detector, and the audit export. The branch tree as a post-mortem artifact is unreadable without times on the records.  
     
6. **`JournalRecord.detail: dict | None` added.** One escape hatch for structured context that does not belong in `args`: barrier reason, escalation cause, fork depth, supersede pointer. Without it you will end up stuffing these into `args` and regretting it.  
     
7. **`timeout_s` added to `Tool` and `World`.** Timeouts are central to the pitch. In v1 they were not in the type signature at all, which means every call site would have invented its own convention.

---

# PART 5: THE BUILD, IN PHASES

Phases and gates, not clock times. What matters is the order and the two hard gates.

## 5.1 Roles

- **A:** Orchestrator, journal, branch tree, barrier, bounded barrier and escalation policy, compensation driver. Critical path. Strongest systems person.  
- **B:** Effect services, latency and fault injection, ground-truth ledger, EEO checker, crash sweeps.  
- **C:** Effect types, tools, probes, triage workflow, scripted traces, Redis ingest, the poison-step scenario and the two baselines.  
- **D:** The dashboard. Full time on this. See Part 6\.

Three people: fold C's ingest into B and C's tools into A. Two people: cut to `InProcessWorld` only, cut failover, keep the demo.

## 5.2 Phase 0: lock

Re-read Part 4 together. Push the module skeleton. No design discussion afterwards. Whoever wants to relitigate an interface does it now or never.

## 5.3 Phase 1: foundations

- **A:** journal, intent-then-result, SQLite, idempotency keys, replay recovery. `InProcessWorld` wired. No branching yet.  
- **B:** the three effect services (one app, parameterised, three deployments), latency and fault middleware, ground-truth ledger. **Then the EEO checker.** In-memory effect stubs for `InProcessWorld` first, they take ten minutes and unblock A immediately.  
- **C:** effect types for all eight tools, triage workflow, scripted agent trace with alternatives, Redis producer and consumer behind `AlertSource` with the in-process fallback.  
- **D:** three panes, three counters, status line. Static data first.

## 5.4 GATE 1: clean run end to end

**Exit condition:** an alert flows from the source through the orchestrator to the effect layer, journal written, EEO checker passes on a clean run. `InProcessWorld` counts for this gate. `HttpWorld` should follow shortly after but is not the gate.

If not met, everyone helps A and B. Slipping here is survivable. Gate 2 is not.

## 5.5 Phase 2: the contribution

- **A:** branch tree, fork on divergence, branch status including residue, **barrier enforcement with the corrected scope (2.2)**, **bounded barrier and escalation (2.3)**, **fork depth bound (2.5)**, compensation driver **in reverse order (2.6)**.  
- **B:** crash-at-every-boundary sweep with EEO verdicts against the three clauses in 2.7, emitting the evidence table in 2.8.  
- **C:** compensation handlers, probes, escalation for `unknown`, the poison-step scenario and both baselines runnable from one command.  
- **D:** the barrier gate, static states only, reading `BARRIER_BLOCKED` / `BARRIER_RELEASED` from the journal.

## 5.6 GATE 2: the poison step. HARD GATE.

**Exit condition:** three panes, one crash, three outcomes. Working, repeatable, from one command.

If it does not work, everything stops and everyone fixes it. Nothing in Phase 3 starts until this passes. A submission with only the three-pane demo beats one with bonus modules and no story.

## 5.7 Phase 3: depth

D continues on the dashboard throughout. That is their job, not a tradeoff against the others.

- **A:** switch to `HttpWorld` if not already, then orchestrator failover with lease and fencing (3.4). **Cut rule:** if leader election is not working with an hour of buffer before freeze, revert cleanly. Half-working failover fails on stage.  
- **B:** late-arriving effect detection (3.3), then the concurrent scale run with Redis lag and p99, then the overhead benchmark.  
- **C:** the compensation-failure escalation demo (2.3), which is a new explosive moment and cheap. Then help D. Audit artifact export (branch tree rendered as a readable incident post-mortem) if there is time.  
- **D:** gate animation with reverse drain, phones, timer, zombie event line, escalation state, topology strip.

## 5.8 FREEZE

Bug fixes only. Anything not working at freeze is cut from the demo, not fixed.

## 5.9 Deck

One architecture diagram. The three-pane result. The evidence table. Honesty slide. Nothing else.

## 5.10 Rehearse

Three full run-throughs minimum. Record a screen capture fallback. Time it. Over five minutes means cutting content, not talking faster.

**Run the outsider test:** show the dashboard to someone from another team with no explanation and ask "which of these three is broken, and how?" If they cannot answer from the screen, fix the display before touching anything else.

---

# PART 6: THE DASHBOARD

**Governing rule:** every visual must *be* the explanation, not decoration on top of it. Beautiful and teaches nothing gets cut. Beautiful and is the explanation gets built big.

## 6.1 The hero visual: the Barrier Gate

The workflow renders as a path of step nodes, left to right. Steps 1 through 6 light up. A crash hits.

A second path visibly forks upward from step 3\. The original desaturates to grey, labelled **abandoned**. Its two committed effects (ticket, channel post) stay bright and pulse red: **uncompensated**.

The new branch reaches step 7 and a glowing gate blocks it, labelled:

> **irreversible effect blocked: 2 uncompensated effects on abandoned branch**

Compensation fires. **In reverse order** (2.6): channel post first, then ticket. Each red effect drains to grey with a reverse animation. The gate counter ticks 2, 1, 0\.

The gate lifts. The page fires. The right rota is reached.

A judge who watches the gate hold, the cleanup drain, and the gate lift has understood the irreversible barrier without you explaining it. When they ask what is novel, point at the gate.

**Build notes.**

- Static-correct before animated. Every state readable as a still frame.  
- The gate label text comes from the `BARRIER_BLOCKED` record's `detail` field. A gate that closes without a reason is decoration.  
- Slow the drain to two or three seconds so the room can watch.  
- **NEW: the escalation state.** The gate needs a third visual state beyond blocked and lifted: **escalated**, for when compensation exhausts (2.3). Amber, not red, with the label `compensation failed: escalated to human, incident not lost`. This is a still frame, no animation needed, and it is what makes the bounded barrier legible on screen.

## 6.2 The phones

Bottom right, three rendered phones.

- **Pinned replay:** dark. Timer climbing above it. `incident open 4m 32s, nobody paged`.  
- **Naive re-run:** buzzes twice, two notifications stack. `rota X paged, rota Y paged`.  
- **PALIMPSEST:** buzzes once. `rota Y paged`.

The double buzz is the most memorable half-second of the demo. Add sound if you have ten spare minutes.

## 6.3 The scoreboard

Persistent bottom strip, three columns: tickets created, channel posts, pages sent. Ending 1/1/0, 2/2/2, 1/1/1.

The gate is the explanation. The scoreboard is the proof. Keep both.

## 6.4 Topology strip

Top. Nodes for orchestrators, services, Redis. Packets animate along edges. Its moment is the live `kill -9` on the leader: node red, epoch increments, standby lights up, traffic resumes.

Only build if failover lands.

## 6.5 D's build order

1. Three panes, counters, status line, static  
2. Wire to the live journal, polling not websockets  
3. Barrier gate, static states only (blocked / lifted / escalated)  
4. Animate the gate sequence with reverse drain  
5. Phones and incident timer  
6. Zombie-page event line  
7. Topology strip, only if failover landed  
8. Sound and colour polish

**Cut line:** if not through item 4 with two hours of buffer before freeze, stop adding and polish what exists. Items 1 and 2 alone are a working demo. Items 1 through 4 are a winning one.

## 6.6 Rules that keep beauty from sinking you

- Static-correct before animated. Every view readable as a still frame.  
- No visual without a label. A grey branch confuses; a grey branch labelled **abandoned** teaches.  
- Nothing that needs narration to parse. The outsider test governs.  
- **The dashboard reads the journal and never drives execution.** If the UI dies, the system runs and you demo from the terminal. This is also why the barrier records in Part 4 are mandatory.  
- Fallback video recorded before rehearsal ends.

---

# PART 7: THE PITCH

## 7.1 The five minute demo

**0:00 to 0:40, the problem in their language.** Detection and reasoning produce a recommendation. Decisioning means acting. Every durable execution engine assumes deterministic replay, which an agent cannot provide. So a crash mid-decision either loses the signal or duplicates it, and both are the failure the system was built to eliminate.

Use their two primitive names here: this is an **Attention Routing** failure, and the branch tree is **Institutional Memory** for decisions that were tried and abandoned.

Somewhere in the first minute, spend ten seconds on the name:

> A palimpsest is a manuscript where the erased writing still shows through. That is our journal. The path we abandoned is still legible, which is what makes the cleanup provable and the audit trail real.

**0:40 to 1:30, architecture.** One diagram. Name the boxes. Name what you rejected (pinned replay, naive re-run) and why. Land the barrier rule as one sentence. Heaviest criterion, give it real airtime.

**1:30 to 3:10, the demo.** Three panes, one injected crash. Let the livelocking pane and the climbing timer sit on screen before you explain. Then the gate sequence. Read the scoreboard out loud once at the end.

**3:10 to 3:50, evidence and the hard corner.** The evidence table from 2.8, with the unexplained-violations line read out. Then the zombie page: timed out, probed unknown, escalated, landed late, caught on the key.

**3:50 to 4:30, where it falls over.** The honesty slide.

**4:30 to 5:00, what it unlocks.** This is what lets a decisioning layer take irreversible action instead of only recommending it.

## 7.2 The explosive moments

| \# | Moment | Effect |
| :---- | :---- | :---- |
| 1 | Timer past 4 minutes with `pages sent: 0` | Dread |
| 2 | The phone buzzing twice | Visceral wrongness |
| 3 | Gate holds, cleanup drains, gate lifts | Understanding |
| 4 | Zombie page caught late on the key | Engineers exhale |
| 5 | `kill -9` live, workflow continues | Theatre |
| 6 | **Compensation fails, barrier escalates instead of hanging** | **Trust** |

Moment 6 is new and it is the one that answers the sharpest objection before it is asked. Protect moment 4 and moment 6\. Both are the least replicable, and the audience judging you is exactly the audience that appreciates them.

## 7.3 When a judge asks "is this actually distributed?"

Three ways.

**First,** the workflow is a distributed transaction across independent failure domains, and one participant, the pager, cannot participate in any commit protocol: no prepare, no vote, no rollback, no way to query it afterwards. Compensation replaces rollback for participants with an inverse. Probing replaces the commit acknowledgement for participants that can be queried. Bounded Ambiguity is what remains when a participant offers neither, which is the two generals boundary rather than a gap in our engineering.

**Second,** the orchestrator is replicated with a lease and a fencing epoch, so a deposed leader gets rejected by the effect services rather than trusted to stand down on its own.

**Third,** we inject latency and partitions, so timeouts produce the same ambiguity crashes do. Here is a page that timed out, got probed as unknown, escalated, and landed anyway. We caught the zombie on the idempotency key instead of paging twice.

Then show the third one running.

**If they push on the pager:** yes, some real pagers do expose delivery status. That is why observability is a *type parameter* rather than an assumption. Configure the pager as observable and the system probes instead of escalating. The design types the capability rather than hardcoding the pessimistic case, and the unobservable configuration is the one worth demoing because it is the corner where the impossibility bites.

## 7.4 The honesty slide

They wrote "where the thing falls over" into the criteria. Most teams will skip this. Do not.

- **Exactly-once for irreversible-unobservable effects is impossible.** Two generals. We bound the ambiguity and report it.  
- **Compensation restores state, not history.** A deleted channel post was still seen. An irreversible effect on an abandoned branch is permanent residue, recorded and superseded, never silently erased.  
- **The barrier is bounded, not absolute.** If compensation cannot complete we escalate to a human rather than blocking or acting blind. That is a degraded outcome, and we chose it deliberately over the two alternatives.  
- **Agent decisions are scripted** apart from the live call at step 3\. Reproducible, but not proof of behaviour at scale.  
- **Fault coverage is not proof.** Here is what we swept and what we did not reach.  
- **\[If failover did not land\]** Single orchestrator. Process crash survivable, node loss not yet.

Then say what is next: effect type inference from OpenAPI specs, and measuring real agent divergence under GPU co-tenancy.

## 7.5 The business case

One sentence:

> Enterprises will let an AI system recommend. They will not let it act, because nobody can tell them what happens when it fails halfway through. We make failure semantics for agent actions explicit and recoverable, which is what stands between a recommendation engine and an automated decisioning system.

This names an adoption blocker, not a feature. The obstacle is not model quality. It is that no operations leader approves an agent that can page, escalate, or publish when the honest answer to "what if it crashes at step 6" is "we are not sure".

**Four claims:**

1. **The failure mode is the product thesis, inverted.** A decisioning layer exists so signals reach the right person. Crash mid-decision and it drops or duplicates the signal.  
2. **The cost is asymmetric and lands on the worst day.** Crashes cluster with load, load clusters with incidents. This fires during the outage. A duplicate-page storm mid-incident is worse than no automation.  
3. **Volume turns a rare bug into a certainty.** At enterprise decision volume, any per-decision failure probability becomes a steady stream of dropped or duplicated actions.  
4. **It converts a trust problem into an audit artifact.** The branch tree records what was tried, abandoned, cleaned up, and escalated. That is what a risk function asks for before approving automated action. If the audit export got built, show it rather than describe it.

**Do not invent statistics.** No fabricated downtime costs or market sizes. These are infrastructure engineers and one fake number discredits everything near it. The structural arguments above are stronger and free to defend. Their own site publishes figures (106 tools per enterprise, 95% of signals never reaching action). Cite theirs if you want a number, never your own.

## 7.6 Objections

**"What if compensation itself fails? Doesn't your barrier just hang, and isn't that your own Option A?"** (NEW, and the sharpest one available.)

Correct, and that is why the barrier is bounded. Compensation is retried to a deadline. On exhaustion we do not proceed blind and we do not hang. We write an escalation record carrying the branch tree and the uncompensated effect list, and surface it to a human. So the terminal states are: acted with proof of cleanup, or escalated with a full account of why. Never lost, never silent. Then show it: kill the ticket service and let the gate go amber.

**"Can't you just make retries idempotent?"** Only for effects with an inverse or a key. A page is neither. The effect typing separates what idempotency solves from what it does not, and is explicit about the residue.

**"Doesn't Temporal already do this?"** Temporal does durable execution well and we use its model as our baseline. It requires deterministic replay, which is why it fails workflows it detects as nondeterministic. An agent generates its own control flow. Show the replay pane livelocking.

**"Why not have a human approve every irreversible action?"** That is the current answer and it is why decisioning is still mostly recommendation. The barrier gives a middle option: automate up to the irreversible step, escalate only genuinely ambiguous cases. Our escalation rate under the sweep is the number that matters, and here it is.

**"What does the overhead cost?"** If the benchmark landed, give the number. If not, say you have not measured under load and it is the first thing you would do. **Never bluff a number.**

**"How is this different from sagas?"** Sagas (Garcia-Molina and Salem, 1987\) give us compensation and we are their descendant; cite them, and note we compensate in reverse order as they specify. What they lack is nondeterministic control flow that can pick a different path on recovery. The branch tree and the barrier are the new part.

**"What stops it forking forever?"** Bounded at depth three, then escalate. An unbounded search over irreversible actions is the livelock we are criticising.

---

# PART 8: RISK AND FALLBACK

## 8.1 Risks

| Risk | What to do |
| :---- | :---- |
| Afternoon work started before Gate 2 | Do not. The gate is the gate |
| Claude Code interface drift | Paste Part 4 into every session. Checkpoint at Gate 1 regardless of how well it feels |
| Plausible-but-wrong generated recovery logic | The EEO checker early. Trust it over any test suite Claude Code also wrote |
| Docker or Redis problems at the venue | `AlertSource` interface with in-process fallback, written first |
| HTTP integration eats the morning | `InProcessWorld` satisfies Gate 1\. Flip to `HttpWorld` after |
| Failover half-finished near freeze | Revert cleanly. Half-working failover fails on stage |
| Barrier deadlocks during the demo | The bounded barrier (2.3). This is also the fix for the sharpest objection |
| D rat-holes on animation | Static-correct first. Cut line at item 4 |
| UI dies during demo | Dashboard never drives execution. Demo from terminal |
| Venue wifi or laptop failure | Fallback video, no exceptions |
| Demo runs long | Compress the late-delivery window (3.3). Cut evidence to one number. Never cut the diagram, the gate, or the honesty slide |

## 8.2 Cut order under pressure

Live model call, then nondeterminism experiment, then topology strip, then audit export, then overhead benchmark, then scale run, then failover, then late-arriving effects.

**Never cut:** the architecture diagram, the poison-step three-pane comparison, the barrier gate, the bounded-barrier escalation path, the honesty slide.

Note that the bounded barrier is on the never-cut list even though it is new. It is twenty minutes of work and it is the difference between an answer and a shrug when the hardest question comes.

## 8.3 Minimum viable submission

Journal, effect types, branch tree, barrier with correct scope, bounded escalation, compensation in reverse order, poison-step three-pane demo with counters, one architecture diagram, honesty slide.

Strong on its own. `InProcessWorld` is acceptable for all of it. Everything else is upside.

## 8.4 The fallback if the branch tree is not working

Decide this now, not under pressure. If divergent recovery is broken at freeze, demo the linear durable execution baseline against naive re-run: crash recovery with no duplicated effects, plus the ambiguity case and the escalation. Then present the branch tree and the barrier as the design you specified and partially built, and be honest that it is not finished.

That is a respectable submission and a far better outcome than a broken three-pane demo. Knowing you have this fallback is what lets you push hard on the ambitious version.  
