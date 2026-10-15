@echo off
rem A/B RUN (Windows): the default (A_default = S since 2026-10-15) and the previous bot (P_previous) take turns, forever,
rem market recorded: heat 1 = A, heat 2 = P, heat 3 = A, ... counted from THIS session, so old logs\ do not matter.
rem Double-click it, or run `scripts\ab_default_staging.bat` from the repo folder. Ctrl-C quits (and cancels resting orders).
rem Logs: logs\liability-<time>__A_default.log / __P_previous__....log (read these) + logs\data\*.jsonl (analysis tools).
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
python scripts\review_batch.py check
echo.
echo === A/B: ritc run liability --live --forever --record --profile alternate:A_default,P_previous  (A_default, P_previous, A_default, ...) ===
echo === start this BEFORE the heat opens; only ONE bot window per account ===
echo.
ritc run liability --live --forever --record --profile alternate:A_default,P_previous
pause
