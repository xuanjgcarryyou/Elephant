#!/usr/bin/env bash
# 2026金象杯 - 現場環境初始化腳本
# 用法: bash setup.sh（通常由 bootstrap.sh 自動呼叫）
#
# 不需要 sudo / root：所有東西都裝在家目錄（~/.local/bin、~/.elephant-venv），不呼叫 apt。
#
# 為了速度：
# - Claude Code、Python 環境、HDFS 檢查三件事同時在背景跑；Claude 和 HDFS 好了就結束，
#   Python 套件比較慢的話繼續在背景裝（登入只需要 Claude，pandas 到「開始」之後才用得到）
# - Python 用 uv（單一執行檔，比 pip 快很多）建 venv；系統 Python 太舊（例如 3.8、沒有 pip）時，
#   uv 會自己下載 Python 3.12，不需要 root
# - 系統沒有 zstd 時，用 venv 裡的 zstandard 臨時充當 zstd，讓官方安裝腳本下載壓縮版的 Claude
#   （137MB，原本是 381MB）
# - HDFS 一連上就在背景啟動 tools/prefetch.py：平行下載資料到 data/、先跑好 peek，
#   使用者登入 Claude 的同時資料就在準備
#
# 刻意不用 set -e：單一步驟失敗時繼續往下跑，最後統一列出所有問題，
# 避免現場卡在第一個錯誤、看不到後面的檢查結果。
set -uo pipefail

cd "$(dirname "$0")" || exit 1
WORKDIR="$(pwd)"
VENV="${ELEPHANT_VENV:-$HOME/.elephant-venv}"
STATE="$WORKDIR/work/.setup"   # 背景工作的輸出、完成標記
rm -rf "$STATE"
mkdir -p "$STATE/shim" "$WORKDIR/data" "$WORKDIR/work" "$HOME/.local/bin"
T0=$SECONDS

warn() { echo "⚠️  $*"; echo "$*" >> "$STATE/warnings"; }
have() { command -v "$1" &> /dev/null; }
PY_SYS="$(command -v python3 || true)"

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

# fetch URL [輸出檔]：沒給輸出檔就寫到 stdout。
# 一律用 curl；系統沒有 curl 時，下面會先做一個同名的替代品（wget 或 python3），官方安裝腳本也能用
fetch() { curl -fsSL --retry 2 --connect-timeout 15 -o "${2:--}" "$1"; }

# 等某個標記檔出現（任一個就算），最多 $1 秒
wait_for() {
    local limit=$(( $1 * 2 )) i f
    shift
    for (( i = 0; i < limit; i++ )); do
        for f in "$@"; do [ -e "$f" ] && return 0; done
        sleep 0.5
    done
    return 1
}

# ---------------------------------------------------------------------------
echo "=== 1/3 基本設定 ==="
# 中文輸出不亂碼、時間以台灣時區為準
export LANG=C.UTF-8 TZ=Asia/Taipei
add_rc "export LANG=C.UTF-8"
add_rc "export TZ=Asia/Taipei"
# Claude Code 和 uv 都裝在這裡，先加進 PATH（背景工作共用）
add_to_path "$HOME/.local/bin"
# 臨時指令（curl / zstd 替代品）只給這次安裝用，不寫進設定檔
export PATH="$STATE/shim:$PATH"
# 現場只能用內部 PyPI 鏡像時，沿用 pip 的設定
[ -n "${PIP_INDEX_URL:-}" ] && [ -z "${UV_DEFAULT_INDEX:-}" ] && export UV_DEFAULT_INDEX="$PIP_INDEX_URL"

if ! have curl; then
    if have wget; then
        echo "沒有 curl，用 wget 代替"
        cat > "$STATE/shim/curl" <<'EOF'
#!/bin/sh
# 只支援這次安裝用到的寫法：curl [旗標] [-o 檔案] URL
out=-; url=
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --retry|--connect-timeout|--max-time) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
exec wget -q -O "$out" "$url"
EOF
    elif [ -n "$PY_SYS" ]; then
        echo "沒有 curl、wget，用 python3 代替"
        cat > "$STATE/shim/curl" <<EOF
