#!/usr/bin/env bash
# 空環境第一支腳本：裝 git → clone 這個 repo → 執行 setup.sh。全程不需要任何輸入。
#
# 用法（環境有 curl）：
#   curl -fsSL https://raw.githubusercontent.com/xuanjgcarryyou/Elephant/main/bootstrap.sh | bash
# 連 curl 都沒有：見 README「空環境開局」手打版
#
# 可用環境變數覆寫：REPO_URL、DEST
#
# 整支包在 main() 裡最後才呼叫：用 curl | bash 執行時，bash 會先讀完整支腳本再開始跑，
# 避免中途有指令讀 stdin 把腳本後半段吃掉。

main() {
    set -uo pipefail

    local REPO_URL="${REPO_URL:-https://github.com/xuanjgcarryyou/Elephant.git}"
    local DEST="${DEST:-$HOME/Elephant}"

    have() { command -v "$1" &> /dev/null; }
    run_root() {
        if [ "$(id -u)" -eq 0 ]; then "$@"
        elif have sudo; then sudo "$@"
        else echo "❌ 需要 root 或 sudo 才能安裝套件：$*" >&2; return 1
        fi
    }

    echo "=== 1/2 安裝 git、curl ==="
    if ! have git || ! have curl || [ ! -f /etc/ssl/certs/ca-certificates.crt ]; then
        run_root apt-get update -qq || return 1
        run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates || return 1
    fi
    git --version

    echo ""
    echo "=== 2/2 取得 repo ==="
    if [ -d "$DEST/.git" ]; then
        echo "$DEST 已存在，更新到最新版"
        git -C "$DEST" pull --ff-only || echo "⚠️  pull 失敗，沿用現有版本"
    else
        git clone "$REPO_URL" "$DEST" || { echo "❌ clone 失敗，檢查網路或 repo 是否為 public" >&2; return 1; }
    fi
    # 只設在這個 repo、不碰全域設定：萬一現場要 commit，不會被「Please tell me who you are」擋住
    [ -n "$(git -C "$DEST" config user.name)" ] || git -C "$DEST" config user.name contest
    [ -n "$(git -C "$DEST" config user.email)" ] || git -C "$DEST" config user.email contest@localhost

    echo ""
    echo "=== 交給 setup.sh ==="
    bash "$DEST/setup.sh"
}

main "$@"
