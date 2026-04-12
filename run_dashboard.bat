@echo off
chcp 65001 >nul
cd /d "C:\Users\dongh\Desktop\주식\AI agent"
start /B "" "C:\Users\dongh\AppData\Local\Programs\Python\Python314\python.exe" -m streamlit run market_dashboard.py --server.headless true --server.port 8501 >> "%~dp0log_dashboard.txt" 2>&1