#!/bin/sh
# 只支援這次安裝用到的寫法：curl [旗標] [-o 檔案] URL
out=-; url=
while [ \$# -gt 0 ]; do
    case "\$1" in
        -o) out="\$2"; shift 2 ;;
        --retry|--connect-timeout|--max-time) shift 2 ;;
        -*) shift ;;
        *) url="\$1"; shift ;;
    esac
done
# 一定要換 User-Agent：claude.ai 會擋 Python 預設的「Python-urllib」（403）
exec "$PY_SYS" -c '
import shutil, sys, urllib.request as u
r = u.urlopen(u.Request(sys.argv[1], headers={"User-Agent": "elephant-setup/1.0"}), timeout=60)
out = sys.stdout.buffer if sys.argv[2] == "-" else open(sys.argv[2], "wb")
shutil.copyfileobj(r, out, 1 << 20)
' "\$url" "\$out"
EOF
    else
        warn "curl、wget、python3 都沒有，無法下載任何東西"
    fi
    [ -f "$STATE/shim/curl" ] && chmod +x "$STATE/shim/curl"
fi
missing_tools=()
for t in git tar unzip file; do have "$t" || missing_tools+=("$t"); done
[ ${#missing_tools[@]} -gt 0 ] && echo "系統沒有：${missing_tools[*]}（不影響安裝；沒有 unzip 時用 python3 -m zipfile -e）"

# ---------------------------------------------------------------------------
# 背景工作 1：Claude Code CLI

node_major() {
    local v
    v="$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')"
    echo "${v:-0}"
}

# npm 備案：Node.js 也裝在家目錄（官方 tarball），全域套件裝到 ~/.npm-global，都不需要 root
install_via_npm() {
    if ! have node || [ "$(node_major)" -lt 18 ]; then
        local arch base file
        case "$(uname -m)" in
            x86_64|amd64) arch=x64 ;;
            aarch64|arm64) arch=arm64 ;;
            *) warn "不支援的 CPU 架構：$(uname -m)"; return 1 ;;
        esac
        base=https://nodejs.org/dist/latest-v22.x
        file="$(fetch "$base/SHASUMS256.txt" | grep -o "node-v[0-9.]*-linux-$arch\.tar\.gz" | head -1)"
        [ -n "$file" ] || { warn "找不到 Node.js 的下載檔"; return 1; }
        echo "下載 $file 到 ~/.local/node ..."
        rm -rf "$HOME/.local/node" && mkdir -p "$HOME/.local/node"
        fetch "$base/$file" | tar -xzf - --strip-components=1 -C "$HOME/.local/node" \
            || { warn "Node.js 下載失敗"; return 1; }
        add_to_path "$HOME/.local/node/bin"
    fi
    echo "Node.js $(node -v)"
    add_to_path "$HOME/.npm-global/bin"
    npm install -g --prefix "$HOME/.npm-global" --no-fund --no-audit @anthropic-ai/claude-code \
        || { warn "npm 安裝 Claude Code 失敗"; return 1; }
}

job_claude() {
    if have claude; then
        echo "已安裝，略過：$(command -v claude)"
        claude --version 2>/dev/null || true
        return
    fi
    # 官方安裝腳本在有 zstd 時會改下載壓縮版（137MB，原本 381MB）。
    # 等 Python 那邊裝好 zstandard（通常幾秒；最多等 30 秒），做一個臨時的 zstd 給它用。
    # 解壓失敗也沒關係：安裝腳本驗證 checksum 不符時，會自動改下載未壓縮版
    if ! have zstd && wait_for 30 "$STATE/zstd.ready" "$STATE/zstd.none" "$STATE/python.done" \
        && [ -f "$STATE/zstd.ready" ]; then
        cat > "$STATE/shim/zstd" <<EOF
#!/bin/sh
# 只支援官方安裝腳本用到的寫法：zstd -d -q -c 檔案（解壓到 stdout）
for f; do :; done
exec "$VENV/bin/python" -c 'import sys, zstandard; zstandard.ZstdDecompressor().copy_stream(open(sys.argv[1], "rb"), sys.stdout.buffer, write_size=1 << 20)' "\$f"
EOF
        chmod +x "$STATE/shim/zstd"
        echo "用 zstandard 充當 zstd，下載壓縮版"
    fi
    echo "使用官方安裝腳本（不需要 Node.js，也不需要 sudo）..."
    fetch https://claude.ai/install.sh "$STATE/claude-install.sh" && bash "$STATE/claude-install.sh"
    if ! have claude; then
        echo "官方安裝腳本失敗，改用 npm 備案..."
        install_via_npm
    fi
    if have claude; then
        echo "claude 位置：$(command -v claude)"
        claude --version 2>/dev/null || true
    else
        warn "Claude Code 安裝失敗（檢查網路是否能連到 claude.ai / downloads.claude.ai / nodejs.org）"
    fi
}

