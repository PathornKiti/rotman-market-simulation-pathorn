@echo off
rem DEFAULT RUN (Windows): every heat with the default bot (= S_staging, promoted 2026-10-15), forever, market recorded.
rem Double-click it, or run `scripts\staging.bat` from the repo folder. Ctrl-C quits (and cancels resting orders).
rem Logs: logs\liability-<time>__A_default.log (read this) + logs\data\...jsonl (for the analysis tools).
rem Compare with the frozen bot on the Mac: docs\results\screening_2026-10-15\paired.py replays every heat under the previous bot and S.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
python scripts\review_batch.py check
echo.
echo === DEFAULT (= S): ritc run liability --live --forever --record --profile A_default ===
echo === start this BEFORE the heat opens; only ONE bot window per account ===
echo.
ritc run liability --live --forever --record --profile A_default
pause
