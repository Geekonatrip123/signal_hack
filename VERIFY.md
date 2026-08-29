# Verifying PALIMPSEST end to end

One command:

```
verify.bat
```

Expected last line: **`ALL CHECKS PASSED`**, exit code 0. Anything else means read
`_verify_out\`, which keeps the full output of every step.

```
verify.bat --keep     leave the effect services running afterwards
```

This document explains what each step does, **why it is there**, and what its output
should look like — so you can run the commands by hand and know whether the answer is
the right one. A green run that never touched Redis is the failure mode this is built
to make visible.

---

## What the topology is

Six moving parts, in four OS processes plus a container:

```
run_producer.py  ──▶  Redis stream (alerts:incoming)   [docker, in WSL]
                              │
                              ▼
                      run_orchestrator.py              [journal: .palimpsest/shared.db]
                              │  HTTP
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
          ticket:8101   channel:8102     pager:8103
              └───────────────┼───────────────┘
                              ▼
                        ledger:8100        ← the out-of-process oracle
```

The ledger is the point. It is a **separate process** that records ground truth, so
correctness is checked against something the orchestrator cannot lie to.

---

## Prerequisites

| Thing | Check | If missing |
|---|---|---|
| venv | `.venv\Scripts\python.exe` exists | `python -m venv .venv` then `pip install -r requirements.txt` |
| Redis | `wsl -d Ubuntu-24.04 -- docker ps` shows `palimpsest-redis` | `verify.bat` starts it automatically |

Redis runs as a container inside WSL2 (Docker Engine, not Docker Desktop). It is
published to Windows `localhost:6379`, so nothing in the Python code needs to know
it lives in WSL. `REDIS_URL` defaults to `redis://localhost:6379/0`.

> **`wsl --shutdown` kills Redis.** The container has no restart policy, so bring it
> back with `wsl -d Ubuntu-24.04 -- docker start palimpsest-redis`.

> **WSL tears the distro down once the last `wsl.exe` client disconnects**, and that
> SIGTERMs the container with it — observed dying 16 seconds after `docker compose up`.
> `verify.bat` holds one hidden `wsl.exe` client open for the length of the run and
> drops it at teardown. If you start Redis by hand, keep a WSL terminal open or the
> container will vanish under you a few seconds later.

---

## Step 1 — Remove remnants

```
rd /s /q .palimpsest
rd /s /q .pytest_cache
for /d /r . %d in (__pycache__) do @if exist "%d" rd /s /q "%d"
```

**Why:** `.palimpsest\shared.db` is the journal. A workflow already committed there
will **replay** instead of executing, so a stale journal turns a real test into a
no-op that still prints `EEO PASS`. This is the most likely way to fool yourself.

`verify.bat` also kills anything already listening on 8100–8103. A stale service
process running pre-edit code is the second most likely way — you fix a bug, rerun,
and watch the old process fail in exactly the old way.

---

## Step 2 — Redis

```
python -c "import redis; print(redis.Redis(port=6379).ping())"
```

Expected: `True`

If it fails:

```
wsl -d Ubuntu-24.04 -u root -- bash -lc "systemctl start docker; cd /mnt/c/Users/18mps/SignalLabs/signal_hack && docker compose up -d"
```

---

## Step 3 — Effect services

```
python run_services.py
```

Expected — four processes, each with its own pid:

```
  ledger   pid 24920  http://127.0.0.1:8100
  ticket   pid 29484  http://127.0.0.1:8101
  channel  pid 18020  http://127.0.0.1:8102
  pager    pid 25976  http://127.0.0.1:8103

  all four up. ctrl-c to stop, or kill one pid to break a service.
```

**Why separate processes:** distinct pids are what make this a distributed system
rather than a single interpreter pretending. `smoke.py` asserts there are four
distinct pids, so this cannot quietly regress into threads.

---

## Step 4 — Package imports

```
python -c "import palimpsest; print(palimpsest.__version__)"
```

Expected: `1.0.0`

---

## Step 5 — Unit tests

```
pytest -q
```

Expected: `24 passed`

**Why it is not sufficient:** these exercise the in-process world only — no sockets,
no Redis. Part 0.2 of the plan is explicit that a green suite is not evidence. The
oracle is the ledger, checked in steps 7–10.

---

## Step 6 — Three-pane demo

```
python demo.py
```

Expected scoreboard:

| Mode | tickets / posts / pages | Outcome | EEO |
|---|---|---|---|
| pinned | **1 / 1 / 0** | livelocked, nobody paged | FAIL — `no_loss` |
| naive | **2 / 2 / 1** | completed | FAIL — `clean_abandonment` |
| palimpsest | **1 / 1 / 1** | completed, rota-Y paged | **PASS** |

and at the bottom:

```
  unexplained violations:    0    <- this is the number that matters
```

**The two FAILs are the point, not a problem.** They are the baselines the system is
being compared against: `pinned` livelocks and loses the alert; `naive` acts twice and
abandons two uncompensated effects. Only `palimpsest` gets 1/1/1 with a clean verdict.

---

## Step 7 — Topology smoke

```
python smoke.py
```

Expected: `20 passed, 0 failed, 0 skipped`

Covers HTTP round trips, idempotency across a real socket, probe observability,
epoch fencing enforced by the service (HTTP 409), a genuine read timeout yielding
`unknown`, a refused connection yielding `failed`, and five Redis stream checks.

> **A `skip` is a warning, not a pass.** `skip redis reachable` means the stream was
> never exercised and five of those checks proved nothing. `verify.bat` treats any
> skip as a failure for this reason.

