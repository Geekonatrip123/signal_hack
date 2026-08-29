# PALIMPSEST

**Divergence-safe durable execution for agent decisioning.**

When an automated decisioning layer crashes mid-decision, it either loses the signal
or duplicates it. Both are the exact failure the decisioning layer was built to
eliminate. It is not an intelligence problem. It is an architecture problem.

A palimpsest is a manuscript where the erased writing still shows through. That is
this journal: the path the system abandoned is still legible, which is what makes the
cleanup provable and the audit trail real.

Two-page design summary: [`DESIGN.md`](DESIGN.md) — architecture, the seven design
decisions and the scenario analysis. 

---

## The problem

An agent classifies an incident, acts on that classification, and the action fails
because the classification was wrong. Recovery now has two bad options:

- **Replay the journaled decision.** It was wrong the first time, so it fails again,
  forever. The signal is lost and nobody is paged.
- **Re-run the agent from scratch.** It reaches a better answer, but the effects from
  the first attempt are still standing: a duplicate ticket, a duplicate post, and a
  second phone call that explains nothing.

Durable execution frameworks guarantee the *first* behaviour. That guarantee is the
bug when the thing being replayed is a judgement rather than a computation.

PALIMPSEST takes a third path: fork the journal at the decision, prove the abandoned
branch is cleaned up, and only then take the irreversible action.

---

## Architecture

![Architecture](signal-labs-hack-architecture.drawio.svg)

Four OS processes, one container and one shared journal. The property that matters is
that **nothing grades itself**: the orchestrator acts, the ground-truth ledger records
what actually happened, and they are different processes.

| Port | Process | Role |
| --- | --- | --- |
| 8000 | dashboard | read-only; never drives execution |
| 8100 | ledger | ground truth, the oracle |
| 8101 | ticket | reversible effect |
| 8102 | channel | reversible effect |
| 8103 | pager | **irreversible and unobservable** |
| 6379 | redis | alert stream (optional) |

The pager is the hard case by construction. A ticket can be closed and a post can be
deleted, and both can be queried after a timeout. A phone call can be neither undone
nor asked about, which is why `unknown` is its own outcome rather than a synonym for
`failed`.

---

## Setup

