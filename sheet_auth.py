"""
구글 시트/드라이브 공통 인증 헬퍼.

우선순위 (파일 생성이 필요한 SA의 Drive quota 이슈 때문에 OAuth 우선):
1. 환경변수 GOOGLE_OAUTH_TOKEN_B64 (token.pickle의 base64) — GH Actions용
2. 로컬 파일 token.pickle — 기존 개인 계정 토큰
3. 환경변수 GOOGLE_SA_JSON (서비스 계정 키 JSON) — SA 폴백
4. 로컬 파일 sa_credentials.json — 로컬 SA 키
5. client_secret.json → 브라우저 로그인 (최초 셋업용)
"""
import base64
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


def _refresh_and_return(creds):
    from google.auth.transport.requests import Request
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def get_credentials():
    # 1) 환경변수 OAuth 토큰 (GH Actions 등)
    tok_b64 = os.environ.get("GOOGLE_OAUTH_TOKEN_B64")
    if tok_b64:
        # 공백·줄바꿈 제거 (secret 복붙 시 줄 접힘 대비)
        tok_b64 = "".join(tok_b64.split())
        creds = pickle.loads(base64.b64decode(tok_b64))
        return _refresh_and_return(creds)

    # 2) 로컬 token.pickle
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
        creds = _refresh_and_return(creds)
        if creds.valid:
            # 토큰 갱신되었으면 파일에 재저장
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)
            return creds

    # 3) SA JSON env
    sa_env = os.environ.get("GOOGLE_SA_JSON")
    if sa_env:
        from google.oauth2.service_account import Credentials as SACredentials
        info = json.loads(sa_env)
        return SACredentials.from_service_account_info(info, scopes=SCOPES)

    # 4) SA 로컬 파일
    if os.path.exists(SA_FILE):
        from google.oauth2.service_account import Credentials as SACredentials
        return SACredentials.from_service_account_file(SA_FILE, scopes=SCOPES)

    # 5) 브라우저 로그인 폴백 (최초 셋업)
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CLIENT_SECRET_FILE):
        raise FileNotFoundError("OAuth 토큰/SA 키/client_secret.json 모두 없음")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)
    return creds
