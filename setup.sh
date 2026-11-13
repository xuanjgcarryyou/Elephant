#!/usr/bin/env bash
# 2026金象杯 - 現場環境初始化腳本
# 用法: bash setup.sh（通常由 bootstrap.sh 自動呼叫）
#
# 刻意不用 set -e：單一步驟失敗時繼續往下跑，最後統一列出所有問題，
# 避免現場卡在第一個錯誤、看不到後面的檢查結果。
set -uo pipefail

cd "$(dirname "$0")" || exit 1
WORKDIR="$(pwd)"
export DEBIAN_FRONTEND=noninteractive   # 避免 tzdata 等套件跳出互動式設定卡住

WARNINGS=()
warn() { echo "⚠️  $*"; WARNINGS+=("$*"); }
have() { command -v "$1" &> /dev/null; }

# 以 root 執行指令（已是 root 就直接跑，否則用 sudo）
can_root() { [ "$(id -u)" -eq 0 ] || have sudo; }
run_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }

APT_UPDATED=0
apt_install() {
    can_root || { warn "沒有 root/sudo，無法 apt 安裝：$*"; return 1; }
    if [ "$APT_UPDATED" -eq 0 ]; then run_root apt-get update -qq && APT_UPDATED=1; fi
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
}

# 寫入 shell 設定檔（不重複），兩個都要寫：
# - ~/.bashrc：互動 shell 讀這個
# - 登入設定檔：`bash -lc '...'`、`ssh 主機 '...'` 這類非互動的登入 shell 只會讀這個。
#   Ubuntu 的 ~/.bashrc 開頭遇到非互動 shell 就 return，只寫 ~/.bashrc 的話它們讀不到
# 登入設定檔照 bash 的順序取第一個存在的：~/.bash_profile → ~/.bash_login → ~/.profile（都沒有就建 ~/.profile）
LOGIN_RC="$HOME/.profile"
for f in "$HOME/.bash_profile" "$HOME/.bash_login"; do
    [ -f "$f" ] && { LOGIN_RC="$f"; break; }
done
add_rc() {
    local f
    for f in "$HOME/.bashrc" "$LOGIN_RC"; do
        grep -qsxF "$1" "$f" || echo "$1" >> "$f"
    done
}

# 加入 PATH：這次執行立即生效，並寫入 shell 設定檔讓之後的 shell 也找得到。
# 寫進檔案的那行會先檢查，登入 shell 同時讀到登入設定檔和 ~/.bashrc 時才不會重複加
add_to_path() {
    local dir="$1"
    case ":$PATH:" in *":$dir:"*) ;; *) export PATH="$dir:$PATH" ;; esac
    add_rc "case \":\$PATH:\" in *\":$dir:\"*) ;; *) export PATH=\"$dir:\$PATH\" ;; esac"
}

node_major() {
    local v
    v="$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')"
    echo "${v:-0}"
}

# npm 備案：Claude Code 需要 Node 18+；全域套件裝到家目錄，避開 /usr/lib 權限問題 (EACCES)
install_via_npm() {
    if ! have node || [ "$(node_major)" -lt 18 ]; then
        echo "Node.js 不存在或版本低於 18（目前：$(node -v 2>/dev/null || echo 無)），安裝 Node 20..."
        can_root || { warn "沒有 root/sudo，無法安裝 Node.js"; return 1; }
        curl -fsSL https://deb.nodesource.com/setup_20.x | run_root bash - \
            && run_root apt-get install -y -qq nodejs \
            || { warn "Node.js 安裝失敗"; return 1; }
    fi
    echo "Node.js $(node -v)"
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    add_to_path "$HOME/.npm-global/bin"
    npm install -g @anthropic-ai/claude-code || { warn "npm 安裝 Claude Code 失敗"; return 1; }
}

# ---------------------------------------------------------------------------
echo "=== 1/5 基礎套件與語系 ==="
# 空的 Linux 映像檔常常連 curl 都沒有
missing_pkgs=()
for pair in curl:curl git:git file:file unzip:unzip python3:python3; do
    have "${pair%%:*}" || missing_pkgs+=("${pair##*:}")
