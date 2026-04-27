"""매월 27일 20:00 KST — 투자 수익률 기록 시트 입력 리마인더 메일."""
import os, sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from zoneinfo import ZoneInfo

from notifier import send_email

SHEET_URL = "https://docs.google.com/spreadsheets/d/1Jnm55njxzB8NxY0LJAlrum8WPMHuD-EdeMlpHMQfHrY"


def main():
    now = datetime.datetime.now(ZoneInfo("Asia/Seoul"))
    month = now.strftime("%Y-%m")
    subject = f"[롱돌이] {month} 월간 정산 입력 리마인더 (D-2h)"
    body = (
        f"오늘은 {now.strftime('%Y-%m-%d')} 월간 정산일입니다.\n"
        f"22:00에 주식 탭 D/E/F(월 손익·수익률·누적)가 자동 갱신됩니다.\n"
        f"그 전까지 아래 항목을 {month} 행에 입력해주세요.\n\n"
        f"[주식]\n"
        f"  · 평가액 (B): 27일 종가 기준 계좌 잔고\n"
        f"  · '입출금 이력' 탭: 이번달 미기록 거래 추가 (대출이자 출금 포함)\n\n"
        f"[저축] 청년도약 / 일반적금 / 주청 / IRP 잔액 (B~E)\n\n"
        f"[대출] 대출1·2 잔액(B/E) + 월 상환원금(C/F) + 월 이자(D/G)\n\n"
        f"→ {SHEET_URL}\n"
    )
    ok = send_email(subject, body)
    print(f"발송 {'성공' if ok else '실패'}: {subject}")


if __name__ == "__main__":
    main()
