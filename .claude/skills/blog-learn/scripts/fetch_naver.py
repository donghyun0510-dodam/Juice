"""Naver 블로그 URL → 본문 텍스트 추출.

사용: python fetch_naver.py <naver-url>
출력: stdout에 "# 제목\n\n본문" 마크다운 형태로 출력
"""
import re
import sys
import urllib.parse as urlparse

import requests
from bs4 import BeautifulSoup

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _parse_blog_id_logno(url: str):
    p = urlparse.urlparse(url)
    qs = urlparse.parse_qs(p.query)
    if "blogId" in qs and "logNo" in qs:
        return qs["blogId"][0], qs["logNo"][0]
    parts = [x for x in p.path.split("/") if x]
    if len(parts) >= 2 and parts[-1].isdigit():
        return parts[-2], parts[-1]
    return None, None


def fetch_naver_post(url: str) -> str:
    blog_id, log_no = _parse_blog_id_logno(url)
    if not blog_id or not log_no:
        raise SystemExit(f"URL에서 blogId/logNo 추출 실패: {url}")

    pv = (
        f"https://blog.naver.com/PostView.naver?blogId={blog_id}"
        f"&logNo={log_no}&redirect=Dlog&widgetTypeCall=true&directAccess=false"
    )
    r = requests.get(pv, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title = ""
    for sel in (".se-title-text", ".htitle", ".pcol1", "h3.tit_h3", "title"):
        t = soup.select_one(sel)
        if t and t.get_text(strip=True):
            title = t.get_text(" ", strip=True)
            break

    body = (
        soup.select_one(".se-main-container")
        or soup.select_one("#postViewArea")
        or soup.select_one(".post-view")
    )
    if not body:
        raise SystemExit("본문 컨테이너(.se-main-container / #postViewArea) 못 찾음.")

    for tag in body.select("script, style"):
        tag.decompose()
    text = body.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return f"# {title}\n\n{text}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용: python fetch_naver.py <naver-url>")
    print(fetch_naver_post(sys.argv[1]))
