---
name: blog-learn
description: "네이버 발행본 URL → 본문 추출 → draft와 diff → 수정 패턴 추출 → 학습 누적까지 한 번에. 호출: `/blog-learn <url>` 또는 `/blog-learn <draft경로> <url>`. 도담아빠 블로그 일일 리뷰 글의 발행본을 받아서 작성 규약(blog-kr/blog-us)을 자동 보강."
---

# Blog Learn (fetch + diff + distill)

도담아빠 블로그 일일 리뷰의 **draft → 네이버 발행본 차이**를 학습해 작성 규약을 점진 보강하는 스킬.

## 학습 방향 — 네이버 AI 브리핑 인용 5대 기준 (상위 렌즈)
이 블로그(도담아빠, 4genstorytelling)의 최종 목표는 **네이버 AI 브리핑 인용**이다. draft→발행본 diff에서 규칙을 뽑을 때, 그 변경이 아래 5대 인용 기준 중 무엇을 강화하는지를 판단 렌즈로 쓴다:
1. **전문성·경험** — 분석 프레임 적용·깊이
2. **주제 일관성** — 고정 포맷·단일 주제
3. **진정성·투명성** — 원인 지어내기·서사 짜맞추기 금지 (가장 직접적)
4. **가독성** — 표·`→`·헤딩 빈 줄 구조
5. **활동성·최신성** — 당일 발행·최신 이벤트

- 발행 편집이 이 5축 중 하나를 강화하는 방향이면(예: 사변적 추측 삭제=③, 헤딩 정리=④) lesson의 해당 분류에 그 축을 명시하고 통합 가이드에 규칙으로 승격.
- **통합 가이드가 5축에서 멀어지는 규칙을 축적하지 않도록 가드.** 특히 ③ 진정성과 충돌하는 규칙(그럴듯한 인과 서사 강화 등)은 채택 금지.

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
- 아니면 `C:\Users\dongh\Desktop\Google Drive 동기화\blog\draft\` 안에서 **가장 최근 .md 파일** 선택
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

각 패턴에 **어느 AI 브리핑 축(①~⑤)을 강화하는지 괄호로 태깅** — 예: `- 사변적 "수요 둔화 우려" 삭제 (③ 진정성)`. 어느 축과도 무관한 순수 스타일 변화는 태깅 생략.

### 5. 저장 (4개 파일 모두)

#### (a) 개별 lesson
경로: `C:\Users\dongh\Desktop\주식\AI agent\.claude\skills\blog-learn\lessons\YYYY-MM-DD_제목.md`
구조:
```markdown
---
date: YYYY-MM-DD
naver_url: https://blog.naver.com/...
draft_path: C:\Users\dongh\Desktop\Google Drive 동기화\blog\draft\...
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

#### (d) 발행 최종본 저장 (필수, 빠뜨리지 말 것)
경로: `C:\Users\dongh\Desktop\Google Drive 동기화\blog\published\`
파일명: `YYYY-MM-DD_{KR|US|THEMATIC}_published.md` (draft와 동일한 날짜·시장, 접미사만 `_published`)

- **draft에 step 3~4에서 식별한 발행 변경(삭제·정정·추가·재작성)을 적용해 깔끔한 마크다운 최종본**을 만들어 저장. fetch_naver.py 원문은 네이버 에디터 줄바꿈·볼드 소실 노이즈가 있으므로 그대로 쓰지 말고, **draft의 마크다운 구조(표·헤딩·볼드)를 유지한 채 내용만 발행본에 맞춤**.
- 이미지·표 렌더링 차이는 무시(학습 대상 아님). 텍스트 내용 일치가 목적.
- 목적: draft ≠ 발행본일 때 완성된 최종본을 통으로 보관 (lesson의 raw diff는 파편이라 최종본 대용 불가).

### 6. 보고
사용자에게 한 단락:
> 이번 lesson에서 새로 추가/강화된 규칙 3개:
> ① ...
> ② ...
> ③ ...

## 중요
- **5(c) 통합 가이드 갱신을 빠뜨리지 말 것** — 이걸 안 하면 학습이 죽고 그냥 diff 로그가 됨
- **5(d) 발행 최종본 저장도 빠뜨리지 말 것** — draft는 발행 전 버전이라, 실제 발행본은 published/ 폴더에만 남음
- blog-kr/blog-us 스킬은 작성 시 `_distilled_style_guide.md`를 자동 읽도록 연결되어 있음
- diff 결과가 미미하거나 의미있는 패턴이 없으면 그 사실을 lesson 파일에 명시하고 통합 가이드는 갱신 생략

## 참고
- 발행본 URL 형식: `https://blog.naver.com/{blogId}/{logNo}` 또는 `PostView.naver?blogId=...&logNo=...`
- fetch_naver.py는 자동으로 양쪽 형식 모두 파싱
