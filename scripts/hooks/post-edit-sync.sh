#!/usr/bin/env bash
# PostToolUse(Edit|Write) 훅: 카드·코드·문서가 바뀌면 생성 문서를 즉시 재생성한다.
# 검증 문제(잘못된 TODO ID, 끊긴 refs)가 있으면 모델 컨텍스트로 알려 바로 고치게 한다.
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
file=$(python3 -c 'import json,sys
d=json.load(sys.stdin)
print((d.get("tool_input") or {}).get("file_path") or (d.get("tool_response") or {}).get("filePath") or "")' 2>/dev/null)
case "$file" in
  */docs/BOARD.md) exit 0 ;;
  */tracking/*|*/src/*|*/docs/*) ;;
  *) exit 0 ;;
esac
[ -x .venv/bin/python ] || exit 0
out=$(.venv/bin/python -m aiops.kanban.cli docs sync 2>&1)
if [ -n "$out" ]; then
  python3 -c 'import json,sys
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
      "additionalContext": "[docs-sync] " + sys.argv[1]}}, ensure_ascii=False))' "$out"
fi
exit 0
