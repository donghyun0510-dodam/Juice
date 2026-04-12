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

# 서비스 계정의 용량 확인
about = drive.about().get(fields="storageQuota").execute()
quota = about["storageQuota"]
print(f"사용량: {int(quota['usage']) / 1024 / 1024:.1f} MB")
print(f"한도: {int(quota.get('limit', 0)) / 1024 / 1024 / 1024:.1f} GB")

# 서비스 계정이 소유한 파일 목록
results = drive.files().list(
    q="'me' in owners",
    fields="files(id, name, size, mimeType)",
    pageSize=100
).execute()

files = results.get("files", [])
print(f"\n서비스 계정 소유 파일 ({len(files)}개):")
for f in files:
    size = int(f.get("size", 0)) / 1024
    print(f"  {f['name']} ({size:.1f} KB) - {f['id']}")
