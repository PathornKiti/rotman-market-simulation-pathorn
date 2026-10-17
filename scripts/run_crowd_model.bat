@echo off
rem CROWD TENDER MODEL (Windows): the default bot + the crowd tender model (profile crowd_model), EVERY heat, recorded.
rem In quiet heats it trades exactly like A_default; once the crowd alarm is on it also accepts fixed tenders the bot
rem declined on price that the tender model values > $0.054/sh (log: "TENDER MODEL X: crowded, ... ACCEPT").
rem Double-click it, or run `scripts\run_crowd_model.bat` from the repo folder. Ctrl-C quits (and cancels resting orders).
rem Logs: logs\liability-<time>__crowd_model__....log (read these) + logs\data\*.jsonl (analysis tools).
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
python scripts\review_batch.py check
echo.
echo === CROWD MODEL: ritc run liability --live --forever --record --profile crowd_model ===
echo === start this BEFORE the heat opens; only ONE bot window per account ===
echo.
ritc run liability --live --forever --record --profile crowd_model
pause