# ---------------------------------------------------------------------------
# 背景工作 2：Python 資料分析環境

# pandas/pyarrow：主力；openpyxl/xlrd：新舊 Excel；duckdb：SQL 驗算用的第二種獨立寫法
# 格式「import 名稱|pip 規格」（import 名稱和 pip 套件名稱相同）。
# 大版本設上限，讓比賽當天裝到的跟測試時同一代（2026-11 測試過：pandas 3.1.1、duckdb 2.0.0；
# duckdb 2.0 就改過 API）。下限放寬：退回系統舊版 Python 時（uv 失敗的備案），還能裝 pandas 2.x。
PY_PKGS=(
    "pandas|pandas>=2,<4"
    "pyarrow|pyarrow"
    "openpyxl|openpyxl"
    "xlrd|xlrd"
    "duckdb|duckdb>=1,<3"
)
PY_SPECS=()
for entry in "${PY_PKGS[@]}"; do PY_SPECS+=("${entry#*|}"); done

# 一次檢查所有套件（只啟動一次 Python）：印出每個套件的狀態，全部符合才回傳 0
py_check() {
    "$1" - "${PY_PKGS[@]}" <<'EOF'
import importlib, re, sys
ok_all = True
for entry in sys.argv[1:]:
    mod, spec = entry.split("|", 1)
    try:
        ver = importlib.import_module(mod).__version__
    except Exception:
        print(f"  {mod}：沒有安裝（需要 {spec}）")
        ok_all = False
        continue
    major = int(re.match(r"\d+", ver).group())
    ok = all(major >= int(n) if op == ">=" else major < int(n) for op, n in re.findall(r"(>=|<)(\d+)", spec))
    print(f"  {mod} {ver}" + ("" if ok else f"：版本不符（需要 {spec}）"))
    ok_all = ok_all and ok
sys.exit(0 if ok_all else 1)
EOF
}

install_uv() {
    have uv && return 0
    local arch
    case "$(uname -m)" in
        x86_64|amd64) arch=x86_64 ;;
        aarch64|arm64) arch=aarch64 ;;
        *) return 1 ;;
    esac
    # musl 版是靜態連結，任何 Linux 發行版都能跑
    fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-unknown-linux-musl.tar.gz" "$STATE/uv.tar.gz" \
        && tar -xzf "$STATE/uv.tar.gz" -C "$HOME/.local/bin" --strip-components=1 \
        && have uv
}

# uv 失敗時的備案：系統 python3 + pip --user（沒有 pip 就先裝到家目錄）
python_fallback() {
    [ -n "$PY_SYS" ] || { warn "找不到 python3，uv 也裝不起來"; return 1; }
    echo "改用系統 Python：$("$PY_SYS" --version 2>&1)"
    py_check "$PY_SYS" > /dev/null && return 0
    if ! "$PY_SYS" -m pip --version &> /dev/null; then
        local pyver
        pyver="$("$PY_SYS" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
        if ! "$PY_SYS" -m ensurepip --user &> /dev/null; then
            # 舊版 Python 有各自版本的 get-pip.py
            fetch "https://bootstrap.pypa.io/pip/$pyver/get-pip.py" "$STATE/get-pip.py" \
                || fetch https://bootstrap.pypa.io/get-pip.py "$STATE/get-pip.py"
            "$PY_SYS" "$STATE/get-pip.py" --user -q || { warn "pip 安裝失敗"; return 1; }
        fi
    fi
    # Ubuntu 23.04+ 會擋系統 Python 的 pip（PEP 668），失敗時改加 --break-system-packages
    "$PY_SYS" -m pip install --user -q "${PY_SPECS[@]}" \
        || "$PY_SYS" -m pip install --user -q --break-system-packages "${PY_SPECS[@]}" \
        || warn "Python 套件安裝失敗：${PY_SPECS[*]}"
}

