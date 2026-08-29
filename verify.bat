@echo off
REM ===========================================================================
REM  PALIMPSEST -- end-to-end verification
REM
REM    verify.bat            full run: clean, start topology, verify, tear down
REM    verify.bat --keep     leave the effect services running when it finishes
REM
REM  Owns the whole lifecycle deliberately.  It kills anything already on ports
REM  8100-8103 before starting its own services, because a stale process running
REM  pre-edit code is the single most misleading failure mode here -- you fix a
REM  bug, rerun, and watch the old process fail in exactly the old way.
REM
REM  Expected result: ALL CHECKS PASSED, 0 failures.
REM  See VERIFY.md for what each step proves and what its output should look like.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "OUT=_verify_out"
set "WSL_DISTRO=Ubuntu-24.04"
set /a FAILURES=0
set /a STEP=0
set "STARTED_SERVICES=0"
set "KEEP=0"
if /i "%~1"=="--keep" set "KEEP=1"

echo.
echo ===========================================================================
echo   PALIMPSEST end-to-end verification
echo ===========================================================================

REM ------------------------------------------------------------------ preflight
if not exist "%PY%" (
    echo   FATAL: %PY% not found.
    echo   Create the venv first:
    echo       python -m venv .venv
    echo       .venv\Scripts\activate
    echo       pip install -r requirements.txt
    exit /b 1
)

REM ---------------------------------------------------------- 1. clean remnants
set /a STEP+=1
echo.
echo [%STEP%] Removing remnants
REM Journals, caches and compiled bytecode. A stale .palimpsest/shared.db is what
REM makes a fresh run replay instead of execute, so this is not mere tidiness.
if exist ".palimpsest"   rd /s /q ".palimpsest"
if exist ".pytest_cache" rd /s /q ".pytest_cache"
if exist "%OUT%"         rd /s /q "%OUT%"
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
mkdir "%OUT%" 2>nul
echo     removed .palimpsest, .pytest_cache, __pycache__, %OUT%

REM Anything already holding the service ports is stale by definition.
for %%p in (8100 8101 8102 8103) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:"LISTENING" ^| findstr /C:":%%p "') do (
        echo     killing stale listener on %%p ^(pid %%a^)
        taskkill /PID %%a /T /F >nul 2>&1
    )
)

REM ---------------------------------------------------------------- 2. redis up
set /a STEP+=1
echo.
echo [%STEP%] Redis
%PY% -c "import redis,sys;sys.exit(0 if redis.Redis(port=6379,socket_connect_timeout=3).ping() else 1)" 2>nul
if not errorlevel 1 (
    echo     already up on localhost:6379
) else (
    echo     not reachable; starting the container
    REM Ask WSL to translate this repo's path rather than hardcoding one -- the repo
    REM has already moved once, and a baked-in /mnt/c/... would silently rot.
    set "WSLCWD="
    for /f "delims=" %%w in ('wsl -d %WSL_DISTRO% -- wslpath -a "%CD%" 2^>nul') do set "WSLCWD=%%w"
    if not defined WSLCWD (
        echo     FAILED: could not reach WSL distro %WSL_DISTRO%
        set /a FAILURES+=1
        goto redisdone
    )
    wsl -d %WSL_DISTRO% -u root -- bash -lc "systemctl is-active docker >/dev/null 2>&1 || systemctl start docker; cd '!WSLCWD!' && docker compose up -d" >"%OUT%\redis_up.txt" 2>&1
    set /a TRIES=0
    :waitredis
    set /a TRIES+=1
    %PY% -c "import redis,sys;sys.exit(0 if redis.Redis(port=6379,socket_connect_timeout=3).ping() else 1)" 2>nul
    if not errorlevel 1 goto redisok
    if !TRIES! GEQ 30 (
        echo     FAILED: redis never came up. See %OUT%\redis_up.txt
        set /a FAILURES+=1
        goto redisdone
    )
    ping -n 2 127.0.0.1 >nul
    goto waitredis
    :redisok
    echo     up on localhost:6379
)
:redisdone

REM ------------------------------------------------------------- 3. services up
set /a STEP+=1
echo.
echo [%STEP%] Effect services ^(ticket, channel, pager, ledger^)
powershell -NoProfile -Command "$p = Start-Process -FilePath '%PY%' -ArgumentList 'run_services.py' -PassThru -WindowStyle Minimized -RedirectStandardOutput '%OUT%\services.log' -RedirectStandardError '%OUT%\services.err'; $p.Id | Out-File -Encoding ascii '%OUT%\services.pid'"
set "STARTED_SERVICES=1"

