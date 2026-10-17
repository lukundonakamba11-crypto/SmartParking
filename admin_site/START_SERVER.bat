@echo off
title SMART PARKING - AdMIN SERVER
cd /d C:\Users\USER\Desktop\SmartParking\admin_site
echo Starting Admin Server...
start "" "http://127.0.0.1:5000/admin/login"
"..\.venv\Scripts\python.exe" app.py
pause