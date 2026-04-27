"""
시트6 '목표 계산기' — 종합 시트 데이터 기반 목표 도달 자동 시뮬레이션.

기능:
  A) 목표 순자산 + 목표 시점 → 필요 주식 누적/연환산 수익률
  B) 목표 순자산 + 가정 주식 연수익률 → 도달 시점 (개월·날짜)
  C) 목표 시나리오 비교표 (4~10억 × 시점별)
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import gspread
from sheet_auth import get_credentials

SHEET_ID = "1Jnm55njxzB8NxY0LJAlrum8WPMHuD-EdeMlpHMQfHrY"
gc = gspread.authorize(get_credentials())
sh = gc.open_by_key(SHEET_ID)

TAB = "목표 계산기"
try:
    ws = sh.worksheet(TAB)
    ws.clear()
    ws.resize(rows=300, cols=20)
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title=TAB, rows=300, cols=20)

# ─────────── 메인 영역: A(라벨) | B(값) ───────────
labels_b = [
    ["■ 현재 상태 (자동 갱신)"],                 # 1
    ["기준일"],                                   # 2
    ["주식 평가액"],                              # 3
    ["저축 합계"],                                # 4
    ["대출 잔액"],                                # 5
    ["순자산"],                                   # 6
    [""],                                         # 7
    ["■ 가정 (수정 가능)"],                       # 8
    ["저축 월 증가분"],                           # 9
    ["대출 월 상환원금"],                         # 10
    [""],                                         # 11
    ["■ 시나리오 A: 목표시점 → 필요 수익률"],    # 12
    ["목표 순자산"],                              # 13  ← 사용자 입력
    ["목표 시점 (YYYY-MM)"],                      # 14  ← 사용자 입력
    ["경과 개월수"],                              # 15
    ["예상 저축"],                                # 16
    ["예상 대출"],                                # 17
    ["필요 주식 평가액"],                         # 18
    ["필요 누적 수익률"],                         # 19
    ["필요 연환산 수익률"],                       # 20
    [""],                                         # 21
    ["■ 시나리오 B: 가정 수익률 → 도달 시점"],   # 22
    ["목표 순자산"],                              # 23  ← 사용자 입력
    ["가정 주식 연수익률"],                       # 24  ← 사용자 입력
    ["도달 개월수"],                              # 25
    ["도달 시점"],                                # 26
    ["기준일 + 개월수"],                          # 27
]
ws.update(values=labels_b, range_name="A1:A27")

# 월 형식 셀들을 TEXT 포맷으로 강제 (USER_ENTERED 시 날짜 자동 변환 방지)
for c in ["B2", "B14", "E2", "F2", "G2", "H2", "I2"]:
    ws.format(c, {"numberFormat": {"type": "TEXT"}})

values_b = [
    [""],                                                                                        # 1
    ['=INDEX(종합!$A$2:$A$99, COUNTA(종합!$A$2:$A$99))'],                                         # 2
    ['=INDEX(종합!$B$2:$B$99, COUNTA(종합!$A$2:$A$99))'],                                         # 3
    ['=INDEX(종합!$C$2:$C$99, COUNTA(종합!$A$2:$A$99))'],                                         # 4
    ['=INDEX(종합!$E$2:$E$99, COUNTA(종합!$A$2:$A$99))'],                                         # 5
    ['=INDEX(종합!$F$2:$F$99, COUNTA(종합!$A$2:$A$99))'],                                         # 6
    [""],                                                                                        # 7
    [""],                                                                                        # 8
    [1513504],                                                                                   # 9
    [418052],                                                                                    # 10
    [""],                                                                                        # 11
    [""],                                                                                        # 12
    [400000000],                                                                                 # 13
    [""],                                                                                        # 14 (RAW로 별도 입력)
    ['=DATEDIF(DATEVALUE(B2&"-01"), DATEVALUE(B14&"-01"), "M")'],                                # 15
    ['=B4 + B9*B15'],                                                                            # 16
    ['=MAX(0, B5 - B10*B15)'],                                                                   # 17
    ['=B13 - B16 + B17'],                                                                        # 18
    ['=IFERROR(B18/B3 - 1, "")'],                                                                # 19
    ['=IFERROR(POWER(1+B19, 12/B15) - 1, "")'],                                                  # 20
    [""],                                                                                        # 21
    [""],                                                                                        # 22
    [500000000],                                                                                 # 23
    [0.12],                                                                                      # 24
    ['=IFERROR(MATCH(TRUE, ARRAYFORMULA(Q6:Q246>=B23), 0)-1, "20년 내 미도달")'],                # 25
    ['=IF(ISNUMBER(B25), TEXT(EDATE(DATEVALUE(B2&"-01"), B25), "YYYY-MM"), "")'],                # 26
    ['=IF(ISNUMBER(B25), B25 & "개월 (" & ROUND(B25/12,1) & "년)", "")'],                        # 27
]
ws.update(values=values_b, range_name="B1:B27", value_input_option="USER_ENTERED")
ws.update(values=[["2029-04"]], range_name="B14", value_input_option="RAW")

# ─────────── 시나리오 비교표 (D~I열) ───────────
ws.update(values=[["■ 시나리오 비교 (목표 × 시점 → 필요 연환산 %)"]], range_name="D1")
ws.update(values=[["목표 순자산"]], range_name="D2")
ws.update(values=[["2028-04", "2029-04", "2030-04", "2031-04", "2032-04"]],
          range_name="E2:I2", value_input_option="RAW")

def scenario_cell(col_letter, target_ref):
    months = f'DATEDIF(DATEVALUE($B$2&"-01"),DATEVALUE({col_letter}$2&"-01"),"M")'
    sav = f'($B$4 + $B$9*{months})'
    loan = f'MAX(0, $B$5 - $B$10*{months})'
    req_stock = f'({target_ref} - {sav} + {loan})'
    return f'=IFERROR(POWER({req_stock}/$B$3, 12/{months}) - 1, "")'

scenario_rows = []
for i, target in enumerate([400_000_000, 500_000_000, 600_000_000, 700_000_000, 800_000_000, 1_000_000_000]):
    row_num = 3 + i
    cells = [target]
    for col in ["E", "F", "G", "H", "I"]:
        cells.append(scenario_cell(col, f"$D{row_num}"))
    scenario_rows.append(cells)
ws.update(values=scenario_rows, range_name=f"D3:I{2+len(scenario_rows)}",
          value_input_option="USER_ENTERED")

# ─────────── 헬퍼 테이블 (M~Q열, T=0..240) ───────────
ws.update(values=[["T", "주식(T)", "저축(T)", "대출(T)", "순자산(T)"]], range_name="M5:Q5")
helper = []
for t in range(0, 241):
    row_num = 6 + t
    if t == 0:
        helper.append([0, "=B3", "=B4", "=B5", f"=N{row_num}+O{row_num}-P{row_num}"])
    else:
        helper.append([
            t,
            f"=$B$3*POWER(1+$B$24, M{row_num}/12)",
            f"=$B$4 + $B$9*M{row_num}",
            f"=MAX(0, $B$5 - $B$10*M{row_num})",
            f"=N{row_num}+O{row_num}-P{row_num}",
        ])
ws.update(values=helper, range_name=f"M6:Q{6+240}", value_input_option="USER_ENTERED")

# ─────────── 포맷 ───────────
ws.format("A1", {"textFormat": {"bold": True, "fontSize": 11},
                 "backgroundColor": {"red": 0.85, "green": 0.92, "blue": 1.0}})
ws.format("A8", {"textFormat": {"bold": True, "fontSize": 11},
                 "backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.85}})
ws.format("A12", {"textFormat": {"bold": True, "fontSize": 11},
                  "backgroundColor": {"red": 0.88, "green": 0.95, "blue": 0.85}})
ws.format("A22", {"textFormat": {"bold": True, "fontSize": 11},
                  "backgroundColor": {"red": 0.95, "green": 0.88, "blue": 0.85}})
ws.format("D1", {"textFormat": {"bold": True, "fontSize": 11},
                 "backgroundColor": {"red": 0.95, "green": 0.85, "blue": 0.95}})

# 입력 강조
for cell in ["B13", "B14", "B23", "B24"]:
    ws.format(cell, {"backgroundColor": {"red": 1.0, "green": 0.98, "blue": 0.7}})

# 결과 강조
ws.format("B20", {"textFormat": {"bold": True, "foregroundColor": {"red": 0.7, "green": 0, "blue": 0}},
                  "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.85}})
ws.format("B26:B27", {"textFormat": {"bold": True, "foregroundColor": {"red": 0, "green": 0.4, "blue": 0}},
                      "backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85}})

# 숫자 포맷
ws.format("B3:B6", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws.format("B9:B10", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws.format("B13", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws.format("B16:B18", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws.format("B19:B20", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
ws.format("B23", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws.format("B24", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
ws.format("D3:D8", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws.format("E3:I8", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
ws.format("D2:I2", {"textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.95, "green": 0.85, "blue": 0.95}})

# 헬퍼 테이블 (회색)
ws.format("M5:Q5", {"textFormat": {"bold": True, "fontSize": 9},
                    "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}})
ws.format("M6:Q246", {"textFormat": {"fontSize": 9, "foregroundColor": {"red": 0.6, "green": 0.6, "blue": 0.6}}})
ws.format("N6:Q246", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})

ws.columns_auto_resize(0, 1)
ws.columns_auto_resize(3, 9)

# 시트 순서: 마지막
sh.batch_update({
    "requests": [{
        "updateSheetProperties": {
            "properties": {"sheetId": ws.id, "index": 5},
            "fields": "index"
        }
    }]
})

print(f"'{TAB}' 시트 생성 완료")
print(f"→ https://docs.google.com/spreadsheets/d/{SHEET_ID}")
