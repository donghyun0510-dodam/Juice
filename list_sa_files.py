"""서비스 계정(SA) 소유 파일 전수 조회/선택 삭제.

사용법:
  1) 먼저 그냥 실행하면 파일 목록만 출력 (삭제 안 함)
     python list_sa_files.py

  2) 이름 패턴으로 걸러서 삭제하려면 DELETE_PATTERN 환경변수 사용
     DELETE_PATTERN="증시 리뷰_2601" python list_sa_files.py
     → 이름에 "증시 리뷰_2601" 포함된 것만 삭제

  3) 전부 삭제 (위험!):
     DELETE_ALL=1 python list_sa_files.py

출력:
  - 파일 개수 + 각 파일의 이름/ID/크기(가능 시)
  - 삭제 모드일 때만 실제 삭제 수행
"""
import os
import sys

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SA_PATH = os.path.join(BASE_DIR, "sa_credentials.json")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

if not os.path.exists(SA_PATH):
    print(f"[ERROR] {SA_PATH} 없음")
    sys.exit(1)

creds = Credentials.from_service_account_file(SA_PATH, scopes=SCOPES)
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

delete_pattern = os.environ.get("DELETE_PATTERN", "").strip()
delete_all = os.environ.get("DELETE_ALL") == "1"

# SA 저장소 사용량 조회
try:
    about = drive.about().get(fields="storageQuota, user").execute()
    quota = about.get("storageQuota", {})
    user = about.get("user", {})
    used = int(quota.get("usage", 0))
    limit = int(quota.get("limit", 0)) if quota.get("limit") else 0
    print(f"[about] SA email: {user.get('emailAddress')}")
    print(f"[about] 사용량: {used/1024/1024:.2f}MB / {limit/1024/1024:.2f}MB" if limit
          else f"[about] 사용량: {used/1024/1024:.2f}MB (limit 없음/unlimited)")
except Exception as e:
    print(f"[about] 조회 실패: {e}")

# SA가 접근 가능한 (소유 또는 공유받은) 모든 파일
all_files = []
page_token = None
while True:
    resp = drive.files().list(
        q="trashed=false",
        fields="nextPageToken, files(id, name, mimeType, size, owners, createdTime, modifiedTime)",
        pageSize=1000,
        pageToken=page_token,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute()
    all_files.extend(resp.get("files", []))
    page_token = resp.get("nextPageToken")
    if not page_token:
        break

# SA 이메일 가져오기
sa_email = user.get("emailAddress") if "user" in locals() else ""

# SA가 소유한 것만 필터
all_files = [f for f in all_files if any(o.get("emailAddress") == sa_email for o in f.get("owners", []))]

print(f"\n=== SA 소유 파일 총 {len(all_files)}개 ===\n")

# 크기 순 정렬 (큰 것부터)
def _size(f):
    try:
        return int(f.get("size") or 0)
    except Exception:
        return 0

all_files.sort(key=_size, reverse=True)

for f in all_files:
    size = _size(f)
    size_str = f"{size/1024/1024:.2f}MB" if size else "-"
    mime = f.get("mimeType", "").split(".")[-1]
    print(f"  [{mime:14s}] {size_str:>10s}  {f['name']}  (ID: {f['id']}, created: {f.get('createdTime','')[:10]})")

if not all_files:
    print("(SA 소유 파일 없음)")
    sys.exit(0)

# 삭제 로직
if not (delete_all or delete_pattern):
    print("\n[info] 삭제 안 함. 삭제하려면:")
    print('  DELETE_PATTERN="증시 리뷰_2601" python list_sa_files.py')
    print("  또는 DELETE_ALL=1 python list_sa_files.py")
    sys.exit(0)

to_delete = all_files if delete_all else [f for f in all_files if delete_pattern in f["name"]]

if not to_delete:
    print(f"\n[info] 패턴 '{delete_pattern}'에 매칭되는 파일 없음")
    sys.exit(0)

print(f"\n=== 삭제 대상 {len(to_delete)}개 ===")
for f in to_delete:
    print(f"  - {f['name']}")

confirm = input(f"\n정말 위 {len(to_delete)}개를 삭제할까요? (yes 입력): ").strip()
if confirm.lower() != "yes":
    print("취소됨")
    sys.exit(0)

success = 0
fail = 0
for f in to_delete:
    try:
        drive.files().delete(fileId=f["id"]).execute()
        print(f"  삭제: {f['name']}")
        success += 1
    except Exception as e:
        print(f"  실패: {f['name']} — {e}")
        fail += 1

print(f"\n완료: 성공 {success} / 실패 {fail}")
