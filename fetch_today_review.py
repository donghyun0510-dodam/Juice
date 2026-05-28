"""
오늘자 '증시 리뷰_YYMMDD' 시트를 열어 블로그 작성용 텍스트로 출력.

호출 패턴 (블로그 스킬에서 사용):
    python fetch_today_review.py --market kr
    python fetch_today_review.py --market us [--date 260528]

출력: stdout으로 마크다운 텍스트. /blog-kr·/blog-us 스킬이 이 출력을 입력으로 사용.

데이터 소스 (daily_review.py와 동일):
    구글 드라이브 폴더 1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv 내 '증시 리뷰_YYMMDD'
    국장: '국장' 워크시트 / 미장: '글로벌' 워크시트
"""
import argparse
import io
import sys
from datetime import datetime, timedelta

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import gspread
from googleapiclient.discovery import build

from sheet_auth import get_credentials

FOLDER_ID = "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv"


def find_sheet(creds, date_str):
    """date_str (YYMMDD) 기준 증시 리뷰 시트 검색. 없으면 None."""
    name = f"증시 리뷰_{date_str}"
    drive = build("drive", "v3", credentials=creds)
    q = (f"name='{name}' and '{FOLDER_ID}' in parents "
         "and mimeType='application/vnd.google-apps.spreadsheet' "
         "and trashed=false")
    res = drive.files().list(q=q, fields="files(id, name)").execute()
    files = res.get("files", [])
    if not files:
        return None, name
    gc = gspread.authorize(creds)
    return gc.open_by_key(files[0]["id"]), name


def _pad(row, n=10):
    return (row + [""] * n)[:n]


def parse_kr(values):
    """국장 탭 rows → 섹션별 dict."""
    out = {"news": [], "indicators": [], "asia": [], "macro": [], "sectors": []}
    section = None
    last_sector = ""
    for raw in values:
        row = _pad(raw, 10)
        a, b, c, d, e, f, g = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
        if a.startswith("1.시장 뉴스"):
            section = "news"
        elif a.startswith("2.경제지표") or a.startswith("2.경제 지표"):
            section = "indicators"
        elif a.startswith("3.아시아"):
            section = "asia"
        elif a.startswith("4.지수 및 Macro") or a.startswith("4.지수"):
            section = "macro"
        elif a.startswith("5.섹터/종목"):
            section = "sectors"
        elif a.startswith("6."):
            section = None
            continue
        if not section:
            continue

        if section == "news":
            if any([b, c, d, e]):
                out["news"].append({"topic": b, "checkpoint": c, "content": d, "note": e})
        elif section == "indicators":
            if c and "(당일 주요 지표 발표 없음)" in c:
                continue
            if any([c, d, e]):
                out["indicators"].append({"name": c, "actual": d, "expected_prev": e})
        elif section == "asia":
            if any([b, c, d, e]):
                out["asia"].append({"country": b, "name": c, "price": d, "chg": e})
        elif section == "macro":
            if any([b, c, d, e, f]):
                out["macro"].append({"category": b, "name": c, "price": d, "chg": e, "risk": f})
        elif section == "sectors":
            if b and not c and not d:
                last_sector = b
                continue
            if not c:
                continue
            sec = b or last_sector
            out["sectors"].append({
                "sector": sec, "name": c, "chg": d, "sign": e, "ticker": f, "stale": g
            })
            last_sector = sec
    return out


def parse_us(values):
    """글로벌 탭 rows → 섹션별 dict."""
    out = {"news": [], "indicators": [], "macro": [], "indices": [], "sectors": []}
    section = None
    last_sector = ""
    for raw in values:
        row = _pad(raw, 10)
        a, b, c, d, e, f = row[0], row[1], row[2], row[3], row[4], row[5]
        if a.startswith("1.시장 뉴스"):
            section = "news"
        elif a.startswith("2.경제지표") or a.startswith("2.경제 지표"):
            section = "indicators"
        elif a.startswith("3.매크로 동향"):
            section = "macro"
        elif a.startswith("4.지수 동향") or a.startswith("4.지수"):
            section = "indices"
        elif a.startswith("5.섹터/종목"):
            section = "sectors"
        elif a.startswith("6."):
            section = None
            continue
        if not section:
            continue

        if section == "news":
            if any([b, c, d, e]):
                out["news"].append({"topic": b, "checkpoint": c, "content": d, "note": e})
        elif section == "indicators":
            if c and "(전날 주요 지표 발표 없음)" in c:
                continue
            if any([c, d, e]):
                out["indicators"].append({"name": c, "actual": d, "expected_prev": e})
        elif section == "macro":
            if any([b, c, d, e, f]):
                out["macro"].append({"category": b, "name": c, "price": d, "chg": e, "risk": f})
        elif section == "indices":
            if any([c, d]):
                out["indices"].append({"name": c, "chg": d})
        elif section == "sectors":
            if b and not c and not d:
                last_sector = b
                continue
            if not c:
                continue
            sec = b or last_sector
            out["sectors"].append({
                "sector": sec, "ticker": c, "chg": d, "note": e, "sign": f
            })
            last_sector = sec
    return out


