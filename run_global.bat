@echo off
chcp 65001 >nul
cd /d "C:\Users\dongh\Desktop\주식\AI agent"
"C:\Users\dongh\AppData\Local\Programs\Python\Python314\python.exe" daily_review.py --global-only >> "%~dp0log_global.txt" 2>&1