Requires **Python 3.10+**.

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
source .venv/bin/activate        # macOS / Linux
pip install -r requirements.txt
```

**The core demo needs nothing from `requirements.txt`.** The journal, branch tree,
barrier, bounded escalation, compensation driver, EEO checker and `InProcessWorld`
are stdlib only. Install the requirements for the HTTP topology, the dashboard,
Redis ingest and the tests.

---

## Verify the whole thing in one command

```bat
verify.bat
```

Cleans stale journals and caches, starts Redis and the four effect services, then runs
the tests, the demo, the topology smoke test and a real distributed run end to end.
Expected last line is `ALL CHECKS PASSED` with exit code 0. Step-by-step notes, and
what each check proves, are in [`VERIFY.md`](VERIFY.md).

---

## Run it

```bash
python demo.py                       # GATE 2: the poison step, three panes
python demo.py --slow 0.35           # paced for the room
python demo.py --scenario residue    # the double buzz and the supersede annotation
python demo.py --scenario compfail   # compensation fails, the barrier escalates
python demo.py --scenario compretry  # compensation fails, retries, and succeeds
python demo.py --scenario zombie     # the page that times out and lands anyway
python demo.py --scenario all        # every scenario in turn
python demo.py --sweep               # the crash sweep, evidence table, escalation rate
python demo.py --failover            # lease, epoch, fencing token
python demo.py --bench               # what durable execution costs per decision
pytest                               # invariant tests
```

The dashboard, in a second terminal:

```bash
python run_dashboard.py                  # http://127.0.0.1:8000, read-only
python run_dashboard.py --allow-control  # adds the Run buttons on the page
python demo.py --slow 0.35               # drives it from a terminal instead
```

Six tabs: **Live** (the three panes plus the distributed run), **Architecture**,
**Scenarios**, **Edge cases**, **Analysis** and **Guide**, which explains every element
on the page. Keys `1`-`6` switch tabs and the toggle top-right flips light/dark.

With `--allow-control` the Live tab gets two buttons. **Run** executes a scenario
across all three panes. **Run distributed** drives the real thing — producer, Redis,
orchestrator, HTTP services — into the gold pane above them. That pane is not a fourth
strategy; it is palimpsest again, over real sockets instead of in-process.

Each pane shows its **EEO verdict**. The dashboard cannot compute one — grading needs
the ledger, and a verdict a process grades for itself is worth nothing — so whichever
process held the ledger writes it beside the journal and the dashboard displays it.

---

## Running it as an actual distributed system

Everything above runs in one process against `InProcessWorld`. That is a faithful
model of the failure semantics, but it is a model: a "partition" is a boolean and a
"crash" is an exception. The topology below is the real thing — separate OS processes,
real sockets, real timeouts, a fencing token refused across a network boundary.

**Start the effect layer** (four processes: ledger, ticket, channel, pager):

```bash
python run_services.py
```

**Verify it before trusting it.** Nothing in the HTTP or Redis path is covered by
`pytest`, so check each layer independently:

```bash
python smoke.py            # deps, services, idempotency, fencing, timeouts, Redis
python smoke.py --http     # just the effect layer
python smoke.py --redis    # just the stream
```

**Run the demo against it** — same code above `World`, one flag:

```bash
python demo.py --world http
```

**The stream** (optional; falls back to an in-process queue if Redis is unreachable):

```bash
docker compose up -d
python run_producer.py --count 20 --rate 4
python run_producer.py --stats            # depth, lag, pending entries
```

Docker Engine inside WSL2 works as well as Docker Desktop; the container publishes
6379 to the host either way, so `REDIS_URL` needs no change. If the orchestrator
prints `[ingest] redis unavailable ... falling back to in-process queue`, the run
proves nothing about the stream — that line is the one to watch for.

**Two orchestrators, racing for real.** This is what makes it a distributed system
rather than a client calling servers — real concurrency, real lease contention, and a
deposed leader refused by a real service over a real socket:

```bash
python run_orchestrator.py --owner orch-a --source redis --world http
python run_orchestrator.py --owner orch-b --source redis --world http   # other terminal
python run_producer.py --count 20 --rate 4                             # third terminal
```

Both point at the same journal file (`--db`, default `.palimpsest/shared.db`) — that
is the shared state at the centre of the 3.1 topology. Now:

- **`kill -9` the leader mid-workflow.** The standby takes the lease after the TTL,
  the epoch increments, and the workflow resumes from the journal. If the dead leader
  ever comes back, the pager service refuses it — it is not trusted to stand down.
- **`kill -9` the ticket service.** Watch the barrier try to compensate, fail, and
  escalate within the deadline rather than hanging.
- **Kill an orchestrator between delivery and ack.** The alert stays pending in the
  consumer group; the surviving orchestrator reclaims it with `XAUTOCLAIM` and
  re-runs it. Re-running is safe because the workflow id is derived from the alert id,
  so the journal absorbs the repeat.

Faults can also be injected live, without killing anything:

```bash
curl -X POST localhost:8101/admin/faults -H 'content-type: application/json' \
     -d '{"down_services":["ticket"]}'
curl -X POST localhost:8103/admin/faults -H 'content-type: application/json' \
     -d '{"latency_s":3.0}'          # exceeds the 2s client timeout -> real unknown