Useful subsets: `python smoke.py --http`, `python smoke.py --redis`.

---

## Step 8 — Redis → orchestrator → HTTP, fresh execution

```
python run_producer.py --demo --count 1
python run_orchestrator.py --owner orch-a --source redis --world http --once --narrate
```

**First line of the output is the one that matters:**

```
[ingest] redis stream alerts:incoming group orchestrators
```

If you instead see `[ingest] redis unavailable (...); falling back to in-process
queue`, everything below it is meaningless as a Redis test. The fallback is
deliberate — a Docker problem must not kill a live demo — but it means a green run
proves nothing about the stream. **Check this line every time.**

Expected narration (the interesting part):

```
    [orch-a] branch_abandoned
    [orch-a] forked no responder on rota-X
    [orch-a] barrier_blocked 2 uncompensated effects on abandoned branch
    [orch-a] compensated post_to_channel
    [orch-a] compensated create_ticket
    [orch-a] barrier_released page_oncall
  a-1001: completed   rota=rota-Y       92ms  EEO PASS
```

Read it as: the first branch paged a rota with nobody on it, so the workflow forked;
the barrier refused to let the new branch page anyone until the abandoned branch's two
effects were cleaned up; both were compensated; the barrier lifted; rota-Y was paged
once. **~90–140 ms** — real work across four processes.

---

## Step 9 — Replay is a no-op

Run the **exact same two commands again**, without clearing anything:

```
python run_producer.py --demo --count 1
python run_orchestrator.py --owner orch-a --source redis --world http --once --narrate
```

Expected:

```
    [orch-a] step_replayed fetch_alerts
    ... (all 8 steps)
  a-1001: completed   rota=rota-Y         0ms  EEO PASS
```

**Why:** `workflow_id` is derived from the alert id, so a redelivered `a-1001` lands on
the same workflow, and the journal already has every step committed. `0ms` and
`step_replayed` are the crash-recovery guarantee visible in one line — a recovering
orchestrator resumes rather than redoing.

Steps 8 and 9 back to back are the whole story in four commands: **92 ms and a full
compensation dance, then 0 ms and nothing at all, with identical ledger counts.**

---

## Step 10 — Exactly-once under redelivery

```
curl.exe "http://127.0.0.1:8100/counts?net=true"
python run_producer.py --demo --count 1 --redeliver
python run_orchestrator.py --owner orch-a --source redis --world http --once
curl.exe "http://127.0.0.1:8100/counts?net=true"
```

Expected — **byte-identical before and after**:

```json
{"create_ticket":1,"page_oncall":1,"post_to_channel":1,"update_status_page":1,"write_dedupe_marker":2}
```

**Why this is the headline result:** `--redeliver` publishes the same alert twice, on
top of one already delivered. Redis Streams are at-least-once, so all three arrive.
The out-of-process ledger still shows exactly one ticket, one post, one page.

`write_dedupe_marker: 2` is correct — one per branch (original and forked). It is a
pure marker, not an outside-world effect, so it is not compensated.

> **PowerShell gotcha:** bare `curl` is an alias for `Invoke-WebRequest`, which prints a
> script-execution warning and a wall of HTTP metadata. Use `curl.exe` or
> `Invoke-RestMethod`.

---

## Resetting between manual runs

To make step 8 execute rather than replay, all four must be cleared — journal,
service idempotency keys, ledger, and stream:

```
rd /s /q .palimpsest
python -c "import httpx; [httpx.post(u,timeout=5) for u in ['http://127.0.0.1:8100/reset','http://127.0.0.1:8101/admin/reset','http://127.0.0.1:8102/admin/reset','http://127.0.0.1:8103/admin/reset']]"
python -c "import redis; from palimpsest.ingest import DEFAULT_STREAM; redis.Redis(port=6379).delete(DEFAULT_STREAM)"
```

Clearing only the journal still gives you the narration, but the ledger counts will
not move: the services deduplicate on the effect key independently, so a replayed
effect returns its cached result and never re-enters the ledger.

---

## Reading failures

| Symptom | Meaning |
|---|---|
| `[ingest] redis unavailable ... falling back` | Redis is down; the run tested nothing about the stream |
| `skip` in `smoke.py` | Same — the five stream checks proved nothing |
| `step_replayed` when you expected execution | Stale `.palimpsest\shared.db`; clear it |
| `HTTP 422 ... "loc":["query","body"]` | A FastAPI model was not resolved — see the note atop `palimpsest/services.py` |
| `services down: [...]` | `run_services.py` is not running, or a stale process holds the port |
| `XAUTOCLAIM returned nothing`, then steps 8-10 fail | Redis died mid-run. Check `docker ps -a`: a 16-second lifetime and `exit=0` means WSL tore the distro down, not a code fault |
| ledger counts moved in step 10 | A genuine exactly-once violation. This is the one that matters |

---

## Not covered here

**The two-orchestrator leadership race** — needs two terminals and a `kill -9`, so it
is not automatable in one script:

```
python run_orchestrator.py --owner orch-a --source redis --world http
python run_orchestrator.py --owner orch-b --source redis --world http
python run_producer.py --count 20 --rate 4
```

Kill orch-a mid-workflow. orch-b takes the lease, the epoch increments, and if orch-a
comes back its effects are refused by the pager service over a real socket rather than
trusted to stand down. Service-side fencing is covered by `smoke.py`; the leadership
handover is not.
