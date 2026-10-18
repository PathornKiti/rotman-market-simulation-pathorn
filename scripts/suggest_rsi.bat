@echo off
rem MANUAL TRADING with rsi_bot's logic (Windows, READ-ONLY: nothing is ever sent to RIT; you type the trades yourself).
rem Shows per tender ACCEPT / DECLINE (incl. "touch edge" = price beats the market) and per stock the order to type,
rem or "TREND HOLD: keep it, no order" while the trend runs our way. Do NOT run a bot window at the same time.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
python -m ritc suggest --set strategy.accept_touch_edge=0.0 --set execution.trend_hold_ticks=10 --set execution.trend_hold_min_start_crowded=0.05
pause
