@echo off
REM MedRAG local start (Windows)
set PYTHONPATH=E:\MedRAG\backend
cd /d E:\MedRAG
echo Starting API on http://127.0.0.1:8000
start "MedRAG API" cmd /k python -m app.cli serve --host 127.0.0.1 --port 8000
echo Starting UI on http://localhost:5173
cd frontend
start "MedRAG UI" cmd /k npm run dev
echo.
echo API  http://127.0.0.1:8000/docs
echo UI   http://localhost:5173
