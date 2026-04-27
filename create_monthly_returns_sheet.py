"""
월간 투자 수익률 기록용 구글 시트 생성 (1회성 스크립트).
- 시트1 '주식'  : 월별 주식 평가액 / 입출금 / 월 손익 / 월/누적 수익률
- 시트2 '저축'  : 적금/연금 — 사용자가 첫 행 컬럼 구조를 직접 입력
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gspread
from googleapiclient.discovery import build
from sheet_auth import get_credentials

FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")
SHEET_NAME = "투자 수익률 기록"
START_MONTH = "2026-04"
STOCK_START = 353_277_139

creds = get_credentials()
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

# 동명 시트 존재 확인
q = (f"name='{SHEET_NAME}' and '{FOLDER_ID}' in parents "
     f"and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
existing = drive.files().list(q=q, fields="files(id,name)").execute().get("files", [])
if existing:
    print(f"이미 존재: {SHEET_NAME} (id={existing[0]['id']}) — 중단")
    sys.exit(0)

sh = gc.create(SHEET_NAME, folder_id=FOLDER_ID)
print(f"생성됨: {SHEET_NAME} (id={sh.id})")

# ── 시트1: 주식 ──
ws1 = sh.sheet1
ws1.update_title("주식")
ws1.update("A1", [
    ["월", "주식 평가액", "입출금(추가입금/출금)", "월 손익", "월 수익률", "누적 수익률"],
    [START_MONTH, STOCK_START, 0, "", "", ""],
])
# 다음 달부터 자동 계산되도록 행 3에 수식 템플릿 주석 (실제 입력은 사용자가)
# row N: 평가액=B_N, 입출금=C_N, 손익=B_N-B_{N-1}-C_N, 수익률=손익/(B_{N-1}+C_N), 누적=B_N/B$2 - 1 (단, 입출금 합 차감 필요)
# 여기서는 첫 행만 채우고, 2행 이후는 사용자가 평가액/입출금만 입력하면 D,E,F가 자동.
ws1.format("A1:F1", {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.85, "green": 0.92, "blue": 1.0}})
ws1.format("B2:C2", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})

# 3행부터 99행까지 수식 미리 깔아두기 — 사용자가 평가액(B)·입출금(C)만 입력하면 자동 계산
formulas = []
for r in range(3, 100):
    formulas.append([
        "",  # A 월 (사용자 직접 입력)
        "",  # B 평가액
        "",  # C 입출금
        f"=IF(B{r}=\"\",\"\",B{r}-B{r-1}-C{r})",                  # D 월 손익
        f"=IF(B{r}=\"\",\"\",IFERROR(D{r}/(B{r-1}+C{r}),\"\"))",  # E 월 수익률
        f"=IF(B{r}=\"\",\"\",IFERROR((B{r}-SUM(C$2:C{r}))/B$2-1,\"\"))",  # F 누적 수익률
    ])
ws1.update("A3:F99", formulas, value_input_option="USER_ENTERED")
ws1.format("E2:F99", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
ws1.format("D2:D99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0;[Red]-#,##0"}})
ws1.format("B3:C99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws1.columns_auto_resize(0, 6)

# ── 시트2: 저축 ──
ws2 = sh.add_worksheet(title="저축", rows=120, cols=20)
ws2.update("A1", [
    ["월", "적금A(이름변경)", "적금B(이름변경)", "연금", "합계", "입출금", "월 손익", "월 수익률", "누적 수익률"],
    [START_MONTH, "", "", "", "", 0, "", "", ""],
])
ws2.update("A4", [["[안내] 2행에 본인 적금/연금 잔액과 칼럼명을 직접 입력하세요. 적금 개수가 다르면 B~D 칼럼을 자유롭게 추가/삭제 후, '합계'(E)·'입출금'(F)·'월 손익'(G)·'월 수익률'(H)·'누적 수익률'(I) 위치만 유지하면 다음 달부터 동일 포맷으로 정리됩니다."]])
ws2.format("A1:I1", {"textFormat": {"bold": True}, "backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.85}})
ws2.format("A4:I4", {"textFormat": {"italic": True, "foregroundColor": {"red": 0.4, "green": 0.4, "blue": 0.4}}})
ws2.merge_cells("A4:I4")

# 합계/손익/수익률 수식 (2행은 사용자가 잔액 채우면 합계만 자동, 손익은 비워둠)
ws2.update_acell("E2", "=IF(COUNTA(B2:D2)=0,\"\",SUM(B2:D2))")

# 3행부터 자동 수식
formulas2 = []
for r in range(3, 100):
    formulas2.append([
        "",  # A 월
        "", "", "",  # B,C,D 적금/연금 (사용자 입력)
        f"=IF(COUNTA(B{r}:D{r})=0,\"\",SUM(B{r}:D{r}))",                # E 합계
        "",  # F 입출금
        f"=IF(E{r}=\"\",\"\",E{r}-E{r-1}-F{r})",                          # G 월 손익
        f"=IF(E{r}=\"\",\"\",IFERROR(G{r}/(E{r-1}+F{r}),\"\"))",          # H 월 수익률
        f"=IF(E{r}=\"\",\"\",IFERROR((E{r}-SUM(F$2:F{r}))/E$2-1,\"\"))",  # I 누적 수익률
    ])
ws2.update("A3:I99", formulas2, value_input_option="USER_ENTERED")
ws2.format("B2:F99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
ws2.format("G2:G99", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0;[Red]-#,##0"}})
ws2.format("H2:I99", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
ws2.columns_auto_resize(0, 9)

print(f"\n완료: https://docs.google.com/spreadsheets/d/{sh.id}")
