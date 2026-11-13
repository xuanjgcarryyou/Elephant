#!/usr/bin/env bash
# 2026金象杯 - 現場環境初始化腳本
# 用法: bash setup.sh
set -e

echo "=== 檢查 Node.js ==="
if ! command -v node &> /dev/null; then
    echo "找不到 node，開始安裝..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
node --version

echo "=== 安裝 Claude Code CLI ==="
npm install -g @anthropic-ai/claude-code

echo "=== 檢查 HDFS / Hadoop CLI ==="
if command -v hdfs &> /dev/null; then
    echo "找到 hdfs 指令"
    hdfs dfs -ls / || echo "⚠️ hdfs dfs -ls / 失敗，檢查連線或權限"
elif command -v hadoop &> /dev/null; then
    echo "找到 hadoop 指令"
    hadoop fs -ls / || echo "⚠️ hadoop fs -ls / 失敗，檢查連線或權限"
else
    echo "⚠️ 找不到 hdfs/hadoop CLI，需要現場確認資料存取方式（WebHDFS REST API？本機掛載？）"
fi

echo ""
echo "=== 安裝完成 ==="
echo "下一步：直接執行 'claude'，第一次互動會跳出瀏覽器 OAuth 登入連結，手動完成登入。"
