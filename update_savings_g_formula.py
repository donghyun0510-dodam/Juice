"""
저축 시트 G열(입출금) 자동 계산 — IRP 제외 예적금(B/C/D) 증액분.

원리: G_N = SUM(B_N:D_N) - SUM(B_{N-1}:D_{N-1})
  → 청년도약/일반적금/주청 증액 = 사용자 입금분(자동이체+소액이자)으로 간주
  → IRP(E)는 운용형이라 증감이 손익에 반영돼야 하므로 G에 포함하지 않음
  → H_N (월 손익) = F_N - F_{N-1} - G_N = 결과적으로 IRP 운용손익만 깔끔히 분리
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import gspread
from sheet_auth import get_credentials

SHEET_ID = "1Jnm55njxzB8NxY0LJAlrum8WPMHuD-EdeMlpHMQfHrY"
gc = gspread.authorize(get_credentials())
ws = gc.open_by_key(SHEET_ID).worksheet("저축")

# 베이스라인 행(2)은 0
ws.update(values=[[0]], range_name="G2", value_input_option="RAW")

# 행 3~99: 직전 행 대비 B~D 증감으로 자동 계산
formulas = []
for r in range(3, 100):
    f = (f"=IF(COUNTA(B{r}:D{r})=0,\"\","
         f"IFERROR(SUM(B{r}:D{r})-SUM(B{r-1}:D{r-1}),\"\"))")
    formulas.append([f])
ws.update(values=formulas, range_name="G3:G99", value_input_option="USER_ENTERED")
print("저축!G3:G99 → IRP 제외 예적금 증액분 자동 계산 수식 적용")

# 검증
data = ws.get("A1:J5", value_render_option="UNFORMATTED_VALUE")
for r in data:
    print(r)
