#!/usr/bin/env bash
# 用法：CT=rh3 bash timeline.sh logs/rh3-timeline.log
# 每 2 秒看一次容器（預設 rh2，可用 CT=rh3 指定）的進程與關鍵檔案，狀態有變才記一行（給彩排計時用）
out="$1"; prev=""
while true; do
  s=$(MSYS_NO_PATHCONV=1 docker exec "${CT:-rh2}" sh -c '
    H=/home/player
    p=$(ps -eo args= | grep -vE "^(sh -c|grep)" | grep -oE "apt-get (update|install)|dpkg|git clone|bootstrap\.sh|setup\.sh|install\.sh|pip install|^claude.*|^bash -l" | cut -c1-40 | sort -u | tr "\n" ",")
    f=""
    for x in Elephant .local/bin/claude Elephant/work/env.md .claude.json .claude/.credentials.json Elephant/work/data-notes.md Elephant/answers.md; do
      [ -e "$H/$x" ] && f="$f $x"
    done
    d=$(find $H/Elephant/data -type f 2>/dev/null | wc -l)
    echo "procs=[$p] files=[$f ] data_files=$d"' 2>/dev/null)
  if [ -n "$s" ] && [ "$s" != "$prev" ]; then
    echo "$(date +%H:%M:%S) $s" >> "$out"
    prev="$s"
  fi
  sleep 2
done
