"""
구글 시트/드라이브 공통 인증 헬퍼.

우선순위:
1. 환경변수 GOOGLE_SA_JSON (서비스 계정 키 JSON 문자열) — GitHub Actions/클라우드 용
2. 로컬 파일 sa_credentials.json — 로컬 SA 키 사용 시
3. OAuth token.pickle 폴백 — 기존 개인 계정 토큰 (로컬 개발용)
"""
import json
import os
import pickle

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SA_FILE = os.path.join(SCRIPT_DIR, "sa_credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.pickle")
CLIENT_SECRET_FILE = os.path.join(SCRIPT_DIR, "client_secret.json")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_credentials():
    sa_env = os.environ.get("GOOGLE_SA_JSON")
    if sa_env:
        from google.oauth2.service_account import Credentials as SACredentials
        info = json.loads(sa_env)
        return SACredentials.from_service_account_info(info, scopes=SCOPES)

    if os.path.exists(SA_FILE):
        from google.oauth2.service_account import Credentials as SACredentials
        return SACredentials.from_service_account_file(SA_FILE, scopes=SCOPES)

    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError("SA 키(sa_credentials.json/GOOGLE_SA_JSON) 또는 OAuth client_secret.json 없음")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return creds
