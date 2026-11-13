# Elephant

2026 金象杯資料分析競賽用。比賽現場在空的 Linux 上裝好 Claude Code，讓它在這個 repo 裡讀 HDFS 資料、解題。

## 開局

repo 是 public，不需要任何 git 設定或登入，全程不用輸入。

**有 curl**：

```bash
curl -fsSL https://raw.githubusercontent.com/xuanjgcarryyou/Elephant/main/bootstrap.sh | bash
```

**沒有 curl**（root 環境把 sudo 拿掉）：

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/xuanjgcarryyou/Elephant.git ~/Elephant && bash ~/Elephant/bootstrap.sh
```

跑完後看最後的問題清單，有 ⚠️ 的先處理，然後貼上這一行（不要拆成 `exec bash -l` 加下一行，一起貼上時第二行會被吃掉）：

```bash
source ~/.bashrc; cd ~/Elephant && claude
```

**登入**：`claude` 會印出網址，複製到瀏覽器新分頁登入，再把驗證碼貼回終端機。

- 網頁終端機的貼上常常是 Ctrl+Shift+V 或按右鍵。
- 網址很長會折行，要整段選到。
- 分頁關掉或斷線：重新進入後執行 `cd ~/Elephant && claude -c`，會接回上一段對話。

## 解題

1. 第一句話說「開局」：Claude 會盤點 HDFS、把資料拉到 `data/`、寫好 `work/data-notes.md`。
2. 每題直接貼題目原文：Claude 會回答案、假設、歧義和驗算，並記到 `answers.md`。

提交判斷：

- 只有一種合理解讀 → 直接交
- 有分歧 → 自己判斷採用哪個版本，或先交機率最高的版本
- 兩種獨立算法數字不一致 → 不要交，先查原因

## 檔案

| 檔案 | 用途 |
|---|---|
| `bootstrap.sh` | 裝 git → clone → 執行 setup.sh |
| `setup.sh` | 裝 Claude Code、Python 套件，檢查 HDFS，結果寫進 `work/env.md` |
| `CLAUDE.md` | 給做題 Claude 的規則：開局、解題方式、回覆格式（啟動時自動讀入） |
| `docs/` | 遇到才讀的參考：北中南定義、常見陷阱、HDFS 速查 |
| `tools/` | `tw.py`（縣市、分區、民國日期、四捨五入、數字清理）、`peek.py`（快速看一份資料） |
| `reference/taiwan_counties.csv` | `tw.py` 用的 22 縣市對照表 |
| `.claude/settings.json` | 常用指令免確認；HDFS 寫入、刪除一律禁止 |
