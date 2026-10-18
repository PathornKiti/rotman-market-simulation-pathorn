@echo off
rem ROTATION (Windows): A_default, rsi_bot, rsi_ml, crowd_model take turns, forever, market recorded:
rem   heat 1 = A_default    the default bot (ADAPTIVE MF)
rem   heat 2 = rsi_bot      the colleague's logic: accept every fixed tender priced better than the market, HOLD each
rem                         block while the trend runs our way (when crowded, start only on a clear trend >= $0.05),
rem                         unwind when it turns (logs TOUCH ACCEPT / TREND HOLD)
rem   heat 3 = rsi_ml       rsi_bot with the ML price model as the trend signal (logs TREND HOLD ... ML next 10 ticks)
rem   heat 4 = crowd_model  the default + the tender model while the crowd alarm is on (logs TENDER MODEL)
rem   heat 5 = A_default, ...   counted from THIS session, so old logs\ do not matter.
rem Double-click it, or run `scripts\rotate_default_rsi_crowd.bat` from the repo folder. Ctrl-C quits (and cancels resting orders).
rem Logs: logs\liability-<time>__<profile>__....log (read these) + logs\data\*.jsonl (analysis tools).
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
python scripts\review_batch.py check
echo.
echo === ROTATION: ritc run liability --live --forever --record --profile alternate:A_default,rsi_bot,rsi_ml,crowd_model ===
echo === start this BEFORE the heat opens; only ONE bot window per account ===
echo.
ritc run liability --live --forever --record --profile alternate:A_default,rsi_bot,rsi_ml,crowd_model
pause
