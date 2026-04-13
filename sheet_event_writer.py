"""
구글 시트 H열 이벤트 기록 모듈 (data-collector 담당 영역).

장중 신호 변화 감지 시 당일 시트의 해당 종목 행 H열에 이벤트를 누적 기록한다.
daily_review.py와 OAuth2 토큰 공유 (token.pickle).

컬럼 레이아웃:
    A 단계 | B 주제 | C 체크포인트 | D 내용 | E 비고 | F 위험/신호 | G 특징주 섹터 | H 장중 이벤트

이벤트 포맷 (signal-judge 정의):
    HH:MM <before>→<after>
    여러 이벤트는 줄바꿈으로 누적.
"""

import os
from datetime import datetime
import gspread
from sheet_auth import get_credentials

FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")

EVENT_COL = "H"  # 장중 이벤트 컬럼


def _get_client():
    return gspread.authorize(get_credentials())


def _find_today_sheet(gc, market: str):
    """오늘 날짜(YYMMDD)의 시트 워크시트 반환. 없으면 None."""
    today = datetime.now().strftime("%y%m%d")
    try:
        sheet_name = f"증시 리뷰_{today}"
        sh = gc.open(sheet_name, folder_id=FOLDER_ID)
        ws_title = "글로벌" if market == "global" else "국장"
        return sh.worksheet(ws_title)
    except Exception:
        return None


def _find_ticker_row(ws, search_key: str) -> int | None:
    """C열에서 티커·종목명 매칭되는 행 번호 반환 (1-based). 없으면 None."""
    try:
        col_values = ws.col_values(3)  # C열
        for idx, val in enumerate(col_values, start=1):
            if val and (search_key in val or val == search_key):
                return idx
    except Exception:
        pass
    return None


def append_event(ws, row: int, event_text: str) -> None:
    """H열에 이벤트 누적 기록 (줄바꿈 구분)."""
    cell_addr = f"{EVENT_COL}{row}"
    try:
        current = ws.acell(cell_addr).value or ""
    except Exception:
        current = ""
    combined = (current + "\n" + event_text).strip() if current else event_text
    ws.update(range_name=cell_addr, values=[[combined]])


def record_intraday_changes(changes: dict, market: str = "global") -> int:
    """장중 변화 감지된 종목들을 구글 시트 H열에 기록.

    Args:
        changes: {ticker: (before_tag, after_tag)} — market_common.detect_changes 결과
        market: "global" 또는 "korea"

    Returns:
        실제 기록된 이벤트 수
    """
    if not changes:
        return 0
    try:
        gc = _get_client()
        ws = _find_today_sheet(gc, market)
        if ws is None:
            return 0
    except Exception as e:
        print(f"시트 접근 실패: {e}")
        return 0

    now_str = datetime.now().strftime("%H:%M")
    count = 0
    for ticker, (before, after) in changes.items():
        search_key = ticker.replace(".KS", "").replace(".KQ", "")
        row = _find_ticker_row(ws, search_key) or _find_ticker_row(ws, ticker)
        if row is None:
            continue
        event = f"{now_str} {before}→{after}"
        try:
            append_event(ws, row, event)
            count += 1
        except Exception as e:
            print(f"{ticker} H열 기록 실패: {e}")
    return count
