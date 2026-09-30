#!/usr/bin/env bash
# SessionStart 훅: 의존성 보장 → git 훅 경로 설정 → 칸반 보드 브리핑을 컨텍스트로 출력.
# 메모리 없는 새 세션이 가장 먼저 보드 상태를 알게 하는 장치 (CLAUDE.md §2).
set -u
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" || exit 0
git config core.hooksPath .githooks 2>/dev/null || true
if [ ! -x .venv/bin/python ] && command -v uv >/dev/null 2>&1; then
  uv venv -q .venv -p 3.11 >/dev/null 2>&1 && uv pip install -q -p .venv -e ".[dev]" >/dev/null 2>&1
fi
if [ -x .venv/bin/python ]; then
  .venv/bin/python -m aiops.kanban.cli brief 2>&1 || true
else
  echo "(칸반 브리핑 불가: .venv 없음 — 'make install' 후 'python -m aiops.kanban.cli brief')"
fi
exit 0
