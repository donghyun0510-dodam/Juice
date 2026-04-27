"""
IRP를 운용자산(주식 쪽)으로 재분류.
- 입력 시트(주식/저축)는 그대로 유지 (계좌 분리된 현실 반영)
- 종합 시트 칼럼 정의만 변경:
    B 운용자산(KB+IRP) = 주식 + IRP
    C 예적금          = 저축 합계 - IRP
- 목표 계산기 라벨/가정값도 동기화
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import gspread
from sheet_auth import get_credentials

SHEET_ID = "1Jnm55njxzB8NxY0LJAlrum8WPMHuD-EdeMlpHMQfHrY"
gc = gspread.authorize(get_credentials())
sh = gc.open_by_key(SHEET_ID)

# ── 1) 종합 시트 ──
ws = sh.worksheet("종합")
ws.update(values=[["월", "운용자산(KB+IRP)", "예적금", "자산 합계", "대출 잔액", "순자산",
                   "월 변동", "월 변동률", "누적 변동률"]],
          range_name="A1:I1")

# B (운용자산) = 주식!B + 저축!E(IRP)
# C (예적금)   = 저축!F(합계) - 저축!E(IRP)
b_formulas = []
c_formulas = []
for r in range(2, 100):
    b_formulas.append([
        f'=IFERROR(VLOOKUP($A{r},주식!$A:$B,2,FALSE),0)'
        f'+IFERROR(VLOOKUP($A{r},저축!$A:$E,5,FALSE),0)'
    ])
    c_formulas.append([
        f'=IFERROR(VLOOKUP($A{r},저축!$A:$F,6,FALSE),0)'
        f'-IFERROR(VLOOKUP($A{r},저축!$A:$E,5,FALSE),0)'
    ])
ws.update(values=b_formulas, range_name="B2:B99", value_input_option="USER_ENTERED")
ws.update(values=c_formulas, range_name="C2:C99", value_input_option="USER_ENTERED")
print("종합 B/C 칼럼 재정의 완료")

# ── 2) 목표 계산기 라벨/가정값 ──
gc_ws = sh.worksheet("목표 계산기")
gc_ws.update(values=[
    ["기준일"],
    ["운용자산 (KB+IRP)"],
    ["예적금"],
], range_name="A2:A4")

# 저축 월 증가분: 1,513,504 → 820,000 (자동이체만)
gc_ws.update(values=[[820000]], range_name="B9", value_input_option="RAW")
print("목표 계산기 라벨/가정값 동기화 (B9: 820,000)")

# ── 검증 ──
import time; time.sleep(1)
print("\n─── 종합 시트 결과 ───")
for r in ws.get("A1:F3", value_render_option="FORMATTED_VALUE"):
    print(" ", r)

print("\n─── 목표 계산기 시나리오 A (4억, 2029-04) ───")
for row in range(2, 21):
    a = gc_ws.acell(f"A{row}").value or ""
    b = gc_ws.acell(f"B{row}", value_render_option="FORMATTED_VALUE").value or ""
    print(f"  {a:30s}  {b}")

print("\n─── 시나리오 비교표 ───")
for row in gc_ws.get("D2:I8", value_render_option="FORMATTED_VALUE"):
    print("  " + " | ".join(f"{c:>14s}" for c in row))