```

### What is real here, and what still is not

Real: separate OS processes with distinct pids, real sockets, genuine read timeouts
(the services deliberately do **not** enforce the caller's deadline, so slow really is
indistinguishable from crashed), connection-refused distinguished from timeout,
per-workflow epoch fencing enforced by the service, Redis consumer groups with
pending-entry recovery, and one shared SQLite journal under WAL.

Still not real: crash injection inside the sweep is an in-process exception rather
than a killed process, so `--sweep --world http` exercises a real effect layer with a
simulated orchestrator death. Killing the orchestrator by hand (above) is the real
version; the sweep does not automate it.

**One thing the HTTP path needs that the in-process path does not:** the services are
long-lived and every demo pane runs the same alert, hence the same workflow id. Pane
one leaves the epoch high-water mark above where pane two starts, which would fence
pane two out entirely. `make_ctx` resets the services and the ledger between panes for
exactly this reason — the equivalent of the fresh `InProcessWorld` each pane gets
in-process.

---

## What you should see

`python demo.py` runs one crash through three engines that differ only in four
capability flags — same journal, same tools, same effect layer.

```
              tickets  posts  pages   outcome
PINNED           1       1      0     livelocked, nobody paged
NAIVE            2       2      1     duplicate ticket, duplicate post, no cleanup
PALIMPSEST       1       1      1     forked, gate held on 2 uncompensated,
                                      drained LIFO, gate lifted, rota-Y paged once
```

`--scenario residue` is the harder version, where the wrong page already succeeded:

```
PINNED           1       1      1     committed the misclassification (rota-X)
NAIVE            2       2      2     two phones ring, the second explains nothing
PALIMPSEST       1       1      2     residue recorded, second page supersedes the first
```

Palimpsest's 2 there is not a bug and not a hedge. The rota-X page is irreversible
and already rang; it is recorded as permanent residue and the rota-Y page carries a
supersede annotation naming it. A second phone ringing is acceptable when the second
ring explains the first (2.4).

---

## Layout

```
palimpsest/types.py        frozen interfaces (Part 4)
palimpsest/journal.py      SQLite WAL journal, branch tree, lease + fencing epoch
palimpsest/engine.py       orchestrator: barrier, compensation driver, divergence,
                           escalation, reconciliation        <- review this by hand
palimpsest/tools.py        the eight tools, effect types, scripted trace
palimpsest/world.py        ground-truth ledger, InProcessWorld, fault injection
palimpsest/http_world.py   HttpWorld + HttpLedger, same Protocol, flag swap
palimpsest/services.py     the three effect services and the ledger service (FastAPI)
palimpsest/ingest.py       AlertSource: Redis Streams + in-process fallback
palimpsest/checker.py      the EEO checker (3 clauses), evidence table, escalation rate
palimpsest/sweep.py        crash at every boundary x every fault mode
palimpsest/bench.py        the overhead benchmark, decomposed by layer
palimpsest/view.py         journal -> dashboard state (pure functions)
palimpsest/dashboard.py    read-only dashboard server
palimpsest/static/         the dashboard itself
palimpsest/audit.py        branch tree rendered as an incident post-mortem
palimpsest/failover.py     two orchestrators, lease, epoch, fencing
palimpsest/scenarios.py    the seven runnable scenarios

demo.py                    the three-pane demo, sweep, benchmark, failover
smoke.py                   layer-by-layer check of the HTTP and Redis topology
run_services.py            ledger + ticket + channel + pager, one process each
run_orchestrator.py        one orchestrator process; run two for a real leader race
run_producer.py            synthetic alerts into the Redis stream
run_dashboard.py           read-only dashboard server

