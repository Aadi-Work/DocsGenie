@echo off
echo Starting YMSLI Template Hub backend (Bedrock + S3)...
cd /d "%~dp0backend"
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
python -m app.bootstrap
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
