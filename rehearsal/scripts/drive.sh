#!/usr/bin/env bash
# 從主機驅動容器（預設 rh2，可用 CT=rh3 指定）裡的 claude -p。用法：drive.sh <label> <prompt> [session_id]
# 輸出 stream-json 到 logs/<容器>-<label>.jsonl，結束時印出耗時與摘要
L="${L:-$(cd "$(dirname "$0")/.." && pwd)/logs}"   # 紀錄輸出位置，預設 rehearsal/logs
label="$1"; prompt="$2"; sid="${3:-}"
resume=""; [ -n "$sid" ] && resume="--resume $sid"
start=$(date +%s); echo "$(date +%H:%M:%S) start $label" >> "$L/${CT:-rh2}-drive.log"
MSYS_NO_PATHCONV=1 docker exec -u player -e PROMPT="$prompt" "${CT:-rh2}" bash -lc \
  "cd ~/Elephant && claude -p \"\$PROMPT\" $resume --output-format stream-json --verbose" \
  > "$L/${CT:-rh2}-$label.jsonl" 2> "$L/${CT:-rh2}-$label.stderr"
rc=$?
el=$(( $(date +%s) - start ))
echo "$(date +%H:%M:%S) end $label rc=$rc elapsed=${el}s" >> "$L/${CT:-rh2}-drive.log"
echo "label=$label rc=$rc elapsed=${el}s"
"${PY:-python3}" -I "$(dirname "$0")/summarize.py" "$L/${CT:-rh2}-$label.jsonl"
