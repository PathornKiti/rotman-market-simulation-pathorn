@echo off
rem ML SPRINT A/B (Windows, practice only): the default bot (A_default) and the same bot with the ML price model driving
rem the SPRINT rule (M_ml_sprint) take turns, forever, market recorded: heat 1 = A, heat 2 = M, heat 3 = A, ...
rem Double-click it, or run `scripts\ab_ml_sprint.bat` from the repo folder. Ctrl-C quits (and cancels resting orders).
rem Logs: logs\liability-<time>__A_default.log / __M_ml_sprint__....log; SPRINT lines driven by the model end in [ML model].
rem NOT for the graded heats: run the plain default there.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
python scripts\review_batch.py check
if not exist docs\results\ml_2026-10-15\models.json (echo models.json missing - git pull & pause & exit /b 1)
echo.
echo === ML A/B: ritc run liability --live --forever --record --profile alternate:A_default,M_ml_sprint ===
echo === start this BEFORE the heat opens; only ONE bot window per account ===
echo.
ritc run liability --live --forever --record --profile alternate:A_default,M_ml_sprint
pause
