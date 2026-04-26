"""롱돌이 이메일 알림 모듈 — Gmail SMTP로 본인에게 발송."""
import os
import json
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
ALERT_STATE_PATH = os.path.join(BASE_DIR, "macro_alert_state.json")

GMAIL_ADDR = "donghyun0510@gmail.com"

# 정책 파라미터
DELTA_THRESHOLD = 10.0          # macro_total 변화량 임계
HYSTERESIS = 3.0                # 등급 경계 완충점수 (±3)
COOLDOWN_MIN = 30               # 동일 방향 알림 쿨다운 (분)
GRADE_ORDER = ["안정", "주의", "위험", "고위험"]

# 성과 기록 시트
TIMESERIES_SHEET_NAME = "스카우터_매크로_타임시리즈"
PERF_FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")
PERF_HEADERS = ["날짜", "T-RISK", "FX-RISK", "C-RISK", "VIX점수", "매크로종합",
                "매크로종합 변동", "S&P500 종가", "S&P500 변동(%)", "구분"]
TIMESERIES_INTERVAL_MIN = 1320  # 22h — 일 1회 (KST 05:00) 가드
STATE_SHEET_NAME = "스카우터_알림상태"


def _load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def send_email(subject: str, body: str) -> bool:
    _load_env()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not pw:
        print("[notifier] GMAIL_APP_PASSWORD 미설정 — 알림 스킵")
        return False
    msg = EmailMessage()
    msg["From"] = GMAIL_ADDR
    msg["To"] = GMAIL_ADDR
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=15) as s:
            s.login(GMAIL_ADDR, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[notifier] 발송 실패: {e}")
        return False


def _grade_with_hysteresis(score: float, prev_grade: str | None) -> str:
    """히스테리시스 적용 등급: 경계 ±HYSTERESIS 내에선 이전 등급 유지."""
    base_thresholds = [25.0, 50.0, 75.0]
    if prev_grade is None:
        # 최초엔 일반 경계
        for th, g in zip(base_thresholds, GRADE_ORDER[:-1]):
            if score <= th:
                return g
        return GRADE_ORDER[-1]

    prev_idx = GRADE_ORDER.index(prev_grade) if prev_grade in GRADE_ORDER else 0
    # 상향 전환: 상위 경계 + H 초과해야
    # 하향 전환: 하위 경계 - H 미만이어야
    new_idx = prev_idx
    # 상향
    while new_idx < 3 and score > base_thresholds[new_idx] + HYSTERESIS:
        new_idx += 1
    # 하향
    while new_idx > 0 and score < base_thresholds[new_idx - 1] - HYSTERESIS:
        new_idx -= 1
    return GRADE_ORDER[new_idx]


def _get_state_ws():
    """스카우터_알림상태 시트의 sheet1 워크시트 반환 (없으면 None)."""
    try:
        import gspread
        from sheet_auth import get_credentials
        from googleapiclient.discovery import build
        creds = get_credentials()
        gc = gspread.authorize(creds)
        drive = build("drive", "v3", credentials=creds)
        q = (f"name='{STATE_SHEET_NAME}' and '{PERF_FOLDER_ID}' in parents "
             "and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
        res = drive.files().list(q=q, fields="files(id, name)").execute()
        files = res.get("files", [])
        if not files:
            return None
        return gc.open_by_key(files[0]["id"]).sheet1
    except Exception as e:
        print(f"[notifier] state 시트 접근 실패: {e}")
        return None


def _load_state() -> dict:
    # 1순위: 구글 시트 A1에 저장된 JSON
    ws = _get_state_ws()
    if ws is not None:
        try:
            cell = ws.acell("A1").value
            if cell:
                return json.loads(cell)
        except Exception as e:
            print(f"[notifier] state 시트 로드 실패, 로컬 폴백: {e}")
    # 2순위: 로컬 파일
    if os.path.exists(ALERT_STATE_PATH):
        try:
            with open(ALERT_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    # 로컬 파일 먼저 (항상 시도)
    try:
        with open(ALERT_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[notifier] state 로컬 저장 실패: {e}")
    # 구글 시트에도 동기화 (가용 시)
    ws = _get_state_ws()
    if ws is not None:
        try:
            ws.update(range_name="A1", values=[[json.dumps(state, ensure_ascii=False)]])
        except Exception as e:
            print(f"[notifier] state 시트 저장 실패: {e}")


def _append_row_to_sheet(sheet_name: str, scores: dict) -> bool:
    """주어진 시트명에 현재 매크로 메트릭 + S&P500 한 행 append. 성공 시 True."""
    try:
        import gspread
        import yfinance as yf
        # 클라우드 우선: SA(GOOGLE_SA_JSON) → 로컬 OAuth 토큰 순
        # sheet_auth.get_credentials()가 이미 SA env → SA 파일 → OAuth 순으로 처리
        from sheet_auth import get_credentials
        creds = get_credentials()
        gc = gspread.authorize(creds)
        from googleapiclient.discovery import build
        drive = build("drive", "v3", credentials=creds)

        # 미국 현물장 개장 여부 — 폐장 중엔 E-mini S&P500 선물(ES=F) 사용
        # (KST 월~금 22:30~05:00이 미국 현물 거래시간)
        from datetime import timezone, timedelta as _td, time as _dtime
        _kst = datetime.now(timezone(_td(hours=9)))
        _t, _wd = _kst.time(), _kst.weekday()
        us_open = (_t >= _dtime(22, 30) and _wd <= 4) or (_t <= _dtime(5, 0) and 1 <= _wd <= 5)
        use_futures = not us_open
        sp500_ticker = "ES=F" if use_futures else "^GSPC"
        sp500_close = None
        try:
            hist = yf.Ticker(sp500_ticker).history(period="2d")
            if len(hist) >= 1:
                sp500_close = float(hist["Close"].iloc[-1])
        except Exception:
            pass
        # sp500_diff 는 아래에서 '직전 시트 행'의 종가 대비로 계산 (상관분석용)
        sp500_close_label = round(sp500_close, 2) if sp500_close is not None else ""
        sp500_kind_label = "선물" if (sp500_close is not None and use_futures) else ""

        q = (f"name='{sheet_name}' and '{PERF_FOLDER_ID}' in parents "
             "and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
        res = drive.files().list(q=q, fields="files(id, name)").execute()
        files = res.get("files", [])
        if not files:
            # SA는 파일 생성 권한이 없음 — 시트를 사전에 개인 계정으로 만들고 SA에 공유해야 함
            print(f"[notifier] {sheet_name} 시트가 폴더에 없음 — 사전 생성 필요")
            return False
        if len(files) > 1:
            print(f"[notifier] ⚠️ {sheet_name} 중복 {len(files)}개 발견: {[f['id'] for f in files]}")
        print(f"[notifier] {sheet_name} → file_id={files[0]['id']}")
        sh = gc.open_by_key(files[0]["id"])
        ws = sh.sheet1
        print(f"[notifier] → worksheet='{ws.title}' id={ws.id}")
        existing = ws.get_all_values()
        if not existing or existing[0] != PERF_HEADERS:
            ws.update(range_name="A1", values=[PERF_HEADERS])

        # 직전 로그 행의 S&P500 종가 대비 변동(%) — 매크로종합과의 상관분석용
        # 지수 레벨 드리프트에 불변이도록 pt가 아닌 %로 기록
        # 컬럼 위치: 매크로종합=F(idx 5), 매크로종합변동=G(idx 6), SP500종가=H(idx 7)
        import re as _re
        sp500_diff = None
        macro_delta = None
        prev_row = existing[-1] if len(existing) >= 2 else None
        if sp500_close is not None and prev_row is not None and len(prev_row) >= 8:
            m = _re.search(r"-?\d+(?:\.\d+)?", str(prev_row[7]).replace(",", ""))
            if m:
                try:
                    prev_close_row = float(m.group())
                    if prev_close_row != 0:
                        sp500_diff = (sp500_close - prev_close_row) / prev_close_row * 100
                except Exception:
                    pass
        if prev_row is not None and len(prev_row) >= 6:
            m = _re.search(r"-?\d+(?:\.\d+)?", str(prev_row[5]).replace(",", ""))
            cur_total = scores.get("macro_total")
            if m and cur_total is not None:
                try:
                    macro_delta = float(cur_total) - float(m.group())
                except Exception:
                    pass

        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        ts_label = now.strftime("%Y-%m-%d %H:%M")

        def _r(v, n=1):
            try:
                return round(float(v), n)
            except Exception:
                return ""

        row = [
            ts_label,
            _r(scores.get("t_risk")),
            _r(scores.get("fx_risk")),
            _r(scores.get("c_risk")),
            _r(scores.get("vix_score")),
            _r(scores.get("macro_total")),
            _r(macro_delta, 1),
            sp500_close_label,
            _r(sp500_diff, 2),
            sp500_kind_label,
        ]
        ws.append_row(row)
        print(f"[notifier] {sheet_name} 기록 추가: {ts_label}")
        return True
    except Exception as e:
        print(f"[notifier] {sheet_name} 기록 실패: {e}")
        return False


def _log_if_due(scores: dict, sheet_name: str, state_key: str) -> None:
    if not scores:
        print(f"[notifier] {sheet_name}: scores 비어있음 — 스킵", flush=True)
        return
    force = os.environ.get("FORCE_APPEND") == "1"
    state = _load_state()
    last_ts = state.get(state_key)
    now = datetime.now()
    if last_ts and not force:
        try:
            elapsed = (now - datetime.fromisoformat(last_ts)).total_seconds() / 60
            if elapsed < TIMESERIES_INTERVAL_MIN:
                print(f"[notifier] {sheet_name}: {TIMESERIES_INTERVAL_MIN}분 가드 ({elapsed:.1f}분 경과) — 스킵", flush=True)
                return
            print(f"[notifier] {sheet_name}: 가드 통과 ({elapsed:.1f}분 경과) — append 시도", flush=True)
        except Exception as e:
            print(f"[notifier] {sheet_name}: last_ts 파싱 실패 {e} — 그냥 append 시도", flush=True)
    else:
        reason = "FORCE_APPEND=1" if force else "최초 기록"
        print(f"[notifier] {sheet_name}: {reason} — append 시도", flush=True)
    if _append_row_to_sheet(sheet_name, scores):
        state[state_key] = now.isoformat()
        _save_state(state)


def log_timeseries_if_due(scores: dict) -> None:
    """매일 1회(KST 05:00, 미국 종가 직후) 타임시리즈 시트에 기록."""
    _log_if_due(scores, TIMESERIES_SHEET_NAME, "last_timeseries_ts")


def check_and_notify_macro(macro_total: float, scores: dict | None = None) -> None:
    """market_dashboard.py에서 매크로 스냅샷 저장 직후 호출."""
    if macro_total is None:
        return
    now = datetime.now()
    state = _load_state()
    prev_grade = state.get("last_grade")
    prev_total = state.get("last_total")
    last_alert_ts = state.get("last_alert_ts")

    new_grade = _grade_with_hysteresis(macro_total, prev_grade)

    grade_changed = (prev_grade is not None and new_grade != prev_grade)
    delta_trigger = (
        prev_total is not None
        and abs(macro_total - prev_total) >= DELTA_THRESHOLD
    )

    # 쿨다운: 등급 악화는 무시, 그 외엔 적용
    in_cooldown = False
    if last_alert_ts:
        try:
            last_dt = datetime.fromisoformat(last_alert_ts)
            in_cooldown = (now - last_dt) < timedelta(minutes=COOLDOWN_MIN)
        except Exception:
            in_cooldown = False

    worsening = False
    if grade_changed and prev_grade in GRADE_ORDER and new_grade in GRADE_ORDER:
        worsening = GRADE_ORDER.index(new_grade) > GRADE_ORDER.index(prev_grade)

    should_notify = False
    subject = ""
    body_lines = []

    if grade_changed:
        if worsening or not in_cooldown:
            should_notify = True
            arrow = "⬆️" if worsening else "⬇️"
            subject = f"[롱돌이] 매크로 등급 {prev_grade}→{new_grade} {arrow} (점수 {macro_total:.1f})"
            body_lines.append(f"매크로 등급이 {prev_grade}에서 {new_grade}로 전환되었습니다.")
            body_lines.append(f"현재 점수: {macro_total:.2f}")
    elif delta_trigger and not in_cooldown:
        should_notify = True
        diff = macro_total - prev_total
        sign = "+" if diff >= 0 else ""
        subject = f"[롱돌이] 매크로 점수 급변 {prev_total:.1f} → {macro_total:.1f} ({sign}{diff:.1f})"
        body_lines.append(f"매크로 종합 점수가 {DELTA_THRESHOLD}점 이상 변화했습니다.")
        body_lines.append(f"이전 기준점: {prev_total:.2f} → 현재: {macro_total:.2f} ({sign}{diff:.2f})")
        body_lines.append(f"현재 등급: {new_grade}")

    if should_notify:
        body_lines.append(f"\n시각: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        ok = send_email(subject, "\n".join(body_lines))
        if ok:
            state["last_alert_ts"] = now.isoformat()
            state["last_total"] = macro_total
            state["last_grade"] = new_grade
            _save_state(state)
            return

    # 알림 안 보냈어도 상태는 유지 (최초 진입 시 grade만 기록)
    if prev_grade is None:
        state["last_grade"] = new_grade
        state["last_total"] = macro_total
        _save_state(state)
