@echo off
rem ML + FRONT-LOAD A/B (Windows, practice only): the default bot (A_default) and the same bot crossing the first 25%
rem of every block at once + the ML price model driving the SPRINT rule (MF_ml_front25) take turns, forever:
rem heat 1 = A, heat 2 = MF, heat 3 = A, ... (market recorded)
rem Double-click it, or run `scripts\ab_ml_front.bat` from the repo folder. Ctrl-C quits (and cancels resting orders).
rem Logs: logs\liability-<time>__A_default.log / __MF_ml_front25__....log; FRONT lines + SPRINT ... [ML model] lines.
rem NOT for the graded heats: run the plain default there.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
python scripts\review_batch.py check
if not exist docs\results\ml_2026-10-15\models.json (echo models.json missing - git pull & pause & exit /b 1)
echo.
echo === ML + FRONT-LOAD A/B: ritc run liability --live --forever --record --profile alternate:A_default,MF_ml_front25 ===
echo === start this BEFORE the heat opens; only ONE bot window per account ===
echo.
ritc run liability --live --forever --record --profile alternate:A_default,MF_ml_front25
pause
