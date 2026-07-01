# -*- coding: utf-8 -*-
"""
도담아빠 일일 리뷰(.md) → 커뮤니티 평문 변환기.

표를 못 쓰는 커뮤니티(네이버 카페 등)용으로 마크다운 일일 리뷰를 평문으로 변환:
  - 헤딩 마커 제거:  "## 1. 시장 뉴스" → "1. 시장 뉴스",  "### 1) 금리" → "1) 금리",  "# 제목" → "제목"
  - 볼드 제거:       "**텍스트**" → "텍스트"
  - 표 → 평문:
      * 지수/구분/지표 전치표(헤더행+값행) → "DOW -0.98% / S&P500 -1.21% / ..." 인라인
      * 시장 뉴스표(분류|이벤트|내용) → 각 행을 문단으로. 분류=경제지표면 "경제지표 발표: {내용}", 그 외엔 {내용}만
      * 그 외 일반표 → 행마다 비어있지 않은 셀을 " / "로 연결
  - 연속 빈 줄 1줄로 축소
  - "- " 불릿, "→" 분석줄, "*매크로 종합" 줄은 그대로 유지(볼드만 제거)

원본 파일은 절대 재입력하지 않고 그대로 읽어 변환 → 종목 리스트 오타 방지.

사용:
  python convert_community.py [YYYY-MM-DD] [KR|US]
  python convert_community.py --file <경로>

인자 없으면 published 폴더의 가장 최근 _published.md(없으면 draft) 자동 선택.
출력: stdout + community/YYYY-MM-DD_{KR|US}_community.md 저장.
"""
import os
import re
import sys
import glob

BASE = r"C:\Users\dongh\Desktop\Google Drive 동기화\blog"
PUBLISHED_DIR = os.path.join(BASE, "published")
DRAFT_DIR = os.path.join(BASE, "draft")
COMMUNITY_DIR = os.path.join(BASE, "community")

INDEX_HEADERS = {"지수", "구분", "지표", "변동률"}  # 전치(인라인) 대상 표의 첫 헤더 셀

LABELS = True  # 시장 뉴스 행 앞에 [이벤트] 라벨 표기(기본 ON). --no-labels로 끔


def _strip_bold(s: str) -> str:
    return s.replace("**", "")


def _split_row(line: str):
    """| a | b | c | → ['a','b','c'] (양끝 파이프 제거)."""
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _is_separator(line: str) -> bool:
    return bool(re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", line)) and "-" in line


def _convert_table(block):
    """표 라인 블록(리스트) → 평문 라인 리스트."""
    rows = [_split_row(l) for l in block if not _is_separator(l)]
    if not rows:
        return []
    header = rows[0]
    data = rows[1:]

    # 1) 전치형 인라인 표 (지수/구분/지표) — 헤더행 + 단일 값행
    if header and header[0] in INDEX_HEADERS and len(data) >= 1:
        out = []
        for vals in data:
            pairs = [f"{h} {v}" for h, v in zip(header[1:], vals[1:]) if v]
            if pairs:
                out.append(" / ".join(pairs))
        return out

    # 2) 시장 뉴스형 표 (분류/이벤트/내용)
    low = [h.replace(" ", "") for h in header]
    if "내용" in low:
        ci = low.index("내용")
        clsi = low.index("분류") if "분류" in low else None
        evi = low.index("이벤트") if "이벤트" in low else None
        out = []
        for r in data:
            content = r[ci] if ci < len(r) else ""
            if not content:
                continue
            cls = (r[clsi] if (clsi is not None and clsi < len(r)) else "")
            if "경제지표" in cls:
                out.append(f"경제지표 발표: {content}")
            else:
                ev = (r[evi] if (evi is not None and evi < len(r)) else "")
                if LABELS and ev:
                    out.append(f"[{ev}] {content}")
                else:
                    out.append(content)
        return out

    # 3) 일반표 폴백 — 행마다 비어있지 않은 셀을 " / "로
    out = []
    for r in data:
        cells = [c for c in r if c]
        if cells:
            out.append(" / ".join(cells))
    return out


def convert(text: str) -> str:
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 표 블록 수집
        if line.strip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            for conv in _convert_table(block):
                out.append(_strip_bold(conv))
                out.append("")  # 행 사이 빈 줄
            continue
        # 헤딩 마커 제거
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            out.append(_strip_bold(m.group(2)))
            i += 1
            continue
        out.append(_strip_bold(line))
        i += 1

    # 연속 빈 줄 1줄로 축소
    cleaned = []
    blank = False
    for l in out:
        if l.strip() == "":
            if not blank:
                cleaned.append("")
            blank = True
        else:
            cleaned.append(l)
            blank = False
    # 양끝 빈 줄 정리
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned) + "\n"


def _parse_name(path):
    """파일명에서 (YYYY-MM-DD, MARKET) 추출. 실패 시 (None, None)."""
    base = os.path.basename(path)
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(KR|US|THEMATIC)_", base)
    if m:
        return m.group(1), m.group(2)
    return None, None


def find_source(date=None, market=None):
    """published 우선, 없으면 draft에서 (date, market) 매칭 파일 반환."""
    def match(d):
        files = []
        for p in glob.glob(os.path.join(d, "*.md")):
            fdate, fmkt = _parse_name(p)
            if fdate is None:
                continue
            if date and fdate != date:
                continue
            if market and fmkt != market:
                continue
            files.append((fdate, p))
        return files

    for d in (PUBLISHED_DIR, DRAFT_DIR):
        files = match(d)
        if files:
            files.sort(reverse=True)  # 최근 날짜 우선
            return files[0][1]
    return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 cp949 인코딩 우회
    except Exception:
        pass
    global LABELS
    argv = [a for a in sys.argv[1:]]
    if "--no-labels" in argv:
        LABELS = False
        argv = [a for a in argv if a != "--no-labels"]
    if "--labels" in argv:  # 명시적 ON (기본이 ON이라 사실상 무동작)
        LABELS = True
        argv = [a for a in argv if a != "--labels"]
    src = None
    date = None
    market = None
    if "--file" in argv:
        src = argv[argv.index("--file") + 1]
    else:
        for a in argv:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", a):
                date = a
            elif a.upper() in ("KR", "US", "THEMATIC"):
                market = a.upper()
        src = find_source(date, market)

    if not src or not os.path.exists(src):
        print(f"ERROR: 변환할 원본을 찾지 못함 (date={date}, market={market}). "
              f"published/draft 폴더 확인 또는 --file 로 경로 지정.")
        sys.exit(1)

    with open(src, encoding="utf-8") as f:
        text = f.read()
    result = convert(text)

    fdate, fmkt = _parse_name(src)
    os.makedirs(COMMUNITY_DIR, exist_ok=True)
    out_name = f"{fdate}_{fmkt}_community.md"
    out_path = os.path.join(COMMUNITY_DIR, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"[source] {src}")
    print(f"[saved ] {out_path}")
    print("=" * 60)
    print(result)


if __name__ == "__main__":
    main()