job_python() {
    local pybin="" how=""
    if install_uv; then
        echo "uv $(uv --version 2>/dev/null | awk '{print $2}')"
        if ! "$VENV/bin/python" -c '' &> /dev/null; then
            # 系統 Python 3.11+ 就直接用（pandas 3 需要 3.11+）；否則讓 uv 下載 3.12
            local base=3.12
            [ -n "$PY_SYS" ] && "$PY_SYS" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2> /dev/null && base="$PY_SYS"
            uv venv -q --clear --python "$base" "$VENV" || warn "uv 建立 venv 失敗"
        fi
    fi
    if "$VENV/bin/python" -c '' &> /dev/null; then
        pybin="$VENV/bin/python"
        export VIRTUAL_ENV="$VENV"
        # venv 一建好就寫進設定檔：setup 不等套件裝完就會結束，使用者 source ~/.bashrc 時要已經有這兩行
        add_to_path "$VENV/bin"
        add_rc "export VIRTUAL_ENV=\"$VENV\""
        touch "$STATE/venv.ready"
        echo "venv：$VENV（$("$pybin" --version 2>&1)）"
        # zstandard 先裝（很小）：Claude 那邊等它來加速下載；pip 放進 venv，現場 pip install 也能用
        uv pip install -q --python "$pybin" zstandard && touch "$STATE/zstd.ready" || touch "$STATE/zstd.none"
        uv pip install -q --python "$pybin" pip "${PY_SPECS[@]}" || warn "uv 安裝 Python 套件失敗：${PY_SPECS[*]}"
        touch "$STATE/pkgs.ready"   # prefetch 等這個才開始 peek
        how="uv venv（$VENV）；裝套件用 \`uv pip install <套件>\`"
    else
        touch "$STATE/zstd.none"
        # 有系統 Python 就先讓 prefetch 開始下載（peek 會等這裡裝完）
        [ -n "$PY_SYS" ] && touch "$STATE/venv.ready"
        warn "uv 無法使用，改用系統 python3 + pip（比較慢）"
        python_fallback
        pybin="$PY_SYS"
        how="系統 Python + pip --user；裝套件用 \`python3 -m pip install --user <套件>\`"
    fi
    [ -n "$pybin" ] || return
    touch "$STATE/venv.ready"
    py_check "$pybin" || warn "Python 套件不齊或版本不符（見上方）"
    if "$pybin" -c "import sys; sys.path.insert(0, 'tools'); import tw; assert tw.region('台北') == '北部'" 2> /dev/null; then
        echo "  tools/tw.py 自我檢查通過"
    else
        warn "tools/tw.py 自我檢查失敗"
    fi
    echo "- Python：$("$pybin" --version 2>&1)，$how" > "$STATE/env.python"
    write_env_md   # setup 可能已經先結束了，自己更新 env.md
}

# ---------------------------------------------------------------------------
# 背景工作 3：HDFS 連線檢查，連上就在背景開始下載資料

