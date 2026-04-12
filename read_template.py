import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gspread
from google.oauth2.service_account import Credentials

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
gc = gspread.authorize(creds)

# 가장 최신 파일 열기
sh = gc.open_by_key("1gKA6TLkhSpA6-YRdHYfmXvlpMoCKJueNqbF5bFh5rFU")

# 모든 워크시트 확인
for ws in sh.worksheets():
    print(f"\n=== 시트: {ws.title} (행:{ws.row_count}, 열:{ws.col_count}) ===")
    all_values = ws.get_all_values()
    for i, row in enumerate(all_values):
        print(f"  행{i+1}: {row}")
