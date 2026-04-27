"""
'입출금 이력' 탭 추가 + 주식!C열을 SUMIFS 자동 합산 수식으로 교체.

입출금 이력 탭:
  A 날짜(YYYY-MM-DD) | B 금액(+입금 / -출금) | C 메모
주식!C_N (월별 입출금) = 입출금 이력에서 해당 월 합계
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gspread
from sheet_auth import get_credentials

SHEET_ID = "1Jnm55njxzB8NxY0LJAlrum8WPMHuD-EdeMlpHMQfHrY"
gc = gspread.authorize(get_credentials())
sh = gc.open_by_key(SHEET_ID)

# ── '입출금 이력' 탭 ──
TAB = "입출금 이력"
try:
    ws = sh.worksheet(TAB)
    ws.clear()
    ws.resize(rows=500, cols=5)
    print(f"기존 {TAB} 클리어")
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title=TAB, rows=500, cols=5)
    print(f"{TAB} 생성")

ws.update(values=[["날짜", "금액", "메모"]], range_name="A1:C1")
ws.format("A1:C1", {"textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.92, "green": 0.92, "blue": 0.85}})
ws.format("A2:A500", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
ws.format("B2:B500", {"numberFormat": {"type": "NUMBER", "pattern": "+#,##0;[Red]-#,##0"}})
ws.freeze(rows=1)

# 안내 한 줄
ws.update(values=[["[안내] 입출금 발생 시 한 줄씩 추가. 금액은 입금=+숫자, 출금=-숫자. 주식 탭의 월별 C열은 이 탭의 합계로 자동 계산됩니다."]],
          range_name="E1")
ws.format("E1", {"textFormat": {"italic": True, "foregroundColor": {"red": 0.4, "green": 0.4, "blue": 0.4}}})

# 시트 순서: 주식(0) 다음으로 이동
sheets_meta = sh.fetch_sheet_metadata()
target_id = ws.id
stock_id = sh.worksheet("주식").id
# index 1로 이동 (주식 바로 뒤)
sh.batch_update({
    "requests": [{
        "updateSheetProperties": {
            "properties": {"sheetId": target_id, "index": 1},
            "fields": "index"
        }
    }]
})
print(f"{TAB} → 주식 다음 위치로 이동")

# ── 주식!C2:C99 → SUMIFS 수식으로 교체 ──
ws_stock = sh.worksheet("주식")
formulas = []
for r in range(2, 100):
    f = (f"=IF(A{r}=\"\",\"\","
         f"IFERROR(SUMIFS('입출금 이력'!B:B,"
         f"'입출금 이력'!A:A,\">=\"&DATEVALUE(A{r}&\"-01\"),"
         f"'입출금 이력'!A:A,\"<=\"&EOMONTH(DATEVALUE(A{r}&\"-01\"),0)),0))")
    formulas.append([f])
ws_stock.update(values=formulas, range_name=f"C2:C99", value_input_option="USER_ENTERED")
print("주식!C2:C99 → SUMIFS 자동 합산 수식 적용")

print(f"\n→ https://docs.google.com/spreadsheets/d/{SHEET_ID}")
