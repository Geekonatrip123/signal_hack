# PALIMPSEST core

    python3 demo.py

## Layout

    palimpsest/types.py     frozen interfaces (Part 4, corrected)
    palimpsest/journal.py   SQLite WAL journal, branch tree, leases/epochs
    palimpsest/engine.py    orchestrator: barrier, compensation driver, divergence, escalation
    palimpsest/tools.py     8 tools, effect types, scripted trace
    palimpsest/world.py     ground-truth ledger, InProcessWorld, fault injection
    palimpsest/checker.py   EEO checker (3 clauses) + evidence table
    demo.py                 three-pane poison step

## Verified working

Poison step runs end to end. Barrier blocks the page with 2 uncompensated
effects, compensation drains LIFO, gate releases, rota-Y paged once.
Scoreboard 1/1/0 (pinned), 2/2/1 (naive), 1/1/1 (palimpsest).

## Knobs

    MAX_FORK_DEPTH, MAX_COMP_ATTEMPTS, BARRIER_DEADLINE_S   engine.py
    faults.empty_rotas / timeout_tools / down_services      demo.py
    faults.late_delivery_delay_s                            8.0 stage, 40.0 sweep

## Not built yet

- HttpWorld (same Protocol as InProcessWorld, flag-swap)
- crash-after-page variant for the 2/2/2 double buzz
- dashboard, failover, Redis ingest