done
[ -f /etc/ssl/certs/ca-certificates.crt ] || missing_pkgs+=(ca-certificates)
if [ ${#missing_pkgs[@]} -gt 0 ]; then
    echo "安裝：${missing_pkgs[*]}"
    apt_install "${missing_pkgs[@]}" || warn "基礎套件安裝失敗：${missing_pkgs[*]}"
else
    echo "curl / git / file / unzip / python3 都已存在"
fi
# 中文輸出不亂碼、時間以台灣時區為準
export LANG=C.UTF-8 TZ=Asia/Taipei
add_rc "export LANG=C.UTF-8"
add_rc "export TZ=Asia/Taipei"

# ---------------------------------------------------------------------------
echo ""
echo "=== 2/5 安裝 Claude Code CLI ==="
[ -x "$HOME/.local/bin/claude" ] && add_to_path "$HOME/.local/bin"

if have claude; then
    echo "已安裝，略過"
else
    echo "使用官方安裝腳本（不需要 Node.js，也不需要 sudo）..."
    if curl -fsSL https://claude.ai/install.sh | bash; then
        add_to_path "$HOME/.local/bin"
    fi
    if ! have claude; then
        echo "官方安裝腳本失敗，改用 npm 備案..."
        install_via_npm
    fi
fi

if have claude; then
    echo "claude 位置：$(command -v claude)"
    claude --version 2>/dev/null || true
else
    warn "Claude Code 安裝失敗（檢查網路是否能連到 claude.ai / npmjs.org）"
fi

# ---------------------------------------------------------------------------
echo ""
echo "=== 3/5 Python 資料分析環境 ==="
# pandas/pyarrow：主力；openpyxl/xlrd：新舊 Excel；duckdb：SQL 驗算用的第二種獨立寫法
# 格式「import 名稱|pip 規格」（import 名稱和 pip 套件名稱相同）。
# 大版本設上限，讓比賽當天裝到的跟測試時同一代（2026-11 測試過：pandas 3.1.1、duckdb 2.0.0；
# duckdb 2.0 就改過 API）。下限放寬：舊版 Python（例如 Ubuntu 22.04 的 3.10）裝不了 pandas 3，
# 還能退回 2.x。其他套件 API 穩定，不設限。
PY_PKGS=(
    "pandas|pandas>=2,<4"
    "pyarrow|pyarrow"
    "openpyxl|openpyxl"
    "xlrd|xlrd"
    "duckdb|duckdb>=1,<3"
)

# 已安裝、而且大版本符合 pip 規格裡的 >=N、<N 就回傳 0
py_pkg_ok() {
    python3 -c '
import importlib, re, sys
mod, spec = sys.argv[1], sys.argv[2]
major = int(re.match(r"\d+", importlib.import_module(mod).__version__).group())
for op, n in re.findall(r"(>=|<)(\d+)", spec):
    if op == ">=" and major < int(n):
        sys.exit(1)
    if op == "<" and major >= int(n):
        sys.exit(1)
' "$1" "$2" 2>/dev/null
}

if have python3; then
    python3 --version
    missing=()
    for entry in "${PY_PKGS[@]}"; do
        py_pkg_ok "${entry%%|*}" "${entry#*|}" || missing+=("${entry#*|}")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        echo "缺少或版本不符：${missing[*]}，安裝中..."
        python3 -m pip --version &> /dev/null || apt_install python3-pip
        # Ubuntu 23.04+ 會擋系統 Python 的 pip（PEP 668），失敗時改加 --break-system-packages
        python3 -m pip install --user -q "${missing[@]}" \
            || python3 -m pip install --user -q --break-system-packages "${missing[@]}" \
            || warn "Python 套件安裝失敗：${missing[*]}"
        add_to_path "$HOME/.local/bin"
    fi
    for entry in "${PY_PKGS[@]}"; do
        p="${entry%%|*}"
        if py_pkg_ok "$p" "${entry#*|}"; then
            python3 -c "import $p; print('  $p', $p.__version__)"
        else
            warn "python3 的 $p 不存在或版本不符（需要 ${entry#*|}）"
        fi
    done
    if python3 -c "import sys; sys.path.insert(0, 'tools'); import tw; assert tw.region('台北') == '北部'" 2>/dev/null; then
        echo "  tools/tw.py 自我檢查通過"
    else
        warn "tools/tw.py 自我檢查失敗"
    fi
else
    warn "找不到 python3"
fi

# ---------------------------------------------------------------------------
echo ""
echo "=== 4/5 HDFS / Hadoop 生態系 ==="
HDFS_CMD=""
if have hdfs; then
    HDFS_CMD="hdfs dfs"
elif have hadoop; then
    HDFS_CMD="hadoop fs"
fi
TIMEOUT=""
have timeout && TIMEOUT="timeout 30"   # NameNode 連不上時 hdfs 會重試好幾分鐘

HDFS_STATUS="未測試"
DEFAULT_FS=""
if [ -n "$HDFS_CMD" ]; then
    echo "使用：$HDFS_CMD"
    have hdfs && DEFAULT_FS="$($TIMEOUT hdfs getconf -confKey fs.defaultFS 2>/dev/null)"
    [ -n "$DEFAULT_FS" ] && echo "fs.defaultFS = $DEFAULT_FS"
    # shellcheck disable=SC2086  # 刻意讓 $TIMEOUT / $HDFS_CMD 做字詞切割
    if $TIMEOUT $HDFS_CMD -ls /; then
        HDFS_STATUS="正常"
        echo "HDFS 連線正常"
    else
        HDFS_STATUS="失敗"
        warn "'$HDFS_CMD -ls /' 失敗或超過 30 秒：檢查 NameNode 連線、權限、JAVA_HOME，或是否需要 Kerberos（klist / kinit）"
    fi
else
    HDFS_STATUS="找不到 CLI"
    warn "找不到 hdfs/hadoop CLI，需要現場確認資料存取方式（WebHDFS REST API？本機掛載？），見 docs/hdfs.md"
fi

OTHER_TOOLS=()
for t in java beeline hive spark-sql spark-submit pyspark klist; do
    have "$t" && OTHER_TOOLS+=("$t")
done
echo "其他可用工具：${OTHER_TOOLS[*]:-無}"

# ---------------------------------------------------------------------------
echo ""
echo "=== 5/5 工作目錄與環境紀錄 ==="
mkdir -p data work
[ -f CLAUDE.md ] || warn "找不到 CLAUDE.md，Claude 不會自動套用解題規則"
[ -f .claude/settings.json ] || warn "找不到 .claude/settings.json，比賽中每個指令都會要求確認"

# Claude 開局時會先讀這份，省掉重新偵測環境的時間
{
    echo "# 環境偵測結果（setup.sh 產生於 $(date '+%Y-%m-%d %H:%M')）"
    echo ""
    echo "- OS：$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
    echo "- 使用者：$(id -un)（root 權限：$(can_root && echo 有 || echo 無)）"
    echo "- Python：$(python3 --version 2>&1)"
    echo "- HDFS 指令：${HDFS_CMD:-無}；連線：$HDFS_STATUS"
    echo "- fs.defaultFS：${DEFAULT_FS:-未知}"
    echo "- 其他工具：${OTHER_TOOLS[*]:-無}"
    if [ ${#WARNINGS[@]} -gt 0 ]; then
        echo ""
        echo "## setup 時的問題"
        for w in "${WARNINGS[@]}"; do echo "- $w"; done
    fi
} > work/env.md
echo "已寫入 work/env.md"

# ---------------------------------------------------------------------------
echo ""
if [ ${#WARNINGS[@]} -eq 0 ]; then
    echo "=== 全部完成，沒有問題 ==="
else
    echo "=== 完成，但有 ${#WARNINGS[@]} 個問題需要處理 ==="
    for w in "${WARNINGS[@]}"; do echo "  - $w"; done
fi

cat <<EOF

下一步：
  1. 貼上這一行：重新載入 PATH，並在 repo 目錄啟動 Claude（才會讀到 CLAUDE.md 和權限設定）

       source ~/.bashrc; cd "$WORKDIR" && claude

  2. 登入：claude 會印出一個網址，複製到瀏覽器新分頁打開、登入，再把驗證碼貼回終端機
     （網頁終端機的貼上常是 Ctrl+Shift+V 或右鍵；網址很長會折行，要整段選到）
     分頁關掉或斷線：重新進來後執行 cd "$WORKDIR" && claude -c，接回上一段對話
  3. 第一句話：「開局」——Claude 會依 CLAUDE.md 盤點 HDFS 資料並建立資料筆記
EOF
