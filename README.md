# Elephant

2026 金象杯資料分析競賽準備用 repo。團隊賽、單人操作，比賽現場用 Claude Code CLI 直接讀 HDFS 資料做分析。

設計分三層：

| 層 | 負責 | 檔案 |
|---|---|---|
| 環境 | 空的 Linux → 有 git → 拿到 repo | `bootstrap.sh` |
| 工具 | 裝 Claude Code、Python 套件，檢查 HDFS | `setup.sh` |
| 知識 | 讓 Claude 一啟動就知道怎麼解題、哪裡會踩坑 | `CLAUDE.md`、`docs/`、`tools/`、`reference/` |

## 空環境開局

repo 是 public，clone 前不需要任何 git 設定或登入，全程不用輸入。

**環境有 curl**：一行搞定（裝 git → clone → 執行 setup.sh）

```bash
curl -fsSL https://raw.githubusercontent.com/xuanjgcarryyou/Elephant/main/bootstrap.sh | bash
```

**連 curl 都沒有**：手打兩行（root 環境把 sudo 拿掉）

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/xuanjgcarryyou/Elephant.git ~/Elephant && bash ~/Elephant/bootstrap.sh
```

跑完之後：

```bash
source ~/.bashrc; cd ~/Elephant && claude    # 重新載入 PATH；一定要在 repo 目錄啟動，才會讀到 CLAUDE.md 和權限設定
```

不要改成 `exec bash -l` 再接一行 `claude`：兩行一起貼上時，`exec` 換掉 shell 之後，第二行會被吃掉。

登入：有瀏覽器會自動開啟；SSH 或遠端環境會印出一個網址，到別台機器打開登入，再把驗證碼貼回來。

## 比賽當天流程

1. 照上面開局，看 setup.sh 最後的問題清單，有 ⚠️ 的先處理。
2. 跟 Claude 說「開局」：它會讀環境紀錄、盤點 HDFS、把資料拉到 `data/`、逐份跑 `peek.py`，然後寫好 `work/data-notes.md`。
3. 每題直接貼題目原文。Claude 會依 `CLAUDE.md` 回覆答案、假設、歧義版本和驗算結果，並記到 `answers.md`。

## 檔案

- `bootstrap.sh`：裝 git、curl → clone 或 pull → 執行 setup.sh，全程不需要輸入
- `setup.sh`：安裝基礎套件，設定語系與時區；裝 Claude Code（官方安裝腳本，失敗時改用 npm）；裝 Python 套件（pandas、pyarrow、openpyxl、xlrd、duckdb）；檢查 HDFS；把偵測結果寫進 `work/env.md`
- `CLAUDE.md`：開局 SOP、解題方式、回覆格式。Claude 啟動時會自動讀入
- `docs/taiwan-regions.md`：北中南東的各種官方定義、差異、決策規則、縣市改制
- `docs/pitfalls.md`：四捨五入、民國年、合計列、編碼、比率平均等常見陷阱
- `docs/hdfs.md`：HDFS 指令、檔案格式、Hive 分區、疑難排解、WebHDFS
- `tools/tw.py`：縣市正規化、分區、民國日期、四捨五入、數字清理（`python3 tools/tw.py` 會跑自我測試）
- `tools/peek.py`：一個指令看完一份資料：編碼、欄位、缺值、疑似陷阱、合計列
- `reference/taiwan_counties.csv`：22 縣市對照表，包含國發會分區、七大生活圈、是否離島、舊名
- `.claude/settings.json`：常用指令免確認；HDFS 的寫入、刪除、搬移一律禁止
- `rehearsal/HANDOFF.md`：賽前彩排用，給另一台電腦上的 Claude 看：怎麼用 Docker 模擬比賽現場、從頭跑一次整個流程
- `rehearsal/REPORT-2026-11-13.md`：第一次彩排的結果：時間軸、答案對照、發現的問題與修正、還沒解決的風險
- `rehearsal/` 其他檔案：第一次彩排用的映像檔設定（`contest-image/`）、假資料產生器（`fakedata/`）、題目、標準答案、驅動腳本（`scripts/`）與原始紀錄（`logs/`）

## 提交判斷原則

- 只有一種合理解讀 → 直接交
- 有分歧 → 自己判斷採用哪個版本，或先交機率最高的版本
- 兩種獨立算法數字不一致 → 不要交，先查原因
