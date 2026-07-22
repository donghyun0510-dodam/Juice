"""마감 후 배치 시각엔 아직 미게시였던 '정산 일별 바'를 뒤늦게 다시 확인해
글로벌 시트(3.매크로 동향)의 원자재·FX 셀을 패치한다.

배경 — daily_review(--global-only)는 미 장마감 ~1.5h 뒤(실제 22:35~22:52 UTC) 돈다.
이때 네이버 /prices 일별 바가 아직 D-1까지만 게시된 지표가 있으면, 예외도 없이
**D-1 값이 조용히 시트에 박힌다**. 실제 사고:
  - 2026-07-22 시트 COPPER 13,621/+0.89% (07-20 바) — 실제 07-21은 13,898/+2.04%
  - 2026-07-18 시트 COPPER·DXY 동일 증상
daily_review는 경고만 찍고 넘어가므로(고칠 방법이 그 시점엔 없음), 몇 시간 뒤
이 스크립트가 다시 확인해 대상일 바가 게시됐으면 해당 셀만 덮어쓴다.

동작:
  - 대상 세션일(target)은 daily_review.main과 같은 규칙(전일, 주말은 금요일)
  - naver_settled_date(key) == target 인 지표만 대상. 아직 미게시면 그대로 둠
  - 시트 값과 다를 때만 D(값)·E(등락률)·F(위험) 갱신 → 멱등, 변화 없으면 무동작
  - 하나라도 바뀌면 '원자재 종합 경보'(C-Risk)·'★ 매크로 종합'을 시트에 표시된
    값들로 재계산해 함께 갱신 (레그 입력을 재수집하지 않아 표시값과 항상 일치)

사용: python settled_backfill.py [--date YYMMDD]
"""

import sys
from datetime import datetime, timedelta

# daily_review import 시 sys.stdout이 UTF-8로 재설정된다. 여기서 또 감싸면 같은 버퍼에
# 래퍼가 두 겹 붙어 먼저 것이 GC될 때 버퍼가 닫힌다 — 절대 중복 래핑하지 말 것.
import daily_review as dr
from naver_macro import naver_settled_date


# 시트 라벨 -> (네이버 논리 key, 값 수집 함수, 위험 진단 함수 or None)
# 값 수집 함수는 (price_str, chg_str) 또는 (price_str, chg_str, val)을 반환.
PATCHABLE = [
    ("BRN",     "BRENT",  lambda: dr.get_price_and_change("BZ=F", settled=True),
     lambda v, c: dr.assess_risk("BRN", v, c)),
    ("WTI",     "WTI",    dr.get_wti_investing,
     lambda v, c: dr.assess_risk("WTI", v, c)),
    ("COPPER",  "COPPER", dr.get_copper_investing,
     lambda v, c: dr.assess_copper_risk()),
    ("SILVER",  "SILVER", dr.get_silver_investing, None),
    ("GOLD",    "GOLD",   dr.get_gold_investing, None),
    ("DXY",     "DXY",    lambda: dr.get_price_and_change("DX-Y.NYB", settled=True),
     lambda v, c: dr.assess_risk("DXY", v)),
    ("EUR/USD", "EURUSD", lambda: dr.get_price_and_change("EURUSD=X", settled=True), None),
]


def target_session_date(today):
    """daily_review.main과 동일한 규칙: 전일, 일요일이면 금요일, 토요일이면 금요일."""
    d = today - timedelta(days=1)
    if d.weekday() == 6:
        d -= timedelta(days=2)
    elif d.weekday() == 5:
        d -= timedelta(days=1)
    return d


def macro_section_rows(vals):
    """3.매크로 동향 구간의 라벨 -> 1-based 행번호. B열(★ 매크로 종합)·C열 모두 수집."""
    start = end = None
    for i, row in enumerate(vals):
        a = (row[0] if row else "").strip()
        if a.startswith("3.매크로") and start is None:
            start = i
        elif a.startswith("4.지수") and start is not None:
            end = i
            break
    if start is None:
        return {}
    end = end if end is not None else len(vals)
    labels = {}
    for i in range(start, end):
        row = vals[i]
        for col in (1, 2):  # B, C
            label = (row[col] if len(row) > col else "").strip()
            if label and label not in labels:
                labels[label] = i + 1
    return labels


def cell(vals, row_1based, col_0based):
    row = vals[row_1based - 1] if 0 < row_1based <= len(vals) else []
    return (row[col_0based] if len(row) > col_0based else "").strip()


def set_cell(vals, row_1based, col_0based, value):
    """로컬 사본 갱신용. get_all_values는 뒤쪽 빈 칸을 잘라 반환하므로 패딩 필요."""
    row = vals[row_1based - 1]
    while len(row) <= col_0based:
        row.append("")
    row[col_0based] = value


