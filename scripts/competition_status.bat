@echo off
rem COMPETITION SCOREBOARD (Windows, read-only): every finished heat since today 12:00 + the switch rule.
rem Run it in a SECOND window between heats; it never talks to RIT. Optional: scripts\competition_status.bat 20261018-130000
cd /d "%~dp0.."
call .venv\Scripts\activate.bat || (echo No .venv - see docs\WINDOWS_SETUP.md section 2 & pause & exit /b 1)
if "%~1"=="" (python scripts\competition_status.py logs) else (python scripts\competition_status.py logs --since %1)
pause
