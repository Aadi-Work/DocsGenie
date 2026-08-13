@echo off
echo Starting YMSLI Template Hub frontend...
cd /d "%~dp0frontend"
if not exist node_modules (
  call npm install
)
call npm run dev