def recompute_composites(vals, rows):
    """시트에 표시된 값들로 C-Risk·매크로 종합을 재계산 -> [(A1범위, 값, 색상)].

    레그 입력을 새로 수집하지 않고 시트 D/E열을 읽어 쓰므로, 갱신된 셀이 그대로
    반영되고 나머지 레그는 배치 시점 값이 유지된다(재수집하면 다음 세션 값이 섞인다).
    """
    def val_of(label):
        r = rows.get(label)
        return dr.parse_price(cell(vals, r, 3)) if r else None

    def chg_of(label):
        r = rows.get(label)
        return dr.parse_pct(cell(vals, r, 4)) if r else None

    out = []
    wti_v, brent_v = val_of("WTI"), val_of("BRN")
    gold_v, copper_v = val_of("GOLD"), val_of("COPPER")
    oil_chgs = [c for c in (chg_of("WTI"), chg_of("BRN")) if c is not None]
    c_label, c_color, c_score = dr.compute_c_risk_index(
        wti_v, brent_v, gold_v, copper_v,
        oil_chg=(sum(oil_chgs) / len(oil_chgs) if oil_chgs else None),
        silver_chg=chg_of("SILVER"), copper_chg=chg_of("COPPER"),
    )
    if "원자재 종합 경보" in rows:
        out.append((f"F{rows['원자재 종합 경보']}", c_label, c_color))

    # 매크로 종합: 금리·환율 레그는 수준만으로 결정되고, 위험심리는 VIX 수준·변동 +
    # 신용스프레드(HYG/IEF 일봉, 마감 후 고정) + 금·달러 모멘텀.
    _, _, t_score = dr.compute_t_risk_index(val_of("2년물"), val_of("10년물"), val_of("30년물"))
    _, _, fx_score = dr.compute_fx_risk_index(val_of("DXY"), val_of("USD/JPY"), val_of("USD/CNY"))
    vix_label = "VIX (선물)" if "VIX (선물)" in rows else "VIX"
    _, _, s_score = dr.compute_s_risk_index(
        val_of(vix_label), credit_dev_pct=dr.fetch_credit_dev_pct(),
        gold_chg=chg_of("GOLD"), dxy_chg=chg_of("DXY"),
        vix_chg=cell(vals, rows.get(vix_label, 0), 4) or None,
    )
    m_label, m_color, _total, _ = dr.compute_macro_composite(t_score, fx_score, c_score, s_score)
    if "★ 매크로 종합" in rows:
        out.append((f"F{rows['★ 매크로 종합']}", m_label, m_color))
    return out


def main():
    today = datetime.now()
    if "--date" in sys.argv:
        today = datetime.strptime(sys.argv[sys.argv.index("--date") + 1], "%y%m%d")
    sheet_name = f"증시 리뷰_{today.strftime('%y%m%d')}"
    target = target_session_date(today)
    tgt = target.strftime("%Y-%m-%d")
    print(f"=== 정산 바 백필: {sheet_name} (대상 세션 {tgt}) ===")

    q = (f"name='{sheet_name}' and '{dr.FOLDER_ID}' in parents and "
         "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
    files = dr.drive.files().list(q=q, fields="files(id, name)").execute().get("files", [])
    if not files:
        print(f"  시트 없음 → 스킵 (글로벌 배치가 아직/휴장)")
        return 0
    sh = dr.gc.open_by_key(files[0]["id"])
    if "글로벌" not in [ws.title for ws in sh.worksheets()]:
        print("  '글로벌' 탭 없음 → 스킵")
        return 0
    ws = sh.worksheet("글로벌")
    vals = ws.get_all_values()
    rows = macro_section_rows(vals)
    if not rows:
        print("  '3.매크로 동향' 구간을 찾지 못함 → 스킵")
        return 0

    updates, formats, changed = [], [], []
    for label, key, fetch, risk_fn in PATCHABLE:
        row = rows.get(label)
        if not row:
            continue
        bar_date = naver_settled_date(key)
        if bar_date != tgt:
            print(f"  {label:8s} 정산 바 {bar_date} ≠ 대상일 {tgt} → 그대로 둠")
            continue
        try:
            res = fetch()
        except Exception as e:
            print(f"  {label:8s} 수집 실패({e}) → 그대로 둠")
            continue
        price_str, chg_str = res[0], res[1]
        if not price_str:
            print(f"  {label:8s} 값 비어 있음 → 그대로 둠")
            continue
        old_p, old_c = cell(vals, row, 3), cell(vals, row, 4)
        if (price_str, chg_str) == (old_p, old_c):
            print(f"  {label:8s} 이미 최신({price_str} {chg_str})")
            continue
        print(f"  {label:8s} 패치: {old_p} {old_c} → {price_str} {chg_str}")
        updates.append({"range": f"D{row}:E{row}", "values": [[price_str, chg_str]]})
        set_cell(vals, row, 3, price_str)
        set_cell(vals, row, 4, chg_str)
        changed.append(label)
        if risk_fn:
            r_label, r_color = risk_fn(dr.parse_price(price_str), chg_str)
            if r_label:
                updates.append({"range": f"F{row}", "values": [[r_label]]})
                set_cell(vals, row, 5, r_label)
                if r_color:
                    formats.append((f"F{row}", r_color))

    if not changed:
        print("  변경 없음 → 종료")
        return 0

    for rng, text, color in recompute_composites(vals, rows):
        updates.append({"range": rng, "values": [[text]]})
        if color:
            formats.append((rng, color))
        print(f"  종합 재계산: {rng} → {text}")

    # 등락률 문자열은 RAW로 써야 한다 (USER_ENTERED는 '+4.37%'를 숫자 0.0437로 바꾼다)
    ws.batch_update(updates, value_input_option="RAW")
    if formats:
        ws.batch_format([
            {"range": rng, "format": {"textFormat": {"foregroundColor": c, "bold": True}}}
            for rng, c in formats
        ])
    print(f"=== 완료: {', '.join(changed)} 패치 ===")
    print(f"URL: {sh.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
