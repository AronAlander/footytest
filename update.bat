@echo off
rem Double-click to update the dashboard: fetch, rebuild, publish to GitHub Pages.
cd /d "%~dp0"
python update.py --push
pause
