---
name: blog-learn
description: "네이버 발행본 URL → 본문 추출 → draft와 diff → 수정 패턴 추출 → 학습 누적까지 한 번에. 호출: `/blog-learn <url>` 또는 `/blog-learn <draft경로> <url>`. 도담아빠 블로그 일일 리뷰 글의 발행본을 받아서 작성 규약(blog-kr/blog-us)을 자동 보강."
---

# Blog Learn (fetch + diff + distill)

도담아빠 블로그 일일 리뷰의 **draft → 네이버 발행본 차이**를 학습해 작성 규약을 점진 보강하는 스킬.

## 사용법

- `/blog-learn <naver-url>` ← 기본. draft는 자동 매칭
- `/blog-learn <draft경로> <naver-url>` ← 명시

## 동작 순서 (중간에 멈추지 말 것)

### 1. 본문 추출
```bash
python "C:\Users\dongh\Desktop\주식\AI agent\.claude\skills\blog-learn\scripts\fetch_naver.py" <naver-url>
```
stdout에 "# 제목\n\n본문" 형식으로 출력. 임시 파일에 저장 후 다음 단계로 넘김.

### 2. draft 매칭
- 인자로 draft 경로 받았으면 그거 사용
- 아니면 `C:\Users\dongh\Desktop\주식\blog\draft\` 안에서 **가장 최근 .md 파일** 선택
  - 발행본 본문 첫 줄(제목)에 `KR` 또는 `US`, 날짜가 들어있으면 우선 매칭
- 매칭 실패 시 사용자에게 draft 경로 물어보고 중단

### 3. diff 생성
Python `difflib.unified_diff` 로 draft vs 발행본 비교:

```python
import difflib
diff = difflib.unified_diff(
    draft_text.splitlines(keepends=True),
    published_text.splitlines(keepends=True),
    fromfile="draft",
    tofile="published",
    n=3,
)
diff_str = "".join(diff)
```

### 4. 패턴 추출 (Claude가 직접 분석)
6개 분류별로 핵심 교훈 1~3줄씩:
- **삭제**: draft에 있었지만 발행본에서 빠진 내용 — 어떤 종류의 표현/문장이 잘려나가는가
- **어조**: 문체·존댓말·강도(강한 동사 → 부드러운 동사) 변화 패턴
- **표현**: 어휘 치환(예: "직격탄" → "직격탄으로 해석")
- **구조**: 섹션 순서·헤딩 레벨·bullet vs 단락 변화
- **사실추가**: 발행본에서 새로 추가된 데이터·인용·맥락
- **(이미지·표는 무시)**: 네이버 에디터 변환 차이라 학습 대상 아님

### 5. 저장 (3개 파일 모두)

#### (a) 개별 lesson
경로: `C:\Users\dongh\Desktop\주식\AI agent\.claude\skills\blog-learn\lessons\YYYY-MM-DD_제목.md`
구조:
```markdown
---
date: YYYY-MM-DD
naver_url: https://blog.naver.com/...
draft_path: C:\Users\dongh\Desktop\주식\blog\draft\...
market: KR | US | THEMATIC
---

## 분류된 패턴

### 삭제
- ...

### 어조
- ...

### 표현
- ...

### 구조
- ...

### 사실추가
- ...

## Raw diff
```
<unified diff 전문>
```
```

#### (b) 인덱스 한 줄 추가
경로: `C:\Users\dongh\Desktop\주식\AI agent\.claude\skills\blog-learn\lessons\_index.md`
형식: `- YYYY-MM-DD [KR|US|THEMATIC] [제목](파일명.md) — 이번 lesson 핵심 한 줄`

#### (c) 통합 스타일 가이드 갱신 (필수, 빠뜨리지 말 것)
경로: `C:\Users\dongh\Desktop\주식\AI agent\.claude\skills\blog-learn\lessons\_distilled_style_guide.md`

**누적된 모든 lesson 파일을 다시 읽고 정수만 뽑아 매번 처음부터 재작성**.
- 중복되거나 모순된 규칙은 통합/삭제
- 6개 분류별로 정수 규칙만 5~10개 유지 (오래된 규칙도 여전히 유효하면 유지, 무효화됐으면 삭제)
- blog-kr/blog-us 스킬이 작성 시 이 파일을 reference로 읽음 — 명확한 마크다운 헤딩과 짧은 bullet으로 유지

### 6. 보고
사용자에게 한 단락:
> 이번 lesson에서 새로 추가/강화된 규칙 3개:
> ① ...
> ② ...
> ③ ...

## 중요
- **5(c) 통합 가이드 갱신을 빠뜨리지 말 것** — 이걸 안 하면 학습이 죽고 그냥 diff 로그가 됨
- blog-kr/blog-us 스킬은 작성 시 `_distilled_style_guide.md`를 자동 읽도록 연결되어 있음
- diff 결과가 미미하거나 의미있는 패턴이 없으면 그 사실을 lesson 파일에 명시하고 통합 가이드는 갱신 생략

## 참고
- 발행본 URL 형식: `https://blog.naver.com/{blogId}/{logNo}` 또는 `PostView.naver?blogId=...&logNo=...`
- fetch_naver.py는 자동으로 양쪽 형식 모두 파싱
