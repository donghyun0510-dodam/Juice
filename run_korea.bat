@echo off
chcp 65001 >nul
cd /d "C:\Users\dongh\Desktop\주식\AI agent"
"C:\Users\dongh\AppData\Local\Programs\Python\Python314\python.exe" daily_review.py --korea-only >> "%~dp0log_korea.txt" 2>&1
