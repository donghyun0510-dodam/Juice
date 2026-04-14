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


def _load_state() -> dict:
    if os.path.exists(ALERT_STATE_PATH):
        try:
            with open(ALERT_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    try:
        with open(ALERT_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[notifier] state 저장 실패: {e}")


def check_and_notify_macro(macro_total: float) -> None:
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
