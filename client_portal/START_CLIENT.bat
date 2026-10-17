@echo off
title SMART PARKING - CLIENT SERVER
cd /d C:\Users\USER\Desktop\SmartParking\client_portal
echo Starting Client Portal...
start "" "http://127.0.0.1:5001"
"..\.venv\Scripts\python.exe" app.py
pause