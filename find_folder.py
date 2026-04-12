import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
drive = build("drive", "v3", credentials=creds)

# 서비스 계정에 공유된 폴더 찾기
results = drive.files().list(
    q="mimeType='application/vnd.google-apps.folder'",
    fields="files(id, name)"
).execute()

for f in results.get("files", []):
    print(f"  폴더: {f['name']}  |  ID: {f['id']}")