tests/test_invariants.py   24 invariant tests
conftest.py                puts the repo root on sys.path for pytest
verify.bat                 one-command end-to-end verification
VERIFY.md                  what each check proves, and its expected output
docker-compose.yml         Redis for the alert stream
requirements.txt           HTTP topology, dashboard, Redis, tests
```

---

## The three ideas

**The journal is a tree, not a log.** Recovery that wants a different path forks a
branch and marks the old one abandoned. The journal records what we tried, not just
what we did. Institutional Memory for decisions that were rejected.

**Tool calls are typed by effect.** `pure` / `idempotent` / `compensatable` /
`irreversible`, crossed with `observable` / `unobservable`. This makes recovery
decidable instead of heuristic.

**The irreversible barrier.** A branch may not execute an irreversible effect while
**any abandoned branch in the same workflow** holds uncompensated compensatable
effects. Workflow scope, not sibling scope — fork twice and the first abandoned
branch becomes an aunt and would slip a sibling check (2.2).

The barrier is **bounded**. Compensation is retried to a deadline. On exhaustion the
workflow does not silently proceed and does not hang: it writes an escalation
carrying the branch tree and the uncompensated effect list, and surfaces it to a
human (2.3). The terminal states are *acted with proof of cleanup* or *escalated with
a full account of why*. Never lost, never silent.

---

## Correctness

Effect-Exactly-Once, after crash, recovery and quiescence:

1. **No duplication.** No irreversible effect commits more than once per logical
   decision point, identified by its idempotency key. A second irreversible effect is
   legitimate only when it carries a supersede annotation naming the first.
2. **No loss.** Every workflow terminates in a committed action or a surfaced
   escalation. Never silently, never still running.
3. **Clean abandonment.** Every compensatable effect on an abandoned branch has a
   matching compensation, in reverse order, and no effect on an active branch is
   compensated.

**Bounded Ambiguity.** At most one irreversible + unobservable effect may sit in
unknown state per workflow, and it is surfaced, never silently resolved. Clause 2 is
what makes that safe: an unknown page still terminates in a surfaced escalation, so
the signal is not lost even when the effect status is not knowable.

All three clauses are checked against the **ground-truth ledger**, which sits outside
the system and records what actually happened to the world, independently of what the
system journaled or believed. `python demo.py --sweep` emits the evidence table; the
line that matters is `unexplained violations`.

**Explained vs unexplained.** A clause-3 shortfall accompanied by an escalation record
naming those exact effects is an *explained* violation — the honest degraded outcome
of 2.3, not a bug. Only unexplained violations count against the system.

**EEO does not claim the decision was right.** In `--scenario residue`, pinned replay
passes all three clauses while paging the wrong rota. Exactly-once is a claim about
effects, not about judgement. That is why the branch tree and the barrier exist.

---

## The numbers

Two commands produce every number that gets quoted. Neither is estimated.

**`python demo.py --sweep`** — the evidence table (§2.8), plus a per-fault-mode
breakdown and the escalation-reason histogram.

- `unexplained violations` is the headline. If it is not zero, that is a real bug.
- `escalation rate` is the answer to §7.6's *"why not have a human approve every
  irreversible action?"* — the barrier automates up to the irreversible step and
  escalates only genuinely ambiguous cases, and this is how often that happens under
  fault. The reason histogram says *what* forced a human in, which is the follow-up
  question.
- The per-fault-mode table matters because the rate is not uniform, and quoting the
  headline percentage alone is misleading in both directions. Under `crash` — the
  fault durable execution is actually built for — the rate is **0%**: every run acted,
  with cleanup proven. The permanent-outage modes escalate every time by construction.
  `partition-transient` is the middle case, where the service returns before the
  retry budget is spent and the drain completes.

**`python demo.py --bench`** — the overhead benchmark (§7.6's *"what does the
overhead cost?"*). It reports four configurations rather than one number, because one
number would hide the only interesting thing:

```
bare tool calls                 the floor, no durability
+ journal (:memory:)            bookkeeping only, SQLite I/O removed
+ journal, synchronous=NORMAL   on disk, no fsync per commit
+ journal, synchronous=FULL     what we ship
```

FULL minus NORMAL is the fsync bill and it dominates. That is a deliberate purchase,
not waste: a crash between the `INTENT` record and the effect must not lose the
`INTENT` record, or recovery cannot know the step was started and the effect commits
with nothing pointing at it. `synchronous=NORMAL` is a supported knob
(`Journal(path, synchronous="NORMAL")`) and the benchmark prices exactly what
relaxing it buys back.

It then repeats the bare-vs-durable comparison with a realistic per-effect latency
injected into **both** rows (`--bench-effect-latency-ms`, default 25). The in-process
floor is eight dict writes, so dividing by it yields a true but useless multiple —
nothing pages an engineer that way. The latency is simulated, but applied identically
to both rows it cancels, so the ratio is *measured* rather than extrapolated.

**Quote `ms per step` and the realistic ratio. Never quote the multiple-of-bare.**

`--bench-concurrency K` additionally measures K threads sharing one journal. That is
single-process SQLite contention, **not** the distributed scale run of §5.7, and the
output labels it as such.

## Where it falls over

- **Exactly-once for irreversible + unobservable effects is impossible.** Two
  generals. We bound the ambiguity to one place and report it.
- **Compensation restores state, not history.** A deleted channel post was still
  seen. An irreversible effect on an abandoned branch is permanent residue, recorded
  and superseded, never silently erased.
- **The barrier is bounded, not absolute.** If compensation cannot complete we
  escalate to a human rather than blocking or acting blind. A degraded outcome,
  chosen deliberately over the two alternatives.
- **Agent decisions are scripted.** Reproducible, but not proof of behaviour at
  scale. There is no live model call in this build.
- **Fault coverage is not proof.** The sweep covers crash / timeout / partition /
  late-delivery at every step boundary. It does not cover byzantine services,
  clock skew, or SQLite corruption.
- **The overhead number is a single-machine, in-process figure.** It isolates the
  durability machinery from the network on purpose. Over `HttpWorld` every
  configuration moves by the same network constant, so the comparison between rows
  holds, but the absolute number does not transfer.
- **Failover is single-host.** `run_orchestrator.py` gives two real orchestrator
  processes racing for one lease against a real effect layer, which is genuine
  concurrency and genuine fencing — but all on one machine. Node loss across two
  physical hosts, and network partitions between them, are not exercised.
- **The sweep's crashes are in-process.** `--sweep --world http` runs a real effect
  layer with a simulated orchestrator death. Killing the orchestrator for real is a
  manual step, not part of the swept evidence.

---

## Deviations from Part 4

Part 4 is frozen. Two changes were necessary and are called out rather than hidden:

1. **`effect_key` returns `"{workflow_id}:{sha256(wf|branch|seq)[:16]}"`** instead of
   a bare digest. Still derived from exactly the three inputs Part 4 names, still
   stable across epochs. Per-workflow epoch fencing (3.4) and the multi-workflow
   crash sweep (2.8) both need the workflow to be recoverable from a key, and an
   opaque digest cannot provide it.
2. **`World` carries `compensate`.** Part 4 puts `compensate` on `Tool` but omits it
   from `World`, and the compensation driver has nowhere else to call.

Nothing else in Part 4 changed. `ResultStatus`, `RecordKind`, `Branch.status`,
`Branch.depth`, `JournalRecord.ts` / `.detail` and the `timeout_s` parameters are all
as specified, and every `RecordKind` is now actually emitted.

---

## Knobs

| Knob | Where | Note |
| --- | --- | --- |
| `MAX_FORK_DEPTH` | `engine.py`, or `max_fork_depth=` | 3. Say the number out loud. |
| `MAX_COMP_ATTEMPTS` / `BARRIER_DEADLINE_S` | `engine.py`, or per-Orchestrator | the bounded barrier |
| `--late-delay` | `demo.py` | 8 on stage, 40 in the sweep (3.3) |
| `--slow` | `demo.py` | pause between steps so the room can watch |
| `--narrate` / `--slow` | `demo.py` | stream events live; `--slow` implies it |
| `--world http` | `demo.py` | flag swap to the HTTP topology |
| `synchronous` | `Journal(path, synchronous=)` | `FULL` (default), `NORMAL`, `OFF`. `--bench` prices the difference. |
| `faults.*` | `world.py` `FaultConfig` | latency, jitter, fail, timeout, down, empty rotas |
| `POST /admin/faults` | any effect service | same knobs, live, over HTTP |

---

## Not built

- Live model call at step 3 (first on the 8.2 cut list; the trace is scripted).
- The concurrent scale run of §5.7: Redis stream lag and p99 under a sustained
  producer. `--bench-concurrency` measures single-process journal contention, which
  is a different and smaller claim. No lag number is quoted.
- Topology strip animation on the live `kill -9`; the strip renders service health
  and the current epoch, but does not animate packets.
