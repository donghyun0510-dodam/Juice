"""
'투자 수익률 기록' 시트 갱신 (1회성):
- 시트2 '저축' : IRP 추가로 칼럼 시프트 → 수식 위치 재배치
- 시트3 '대출' : 2개 대출 관리용 헤더+수식 생성
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gspread
from sheet_auth import get_credentials

SHEET_ID = "1Jnm55njxzB8NxY0LJAlrum8WPMHuD-EdeMlpHMQfHrY"
gc = gspread.authorize(get_credentials())
sh = gc.open_by_key(SHEET_ID)

# ── 시트2: 저축 수식 갱신 ──
# 헤더: A=월 B=청년도약 C=일반적금 D=주택청약 E=IRP F=합계 G=입출금 H=월손익 I=월수익률 J=누적수익률
ws2 = sh.worksheet("저축")

# 기존 안내문/머지/잔존 수식 정리 (A4:T120)
ws2.unmerge_cells("A4:I4") if True else None
try:
    ws2.unmerge_cells("A4:I4")
except Exception:
    pass
ws2.batch_clear(["A4:T120"])

# 헤더 갱신 (이전엔 9열, 이제 10열)
ws2.update(values=[["월", "청년도약계좌", "일반적금", "주택청약", "IRP",
                    "합계", "입출금(추가입금/출금)", "월 손익", "월 수익률", "누적 수익률"]],
           range_name="A1:J1")

# 합계 수식: 행 2부터
ws2.update_acell("F2", "=IF(COUNTA(B2:E2)=0,\"\",SUM(B2:E2))")
ws2.update_acell("F3", "=IF(COUNTA(B3:E3)=0,\"\",SUM(B3:E3))")

# 행 3 (2026-04): 입출금만 사용자 입력. 손익/수익률 수식.
ws2.update(values=[[
    "=IF(F3=\"\",\"\",F3-F2-G3)",
    "=IF(F3=\"\",\"\",IFERROR(H3/(F2+G3),\"\"))",
    "=IF(F3=\"\",\"\",IFERROR((F3-SUM(G$2:G3))/F$2-1,\"\"))",
]], range_name="H3:J3", value_input_option="USER_ENTERED")

# 행 4~99: 사용자가 B~E + G만 채우면 F/H/I/J 자동
formulas2 = []
for r in range(4, 100):
    formulas2.append([
        "",  # A 월
        "", "", "", "",  # B,C,D,E 사용자 입력
        f"=IF(COUNTA(B{r}:E{r})=0,\"\",SUM(B{r}:E{r}))",                  # F 합계
        "",                                                                # G 입출금
        f"=IF(F{r}=\"\",\"\",F{r}-F{r-1}-G{r})",                          # H 월 손익
        f"=IF(F{r}=\"\",\"\",IFERROR(H{r}/(F{r-1}+G{r}),\"\"))",          # I 월 수익률
        f"=IF(F{r}=\"\",\"\",IFERROR((F{r}-SUM(G$2:G{r}))/F$2-1,\"\"))",  # J 누적 수익률
    ])
ws2.update(values=formulas2, range_name="A4:J99", value_input_option="USER_ENTERED")

# 포맷
ws2.format("A1:J1", {"textFormat": {"bold": True},
                     "backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.85}})
ws2.format("B2:G99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws2.format("H2:H99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0;[Red]-#,##0"}})
ws2.format("I2:J99", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
ws2.columns_auto_resize(0, 10)

print("저축 시트 갱신 완료")

# ── 시트3: 대출 ──
try:
    ws3 = sh.worksheet("대출")
    ws3.clear()
    ws3.resize(rows=120, cols=14)
except gspread.WorksheetNotFound:
    ws3 = sh.add_worksheet(title="대출", rows=120, cols=14)

# 헤더: A 월
#  B 대출1잔액  C 대출1월상환원금  D 대출1월이자
#  E 대출2잔액  F 대출2월상환원금  G 대출2월이자
#  H 총잔액  I 월 총상환  J 월 총이자  K 누적 상환  L 누적 이자
ws3.update(values=[
    ["", "대출1 (이름변경)", "", "", "대출2 (이름변경)", "", "", "합계", "", "", "누적", ""],
    ["월", "잔액", "월 상환원금", "월 이자",
            "잔액", "월 상환원금", "월 이자",
            "총 잔액", "월 총상환", "월 총이자",
            "누적 상환원금", "누적 이자"],
], range_name="A1:L2")

ws3.merge_cells("B1:D1")
ws3.merge_cells("E1:G1")
ws3.merge_cells("H1:J1")
ws3.merge_cells("K1:L1")

# 안내
ws3.update(values=[["[안내] 1행 'B1','E1' 셀에 대출 이름을 입력하세요. 매월 B/E(잔액), C/F(이번 달 원금 상환액), D/G(이번 달 이자) 만 입력하면 H~L은 자동 계산됩니다."]],
           range_name="A4")

# 수식: 행 3은 시작 월 (사용자가 잔액/상환/이자 직접 입력) → 합계도 자동
# 행 3부터 수식 깔기
formulas3 = []
for r in range(3, 100):
    if r == 3:
        cum_pay = f"=IF(COUNTA(C{r}:F{r})=0,\"\",IFERROR(C{r}+F{r},\"\"))"
        cum_int = f"=IF(COUNTA(D{r}:G{r})=0,\"\",IFERROR(D{r}+G{r},\"\"))"
    else:
        cum_pay = f"=IF(I{r}=\"\",\"\",IFERROR(K{r-1}+I{r},I{r}))"
        cum_int = f"=IF(J{r}=\"\",\"\",IFERROR(L{r-1}+J{r},J{r}))"
    formulas3.append([
        "",  # A 월
        "", "", "",  # B,C,D 대출1
        "", "", "",  # E,F,G 대출2
        f"=IF(AND(B{r}=\"\",E{r}=\"\"),\"\",IFERROR(N(B{r})+N(E{r}),\"\"))",  # H 총잔액
        f"=IF(AND(C{r}=\"\",F{r}=\"\"),\"\",IFERROR(N(C{r})+N(F{r}),\"\"))",  # I 월 총상환
        f"=IF(AND(D{r}=\"\",G{r}=\"\"),\"\",IFERROR(N(D{r})+N(G{r}),\"\"))",  # J 월 총이자
        cum_pay,  # K 누적 상환
        cum_int,  # L 누적 이자
    ])
ws3.update(values=formulas3, range_name="A3:L99", value_input_option="USER_ENTERED")

# 포맷
ws3.format("A1:L2", {"textFormat": {"bold": True},
                     "backgroundColor": {"red": 0.95, "green": 0.88, "blue": 0.85},
                     "horizontalAlignment": "CENTER"})
ws3.format("B3:L99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0;[Red]-#,##0"}})
ws3.format("A4:L4", {"textFormat": {"italic": True,
                                    "foregroundColor": {"red": 0.4, "green": 0.4, "blue": 0.4}}})
ws3.merge_cells("A4:L4")
ws3.columns_auto_resize(0, 12)

print("대출 시트 생성 완료")
print(f"\n→ https://docs.google.com/spreadsheets/d/{SHEET_ID}")
