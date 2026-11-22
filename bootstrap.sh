#!/usr/bin/env bash
# 空環境第一支腳本：取得這個 repo → 執行 setup.sh。全程不用輸入，也不需要 sudo / root。
#
# 用法（比賽環境是 Ubuntu Server，預設有 curl）：
#   curl -fsSL https://raw.githubusercontent.com/xuanjgcarryyou/Elephant/main/bootstrap.sh | bash
# 沒有 curl 時的一行通用版（有 curl 用 curl，沒有就用 wget，再沒有就用 python3）：
#   U=https://raw.githubusercontent.com/xuanjgcarryyou/Elephant/main/bootstrap.sh; if command -v curl >/dev/null; then curl -fsSL $U; elif command -v wget >/dev/null; then wget -qO- $U; else python3 -c "import sys,urllib.request as u;sys.stdout.write(u.urlopen(sys.argv[1]).read().decode())" $U; fi | bash
#
# 有 git 就淺層 clone（之後可以 git pull）；沒有 git 就直接下載 GitHub 的 tarball，不必先裝 git。
# 可用環境變數覆寫：REPO、BRANCH、REPO_URL、TARBALL_URL、DEST
#
# 整支包在 main() 裡最後才呼叫：用 curl | bash 執行時，bash 會先讀完整支腳本再開始跑，
# 避免中途有指令讀 stdin 把腳本後半段吃掉。

main() {
    set -uo pipefail

    local REPO="${REPO:-xuanjgcarryyou/Elephant}"
    local BRANCH="${BRANCH:-main}"
    local REPO_URL="${REPO_URL:-https://github.com/$REPO.git}"
    local TARBALL_URL="${TARBALL_URL:-https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz}"
    local DEST="${DEST:-$HOME/Elephant}"

    have() { command -v "$1" &> /dev/null; }

    # fetch URL：下載到 stdout。依序用 curl → wget → python3，哪個有就用哪個
    fetch() {
        if have curl; then curl -fsSL --retry 2 --connect-timeout 15 "$1"
        elif have wget; then wget -qO- "$1"
        elif have python3; then
            # 換掉預設的 User-Agent「Python-urllib」：有些網站（例如 claude.ai）會擋
            python3 -c 'import shutil, sys, urllib.request as u; shutil.copyfileobj(u.urlopen(u.Request(sys.argv[1], headers={"User-Agent": "elephant-setup/1.0"}), timeout=60), sys.stdout.buffer)' "$1"
        else echo "❌ curl、wget、python3 都沒有，無法下載" >&2; return 1
        fi
    }

    # 從 stdin 解開 tar.gz 到 $1，去掉最上層目錄（GitHub tarball 的 Elephant-main/）
    untar_to() {
        mkdir -p "$1"
        if have tar && have gzip; then
            tar -xzf - --strip-components=1 -C "$1"
        else
            python3 -c '
import io, sys, tarfile
dest = sys.argv[1]
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()), mode="r:gz") as t:
    for m in t.getmembers():
        parts = m.name.split("/", 1)
        if len(parts) == 2 and parts[1]:
            m.name = parts[1]
            t.extract(m, dest)
' "$1"
        fi
    }

    # 下載 tarball 覆蓋 repo 內的檔案。data/、work/、1.txt、answer.md 不在 tarball 裡，不會被動到
    get_tarball() {
        echo "用 tarball 下載（不需要 git）"
        fetch "$TARBALL_URL" | untar_to "$DEST" \
            || { echo "❌ 下載 repo 失敗，檢查網路或 repo 是否為 public" >&2; return 1; }
        [ -f "$DEST/setup.sh" ] || { echo "❌ 下載內容不完整：找不到 setup.sh" >&2; return 1; }
    }

    echo "=== 取得 repo ==="
    if [ -d "$DEST/.git" ] && have git; then
        echo "$DEST 已存在，更新到最新版"
        git -C "$DEST" pull --ff-only -q || echo "⚠️  pull 失敗，沿用現有版本"
    elif have git && [ ! -e "$DEST" ]; then
        if ! git clone -q --depth 1 -b "$BRANCH" "$REPO_URL" "$DEST"; then
            echo "⚠️  git clone 失敗，改用 tarball"
            rm -rf "$DEST"
            get_tarball || return 1
        fi
    else
        # 沒有 git，或目錄是上次 tarball 解出來的
        get_tarball || return 1
    fi
    if [ -d "$DEST/.git" ]; then
        # 只設在這個 repo、不碰全域設定：萬一現場要 commit，不會被「Please tell me who you are」擋住
        [ -n "$(git -C "$DEST" config user.name)" ] || git -C "$DEST" config user.name contest
        [ -n "$(git -C "$DEST" config user.email)" ] || git -C "$DEST" config user.email contest@localhost
    fi

    echo ""
    echo "=== 交給 setup.sh ==="
    bash "$DEST/setup.sh"
}

main "$@"