job_hdfs() {
    local cmd="" tmo=""
    if have hdfs; then cmd="hdfs dfs"
    elif have hadoop; then cmd="hadoop fs"
    fi
    have timeout && tmo="timeout 30"   # NameNode 連不上時 hdfs 會重試好幾分鐘
    if [ -z "$cmd" ]; then
        warn "找不到 hdfs/hadoop CLI，需要現場確認資料存取方式（WebHDFS REST API？本機掛載？），見 docs/hdfs.md"
        echo "- HDFS 指令：無" > "$STATE/env.hdfs"
        return
    fi
    echo "使用：$cmd"
    # getconf 和 ls 各要啟動一次 JVM，同時跑
    # shellcheck disable=SC2086  # 刻意讓 $tmo / $cmd 做字詞切割
    have hdfs && { $tmo hdfs getconf -confKey fs.defaultFS > "$STATE/defaultfs" 2> /dev/null & }
    local status
    # shellcheck disable=SC2086
    if $tmo $cmd -ls / > "$WORKDIR/work/hdfs-root.txt" 2> "$STATE/hdfs-ls.err"; then
        status="正常"
        cat "$WORKDIR/work/hdfs-root.txt"
    else
        status="失敗"
        tail -5 "$STATE/hdfs-ls.err"
        warn "'$cmd -ls /' 失敗或超過 30 秒：檢查 NameNode 連線、權限、JAVA_HOME，或是否需要 Kerberos（klist / kinit）"
    fi
    wait
    local default_fs
    default_fs="$(cat "$STATE/defaultfs" 2> /dev/null)"
    [ -n "$default_fs" ] && echo "fs.defaultFS = $default_fs"
    {
        echo "- HDFS 指令：$cmd；連線：$status"
        echo "- fs.defaultFS：${default_fs:-未知}"
    } > "$STATE/env.hdfs"

    [ "$status" = "正常" ] || return
    # 有 Python 就開始背景下載（不必等 pandas 裝好：peek 那一步才需要，prefetch 會自己等）
    wait_for 120 "$STATE/venv.ready" "$STATE/python.done"
    local py="$VENV/bin/python"
    "$py" -c '' &> /dev/null || py="$PY_SYS"
    if [ -z "$py" ]; then
        warn "沒有 Python，無法自動下載 HDFS 資料"
        return
    fi
    # setsid + nohup：脫離這個終端機，setup 結束、使用者啟動 claude 之後還會繼續跑
    local detach=nohup
    have setsid && detach="setsid nohup"
    # shellcheck disable=SC2086  # $detach 刻意做字詞切割
    (cd "$WORKDIR" && $detach "$py" tools/prefetch.py --auto --peek-after "$STATE/pkgs.ready" "$STATE/python.done" \
        > work/prefetch.log 2>&1 < /dev/null &)
    echo "已在背景開始下載 HDFS 資料（進度：work/prefetch.log）"
    echo "- HDFS 資料：setup 時已在背景自動下載，用 \`python3 tools/prefetch.py --wait\` 等它完成" >> "$STATE/env.hdfs"
}

# ---------------------------------------------------------------------------
# 環境紀錄：Claude「開始」時會先讀這份，省掉重新偵測環境的時間。
# setup 結束時寫一次；Python 比較晚裝完時，Python 那邊會再寫一次

[ -f CLAUDE.md ] || warn "找不到 CLAUDE.md，Claude 不會自動套用解題規則"
[ -f .claude/settings.json ] || warn "找不到 .claude/settings.json，比賽中每個指令都會要求確認"
OTHER_TOOLS=()
for t in java beeline hive spark-sql spark-submit pyspark klist; do
    have "$t" && OTHER_TOOLS+=("$t")
done