def render_kr(p, date_str):
    L = [f"# 증시 리뷰_{date_str} — 국장", ""]

    L.append("## 1. 시장 뉴스 (시트 수동 입력 영역)")
    if not p["news"]:
        L.append("(시트에 입력 없음 — 채팅에서 별도로 알려주세요)")
    else:
        for n in p["news"]:
            parts = [n["topic"], n["checkpoint"], n["content"]]
            line = " / ".join([x for x in parts if x])
            if n["note"]:
                line += f" | 비고: {n['note']}"
            L.append(f"- {line}")
    L.append("")

    L.append("## 2. 경제지표")
    if not p["indicators"]:
        L.append("(당일 주요 지표 발표 없음)")
    else:
        for x in p["indicators"]:
            L.append(f"- {x['name']}: 발표 {x['actual']} / 예상·이전 {x['expected_prev']}")
    L.append("")

    L.append("## 3. 아시아 증시")
    for x in p["asia"]:
        L.append(f"- {x['country']}\t{x['name']}\t{x['price']}\t{x['chg']}")
    L.append("")

    L.append("## 4. 지수 및 Macro")
    for x in p["macro"]:
        parts = [x["category"], x["name"], x["price"], x["chg"]]
        line = "\t".join([str(z) for z in parts if z != ""])
        if x.get("risk"):
            line += f"\t[{x['risk']}]"
        L.append(f"- {line}")
    L.append("")

    L.append("## 5. 섹터/종목 (신호 포함)")
    cur = None
    for x in p["sectors"]:
        if x["sector"] != cur:
            cur = x["sector"]
            L.append(f"\n### {cur}")
        sign_str = f"\t{x['sign']}" if x.get("sign") else ""
        stale_str = "  (전일 데이터)" if x.get("stale") else ""
        L.append(f"- {x['name']}\t{x['chg']}{sign_str}{stale_str}")
    L.append("")

    return "\n".join(L)


def render_us(p, date_str):
    L = [f"# 증시 리뷰_{date_str} — 글로벌", ""]

    L.append("## 1. 시장 뉴스 (시트 수동 입력 영역)")
    if not p["news"]:
        L.append("(시트에 입력 없음 — 채팅에서 별도로 알려주세요)")
    else:
        for n in p["news"]:
            parts = [n["topic"], n["checkpoint"], n["content"]]
            line = " / ".join([x for x in parts if x])
            if n["note"]:
                line += f" | 비고: {n['note']}"
            L.append(f"- {line}")
    L.append("")

    L.append("## 2. 경제지표")
    if not p["indicators"]:
        L.append("(전날 주요 지표 발표 없음)")
    else:
        for x in p["indicators"]:
            L.append(f"- {x['name']}: 발표 {x['actual']} / 예상·이전 {x['expected_prev']}")
    L.append("")

    L.append("## 3. 매크로 동향")
    for x in p["macro"]:
        parts = [x["category"], x["name"], x["price"], x["chg"]]
        line = "\t".join([str(z) for z in parts if z != ""])
        if x.get("risk"):
            line += f"\t[{x['risk']}]"
        L.append(f"- {line}")
    L.append("")

    L.append("## 4. 지수 동향")
    for x in p["indices"]:
        L.append(f"- {x['name']}\t{x['chg']}")
    L.append("")

    L.append("## 5. 섹터/종목 (신호 포함)")
    cur = None
    for x in p["sectors"]:
        if x["sector"] != cur:
            cur = x["sector"]
            L.append(f"\n### {cur}")
        sign_str = f"\t{x['sign']}" if x.get("sign") else ""
        note_str = f"  ({x['note']})" if x.get("note") else ""
        L.append(f"- {x['ticker']}\t{x['chg']}{sign_str}{note_str}")
    L.append("")

    return "\n".join(L)


def _auto_date():
    now = datetime.now()
    wd = now.weekday()
    if wd == 5:
        now -= timedelta(days=1)
    elif wd == 6:
        now -= timedelta(days=2)
    return now.strftime("%y%m%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--date", help="YYMMDD. 미지정 시 오늘 (주말은 직전 평일).")
    args = ap.parse_args()

    date_str = args.date or _auto_date()

    creds = get_credentials()
    sh, name = find_sheet(creds, date_str)
    if not sh:
        print(f"ERROR: '{name}' 시트를 폴더에서 찾을 수 없음.", file=sys.stderr)
        print(f"  daily_review.py가 아직 실행되지 않았거나 다른 날짜인지 확인.", file=sys.stderr)
        sys.exit(1)

    tab = "국장" if args.market == "kr" else "글로벌"
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        print(f"ERROR: '{name}' 시트에 '{tab}' 탭이 없음.", file=sys.stderr)
        sys.exit(2)

    values = ws.get_all_values()
    if args.market == "kr":
        print(render_kr(parse_kr(values), date_str))
    else:
        print(render_us(parse_us(values), date_str))


if __name__ == "__main__":
    main()
