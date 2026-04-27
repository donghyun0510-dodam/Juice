"""
시트4 '종합' 추가 — 주식/저축/대출 시트를 VLOOKUP으로 결합해 금융자산 총액 계산.
부수 작업: 대출!A3 "4" → "2026-04" 로 정규화 (cross-sheet 매칭).
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gspread
from sheet_auth import get_credentials

SHEET_ID = "1Jnm55njxzB8NxY0LJAlrum8WPMHuD-EdeMlpHMQfHrY"
gc = gspread.authorize(get_credentials())
sh = gc.open_by_key(SHEET_ID)

# ── 대출 A3 정규화 ──
ws_loan = sh.worksheet("대출")
a3 = ws_loan.acell("A3").value
if a3 == "4":
    ws_loan.update_acell("A3", "2026-04")
    print(f"대출!A3 '{a3}' → '2026-04' 로 정규화")

# ── 시트4 '종합' ──
try:
    ws4 = sh.worksheet("종합")
    ws4.clear()
    ws4.resize(rows=120, cols=12)
except gspread.WorksheetNotFound:
    ws4 = sh.add_worksheet(title="종합", rows=120, cols=12)

# 헤더: A 월 | B 주식 | C 저축 | D 자산 합계 | E 대출 | F 순자산 | G 월 변동 | H 월 변동률 | I 누적 변동률
ws4.update(values=[[
    "월", "주식 평가액", "저축 합계", "자산 합계", "대출 잔액", "순자산",
    "월 변동", "월 변동률", "누적 변동률"
]], range_name="A1:I1")

# 베이스라인 행 (2026-04)
ws4.update(values=[["2026-04"]], range_name="A2")

# 행 2~99 수식
formulas = []
for r in range(2, 100):
    if r == 2:
        # 베이스라인 — 변동/변동률 없음
        delta = ""
        delta_pct = ""
        cum_pct = ""
    else:
        delta = f"=IF(F{r}=\"\",\"\",IFERROR(F{r}-F{r-1},\"\"))"
        delta_pct = f"=IF(F{r}=\"\",\"\",IFERROR(G{r}/F{r-1},\"\"))"
        cum_pct = f"=IF(F{r}=\"\",\"\",IFERROR(F{r}/F$2-1,\"\"))"

    formulas.append([
        # B 주식 평가액 — 주식 시트에서 매칭
        f"=IFERROR(VLOOKUP($A{r},주식!$A:$B,2,FALSE),\"\")",
        # C 저축 합계 — 저축 시트 F열
        f"=IFERROR(VLOOKUP($A{r},저축!$A:$F,6,FALSE),\"\")",
        # D 자산 합계 = B + C
        f"=IF(AND(B{r}=\"\",C{r}=\"\"),\"\",N(B{r})+N(C{r}))",
        # E 대출 잔액 — 대출 시트 H열 (총 잔액)
        f"=IFERROR(VLOOKUP($A{r},대출!$A:$H,8,FALSE),\"\")",
        # F 순자산 = D - E
        f"=IF(D{r}=\"\",\"\",D{r}-N(E{r}))",
        delta,
        delta_pct,
        cum_pct,
    ])
ws4.update(values=formulas, range_name="B2:I99", value_input_option="USER_ENTERED")

# 포맷
ws4.format("A1:I1", {"textFormat": {"bold": True},
                     "backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85},
                     "horizontalAlignment": "CENTER"})
ws4.format("B2:F99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws4.format("G2:G99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0;[Red]-#,##0"}})
ws4.format("H2:I99", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
ws4.format("F2:F99", {"textFormat": {"bold": True},
                      "backgroundColor": {"red": 0.97, "green": 0.97, "blue": 0.85}})
ws4.columns_auto_resize(0, 9)

# 안내
ws4.update(values=[[
    "[안내] A열 '월' 만 'YYYY-MM' 포맷(예: 2026-05)으로 추가하면 주식/저축/대출 시트에서 자동으로 값을 끌어와 순자산·변동률을 계산합니다."
]], range_name="A101")
ws4.merge_cells("A101:I101")
ws4.format("A101:I101", {"textFormat": {"italic": True,
                                        "foregroundColor": {"red": 0.4, "green": 0.4, "blue": 0.4}}})

print("종합 시트 생성 완료")
print(f"\n→ https://docs.google.com/spreadsheets/d/{SHEET_ID}")
