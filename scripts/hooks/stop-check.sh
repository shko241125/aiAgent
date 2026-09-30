#!/usr/bin/env bash
# Stop 훅: 응답을 끝내기 전 문서를 동기화하고, 검증 실패가 남아 있으면 종료를 막고 수정을 요구한다.
# stop_hook_active=true(이미 한 번 막힘)면 무한 루프 방지를 위해 통과시킨다.
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
active=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))' 2>/dev/null)
[ -x .venv/bin/python ] || exit 0
problems=$(.venv/bin/python -m aiops.kanban.cli docs sync --quiet 2>&1 >/dev/null)
if [ -n "$problems" ] && [ "$active" != "True" ]; then
  python3 -c 'import json,sys
print(json.dumps({"decision": "block", "reason": "칸반/문서 검증 실패 — CLAUDE.md R9·R11 에 따라 수정하세요:\n" + sys.argv[1]}, ensure_ascii=False))' "$problems"
fi
exit 0
