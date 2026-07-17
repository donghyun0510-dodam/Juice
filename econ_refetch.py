"""지정일 경제지표를 investing.com에서 재수집해 JSON으로 출력 (시트 쓰기 없음).

왜 필요한가:
    investing.com 캘린더는 Cloudflare 403이 **간헐적**으로 난다(daily-global 로그 기준 최근
    12영업일 중 7/9·7/13·7/16 3회). 403이 나면 그날 시트 '2.경제 지표'가 실제 발표가 있었는데도
    '(전날 주요 지표 발표 없음)'으로 조용히 기록된다. 그 구멍을 사후에 메우기 위한 수동 도구.

왜 Actions에서 돌리는가:
    한국 가정 IP에서는 워밍업 GET부터 상시 403이라 로컬 실행이 불가능하다(6회 재시도 전부 실패).
    GitHub Actions 러너 IP는 간헐적으로 통과하므로 econ-refetch.yml(workflow_dispatch)로 돌린다.

    python econ_refetch.py 2026-07-16 [--country US]

출력: stdout 마지막 줄에 ECON_JSON= 접두사로 JSON 배열
      [[지표명, "실제 ▲", "예상: X / 이전: Y"], ...]
"""
import argparse
import json
from datetime import datetime

# stdout UTF-8 래핑은 daily_review가 import 시 수행한다. 여기서 먼저 감싸면 daily_review가
# 그 wrapper의 .buffer를 다시 감싸고, 원래 wrapper가 GC되며 버퍼를 닫아버린다
# (ValueError: I/O operation on closed file). 래핑하지 말 것.
from daily_review import build_economic_indicators


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="대상일 YYYY-MM-DD (발표일 기준)")
    ap.add_argument("--country", default="US", choices=["US", "KR", "CN", "JP"])
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date()
    items = build_economic_indicators(target, country=args.country)

    print(f"\n수집 결과: {len(items)}건")
    print("ECON_JSON=" + json.dumps(items, ensure_ascii=False))


if __name__ == "__main__":
    main()
