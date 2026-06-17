#!/usr/bin/env bash
# SessionStart 훅: origin/main을 안전하게 자동 반영(git pull --rebase).
# 절대 세션을 막지 않음(항상 exit 0). 시끄럽지 않게, 변경 있을 때만 메시지 출력.
#
# 동작:
#   - git 레포 아님 / 오프라인(fetch 실패) / 이미 최신 → 조용히 종료
#   - 로컬에 커밋 안 된 (추적)변경 있음 → 자동 pull 건너뛰고 경고만(충돌 방지)
#   - 깨끗함 → git pull --rebase --autostash, 충돌 시 자동 abort + 경고
set -u

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
git fetch origin main -q 2>/dev/null || exit 0

behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "")
[ -z "$behind" ] && exit 0
[ "$behind" = "0" ] && exit 0

# 추적 파일에 커밋 안 된 변경이 있으면(untracked 무시) 자동 pull 건너뜀
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  printf '{"systemMessage":"ℹ️ git: 로컬에 커밋 안 된 변경이 있어 자동 pull을 건너뜀 (origin/main이 %s개 앞섬). 커밋 후 git pull --rebase 하세요."}\n' "$behind"
  exit 0
fi

if git pull --rebase --autostash origin main >/dev/null 2>&1; then
  printf '{"systemMessage":"✅ git: origin/main %s개 커밋 자동 반영 완료"}\n' "$behind"
else
  git rebase --abort >/dev/null 2>&1
  printf '{"systemMessage":"⚠️ git pull --rebase 충돌로 자동 중단(abort). 직접 git pull --rebase 후 충돌 해소 필요."}\n'
fi
exit 0