set /a TRIES=0
:waitsvc
set /a TRIES+=1
%PY% -c "import httpx,sys;sys.exit(0 if all(httpx.get(f'http://127.0.0.1:{p}/health',timeout=2).status_code==200 for p in (8100,8101,8102,8103)) else 1)" 2>nul
if not errorlevel 1 goto svcok
if !TRIES! GEQ 40 (
    echo     FAILED: services never became healthy. See %OUT%\services.err
    set /a FAILURES+=1
    goto teardown
)
ping -n 2 127.0.0.1 >nul
goto waitsvc
:svcok
echo     all four healthy on 8100-8103

REM --------------------------------------------------------------- 4. importable
set /a STEP+=1
echo.
echo [%STEP%] Package imports
%PY% -c "import palimpsest;print('    palimpsest',palimpsest.__version__)"
if errorlevel 1 (
    echo     FAILED: package does not import
    set /a FAILURES+=1
)

REM -------------------------------------------------------------------- 5. tests
set /a STEP+=1
echo.
echo [%STEP%] Unit tests ^(expect 24 passed^)
%PY% -m pytest -q >"%OUT%\pytest.txt" 2>&1
if errorlevel 1 (
    echo     FAILED -- see %OUT%\pytest.txt
    set /a FAILURES+=1
) else (
    for /f "delims=" %%l in ('findstr /C:"passed" "%OUT%\pytest.txt"') do echo     %%l
)

REM --------------------------------------------------------------------- 6. demo
set /a STEP+=1
echo.
echo [%STEP%] Three-pane demo ^(expect 1/1/0, 2/2/1, 1/1/1 and EEO PASS^)
%PY% demo.py >"%OUT%\demo.txt" 2>&1
if errorlevel 1 (
    echo     FAILED: demo.py exited nonzero -- see %OUT%\demo.txt
    set /a FAILURES+=1
) else (
    %PY% -c "import io,sys;t=io.open(r'%OUT%\demo.txt',encoding='utf-8',errors='replace').read();need=['tickets 1   posts 1   pages 0','tickets 2   posts 2   pages 1','tickets 1   posts 1   pages 1','unexplained violations:    0'];miss=[n for n in need if n not in t];sys.exit(0) if not miss else (print('     missing:',miss),sys.exit(1))"
    if errorlevel 1 (
        echo     FAILED: scoreboard did not match -- see %OUT%\demo.txt
        set /a FAILURES+=1
    ) else (
        echo     pinned 1/1/0, naive 2/2/1, palimpsest 1/1/1, 0 unexplained violations
    )
)

REM -------------------------------------------------------------------- 7. smoke
set /a STEP+=1
echo.
echo [%STEP%] Topology smoke ^(expect 20 passed, 0 failed, 0 skipped^)
%PY% smoke.py >"%OUT%\smoke.txt" 2>&1
if errorlevel 1 (
    echo     FAILED -- see %OUT%\smoke.txt
    set /a FAILURES+=1
    findstr /C:"FAIL " "%OUT%\smoke.txt"
) else (
    for /f "delims=" %%l in ('findstr /C:"passed," "%OUT%\smoke.txt"') do echo    %%l
)
findstr /C:"skip " "%OUT%\smoke.txt" >nul
if not errorlevel 1 (
    echo     WARNING: something was SKIPPED -- redis is probably down, so the
    echo              stream was never actually exercised. See %OUT%\smoke.txt
    set /a FAILURES+=1
)

