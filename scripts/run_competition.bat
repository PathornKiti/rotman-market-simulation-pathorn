@echo off
rem COMPETITION (Windows): rsi_bot every heat, forever, market recorded.
rem   accept every fixed tender priced better than the market (TOUCH ACCEPT), HOLD each block while the last 10
rem   ticks ran our way (TREND HOLD; crowd alarm on: start only on >= $0.05), unwind when it turns.
rem Double-click it, or run `scripts\run_competition.bat` from the repo folder. Ctrl-C quits (and cancels resting orders).
rem Runbook: docs\COMPETITION_RUNBOOK.md. Between heats, in a 2nd window: scripts\competition_status.bat
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
echo === 1/4 code version: must match the Mac's last commit; NO files may be listed under it ===
git log -1 --oneline
git status --short --untracked-files=no
echo.
echo === 2/4 config check ===
python scripts\review_batch.py check || (echo CONFIG CHECK FAILED - do not trade with this checkout & pause & exit /b 1)
echo.
echo === 3/4 RIT connection (full output: logs\doctor-competition.txt) ===
python -m ritc doctor > logs\doctor-competition.txt 2>&1
findstr /C:"FAILED" /C:"median" /C:"429 rate-limit" /C:"Expected bot loop" logs\doctor-competition.txt
echo   (FAILED lines = RIT client not logged in / API key wrong / API Orders not green: fix it now)
echo.
echo === 4/4 rsi_bot: ritc run liability --live --forever --record --profile rsi_bot ===
echo === start BEFORE the heat opens; ONE bot window per account; check the log says PROFILE rsi_bot ===
echo.
ritc run liability --live --forever --record --profile rsi_bot
pause