write_env_md() {
    local warnings=() w
    [ -f "$STATE/warnings" ] && mapfile -t warnings < "$STATE/warnings"
    {
        echo "# 環境偵測結果（setup.sh 更新於 $(date '+%Y-%m-%d %H:%M:%S')）"
        echo ""
        echo "- OS：$(. /etc/os-release 2> /dev/null && echo "$PRETTY_NAME")，$(uname -m)，$(nproc 2> /dev/null || echo ?) 核心"
        echo "- 使用者：$(id -un)（root：$([ "$(id -u)" -eq 0 ] && echo 是 || echo 否)；不使用 sudo）"
        cat "$STATE/env.python" 2> /dev/null || echo "- Python：還在背景安裝（紀錄：work/.setup/python.log）"
        cat "$STATE/env.hdfs" 2> /dev/null
        echo "- 其他工具：${OTHER_TOOLS[*]:-無}"
        [ ${#missing_tools[@]} -gt 0 ] && echo "- 系統沒有：${missing_tools[*]}"
        if [ ${#warnings[@]} -gt 0 ]; then
            echo ""
            echo "## setup 時的問題"
            for w in "${warnings[@]}"; do echo "- $w"; done
        fi
    } > "$WORKDIR/work/env.md.$BASHPID" && mv -f "$WORKDIR/work/env.md.$BASHPID" "$WORKDIR/work/env.md"
}

# ---------------------------------------------------------------------------
echo ""
echo "=== 2/3 同時安裝 Claude Code、Python 環境、檢查 HDFS（每完成一項就印出結果）==="
JOBS=(claude python hdfs)
declare -A TITLE=([claude]="Claude Code CLI" [python]="Python 資料分析環境" [hdfs]="HDFS / Hadoop")
declare -A PID=()
for j in "${JOBS[@]}"; do
    (
        trap '' HUP   # 網頁終端機斷線也繼續裝
        # shellcheck disable=SC2034  # trap 字串裡用到
        start=$SECONDS
        trap 'echo $(( SECONDS - start )) > "$STATE/$j.done"' EXIT
        "job_$j"
    ) > "$STATE/$j.log" 2>&1 < /dev/null &
    PID[$j]=$!
done
echo "（背景進行中，Claude Code 下載最久，通常 20～60 秒）"
# 登入只需要 Claude；Python 套件要到「開始」之後才用得到，所以只等 Claude 和 HDFS。
# Python 比較慢時讓它繼續在背景裝，跟使用者登入同時進行（prefetch 會等它裝好才 peek）
show_done() {
    local j
    for j in "${JOBS[@]}"; do
        case "$shown" in *" $j "*) continue ;; esac
        [ -f "$STATE/$j.done" ] || continue
        echo ""
        echo "--- ${TITLE[$j]}（$(cat "$STATE/$j.done") 秒）---"
        cat "$STATE/$j.log"
        shown="$shown$j "
    done
}
shown=" "
until [ -f "$STATE/claude.done" ] && [ -f "$STATE/hdfs.done" ]; do
    show_done
    sleep 0.3
done
wait "${PID[claude]}" "${PID[hdfs]}"
show_done
case "$shown" in
    *" python "*) ;;
    *) echo ""
       echo "--- ${TITLE[python]}：還在背景安裝，不影響登入，可以先繼續 ---"
       echo "（Claude「開始」時會自動等它裝好；紀錄：work/.setup/python.log）" ;;
esac

# 背景工作加進 PATH 的目錄，這裡也要加，後面的檢查才找得到
for d in "$HOME/.local/node/bin" "$HOME/.npm-global/bin" "$VENV/bin"; do
    [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac
done

# ---------------------------------------------------------------------------
echo ""
echo "=== 3/3 環境紀錄 ==="
echo "其他可用工具：${OTHER_TOOLS[*]:-無}"
write_env_md
echo "已寫入 work/env.md"

WARNINGS=()
[ -f "$STATE/warnings" ] && mapfile -t WARNINGS < "$STATE/warnings"

# ---------------------------------------------------------------------------
echo ""
if [ ${#WARNINGS[@]} -eq 0 ]; then
    echo "=== 全部完成，沒有問題（$(( SECONDS - T0 )) 秒）==="
else
    echo "=== 完成，但有 ${#WARNINGS[@]} 個問題需要處理（$(( SECONDS - T0 )) 秒）==="
    for w in "${WARNINGS[@]}"; do echo "  - $w"; done
fi

cat <<EOF

下一步：
  1. 貼上這一行：重新載入 PATH，並在 repo 目錄啟動 Claude（才會讀到 CLAUDE.md 和權限設定）

       source ~/.bashrc; cd "$WORKDIR" && claude

  2. 登入：claude 會印出一個網址，複製到瀏覽器新分頁打開、登入，再把驗證碼貼回終端機
     （網頁終端機的貼上常是 Ctrl+Shift+V 或右鍵；網址很長會折行，要整段選到）
     分頁關掉或斷線：重新進來後執行 cd "$WORKDIR" && claude -c，接回上一段對話
  3. 第一句話：「開始」——HDFS 資料已經在背景下載，Claude 會接手拉完資料、看懂每份資料
  4. 把第 1～10 題放進 $WORKDIR/1.txt，跟 Claude 說「做題」：全部算完後一次寫進 answer.md
EOF
