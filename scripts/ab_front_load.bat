@echo off
rem FRONT-LOAD A/B (Windows, practice only): the default bot (A_default) and the same bot crossing the first 25% of
rem every block at once (F_front25) take turns, forever, market recorded: heat 1 = A, heat 2 = F, heat 3 = A, ...
rem Double-click it, or run `scripts\ab_front_load.bat` from the repo folder. Ctrl-C quits (and cancels resting orders).
rem Logs: logs\liability-<time>__A_default.log / __F_front25__....log; each block starts with a FRONT line.
rem NOT for the graded heats: run the plain default there.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
python scripts\review_batch.py check
echo.
echo === FRONT-LOAD A/B: ritc run liability --live --forever --record --profile alternate:A_default,F_front25 ===
echo === start this BEFORE the heat opens; only ONE bot window per account ===
echo.
ritc run liability --live --forever --record --profile alternate:A_default,F_front25
pause