REM ------------------------------------------- 8. redis pipeline, fresh execution
set /a STEP+=1
echo.
echo [%STEP%] Redis -^> orchestrator -^> HTTP, fresh execution
call :resetall
%PY% run_producer.py --demo --count 1 >"%OUT%\producer1.txt" 2>&1
%PY% run_orchestrator.py --owner orch-a --source redis --world http --once --narrate >"%OUT%\orch_fresh.txt" 2>&1
findstr /C:"[ingest] redis stream" "%OUT%\orch_fresh.txt" >nul
if errorlevel 1 (
    echo     FAILED: fell back to the in-process queue; redis was NOT exercised
    set /a FAILURES+=1
)
findstr /C:"barrier_released" "%OUT%\orch_fresh.txt" >nul
if errorlevel 1 (
    echo     FAILED: no barrier_released -- compensation never ran
    set /a FAILURES+=1
) else (
    echo     barrier blocked, both effects compensated, barrier released
)
findstr /C:"EEO PASS" "%OUT%\orch_fresh.txt" >nul
if errorlevel 1 (
    echo     FAILED: EEO did not pass -- see %OUT%\orch_fresh.txt
    set /a FAILURES+=1
) else (
    for /f "delims=" %%l in ('findstr /C:"a-1001:" "%OUT%\orch_fresh.txt"') do echo    %%l
)

REM ----------------------------------------------------------- 9. replay is a no-op
set /a STEP+=1
echo.
echo [%STEP%] Same alert again, journal intact ^(expect replay, not re-execution^)
%PY% run_producer.py --demo --count 1 >"%OUT%\producer2.txt" 2>&1
%PY% run_orchestrator.py --owner orch-a --source redis --world http --once --narrate >"%OUT%\orch_replay.txt" 2>&1
findstr /C:"step_replayed" "%OUT%\orch_replay.txt" >nul
if errorlevel 1 (
    echo     FAILED: expected step_replayed; the workflow re-executed instead
    set /a FAILURES+=1
) else (
    echo     all steps replayed from the journal, nothing re-executed
)

REM ------------------------------------------- 10. exactly-once under redelivery
set /a STEP+=1
echo.
echo [%STEP%] At-least-once redelivery must not duplicate effects
call :counts "%OUT%\counts_before.txt"
%PY% run_producer.py --demo --count 1 --redeliver >"%OUT%\producer3.txt" 2>&1
%PY% run_orchestrator.py --owner orch-a --source redis --world http --once >"%OUT%\orch_dup.txt" 2>&1
call :counts "%OUT%\counts_after.txt"
fc "%OUT%\counts_before.txt" "%OUT%\counts_after.txt" >nul
if errorlevel 1 (
    echo     FAILED: ledger counts moved after a duplicate delivery
    echo     before: & type "%OUT%\counts_before.txt"
    echo     after:  & type "%OUT%\counts_after.txt"
    set /a FAILURES+=1
) else (
    echo     ledger counts unchanged across the duplicate:
    for /f "delims=" %%l in ('type "%OUT%\counts_after.txt"') do echo        %%l
)

REM ------------------------------------------------------------------- teardown
:teardown
echo.
if "%STARTED_SERVICES%"=="1" (
    if "%KEEP%"=="1" (
        echo   Leaving the effect services running ^(--keep^).
        echo   Stop them with:  taskkill /PID ^<pid in %OUT%\services.pid^> /T /F
    ) else (
        for /f "delims=" %%p in ('type "%OUT%\services.pid" 2^>nul') do taskkill /PID %%p /T /F >nul 2>&1
        echo   Effect services stopped.
    )
)

echo.
echo ===========================================================================
if %FAILURES%==0 (
    echo   ALL CHECKS PASSED
    echo   Full output kept in %OUT%\ if you want to read it.
    echo ===========================================================================
    exit /b 0
) else (
    echo   %FAILURES% CHECK^(S^) FAILED -- see %OUT%\ for the full output
    echo ===========================================================================
    exit /b 1
)

REM ------------------------------------------------------------------ subroutines
:resetall
REM Clear the journal, the services' idempotency keys, the ledger and the stream.
REM All four, or the next run replays instead of executing and proves nothing.
if exist ".palimpsest" rd /s /q ".palimpsest"
%PY% -c "import httpx;from palimpsest.ingest import DEFAULT_STREAM;import redis;[httpx.post(u,timeout=5) for u in ['http://127.0.0.1:8100/reset','http://127.0.0.1:8101/admin/reset','http://127.0.0.1:8102/admin/reset','http://127.0.0.1:8103/admin/reset']];redis.Redis(port=6379).delete(DEFAULT_STREAM)" >nul 2>&1
exit /b 0

:counts
REM Net ledger counts from the out-of-process oracle, sorted so fc can compare them.
%PY% -c "import httpx,json;print(json.dumps(httpx.get('http://127.0.0.1:8100/counts',params={'net':'true'},timeout=5).json(),sort_keys=True))" >%1 2>&1
exit /b 0
