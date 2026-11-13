# 彩排交接：用 Docker 模擬比賽現場

> 給：使用者家裡電腦上的 Claude Code。你沒有之前的對話脈絡，這份文件就是全部的交接內容。

## 先搞清楚你的角色

- 這個 repo 是使用者為「2026 金象杯資料分析競賽」準備的。比賽現場會拿到一台 Linux（預期是 Ubuntu），資料放在 HDFS，使用者要在那台機器上用 Claude Code 分析資料、解題。團隊賽、單人操作，有時間壓力。
- **repo 根目錄的 `CLAUDE.md` 是寫給「比賽現場那台機器裡的 Claude」看的，不是給你的。** 你在 repo 附近啟動時可能會讀到它，但不要照它的「開局」「解題」流程行動。
- 你的角色是**彩排導演**：用 Docker 建一個模擬的比賽環境，照比賽當天的流程從頭跑一次，記錄每一步花多久、哪裡出錯，然後修 repo。

## 已經驗證過的、還沒驗證的

已驗證（在 WSL 上）：

- `tools/tw.py` 自我測試
- `tools/peek.py` 能讀 cp950 CSV、Hive 分區 parquet、Hive textfile、多列表頭 xlsx
- `bootstrap.sh` 沙盒測試（`setup.sh` 用 stub 代替）
- 兩支 bash 腳本的語法檢查

**還沒驗證，也就是這次彩排的重點**：

1. `setup.sh` 在乾淨的 Ubuntu 上從頭跑完：apt、Claude Code 官方安裝腳本、pip（Ubuntu 24.04 有 PEP 668 限制）、HDFS 偵測、產生 `work/env.md`
2. 容器裡沒有瀏覽器時，Claude Code 的登入流程
3. 容器裡的 Claude 有沒有讀到 `CLAUDE.md`；`.claude/settings.json` 的權限白名單夠不夠用（會不會一直跳確認）
4. 「開局」流程的實際效果：盤點 HDFS → 下載 → 跑 `peek.py` → 寫 `work/data-notes.md`
5. 解題品質：答案對不對、有沒有列出歧義（特別是北中南）、驗算是不是真的用獨立寫法

## 比賽當天的流程（要照這個彩排）

```bash
# 環境有 curl
curl -fsSL https://raw.githubusercontent.com/xuanjgcarryyou/Elephant/main/bootstrap.sh | bash

# 沒有 curl（非 root 的話前面加 sudo）
apt-get update && apt-get install -y git
git clone https://github.com/xuanjgcarryyou/Elephant.git ~/Elephant && bash ~/Elephant/bootstrap.sh

# 之後
exec bash -l
cd ~/Elephant && claude      # 登入 → 說「開局」→ 逐題貼題目
```

## 工作目錄

- 彩排用的東西（Dockerfile、假資料、標準答案）**放在 repo 外面**，例如跟 repo 同一層的 `elephant-rehearsal/`，不要 commit。原因是容器裡會 clone 這個 repo，標準答案如果在 repo 裡，容器內的 Claude 有可能看到。
- 容器一律從 GitHub clone，所以修完 repo 要 push，下一輪才會生效。

## 步驟

### 0. 確認主機

- 先確認 `docker version` 能跑。沒有的話，引導使用者安裝 Docker Desktop，裝好再繼續。
- 主機是 Windows、在 Git Bash 下跑 `docker exec` 時，參數裡 `/` 開頭的路徑會被自動改寫。指令前面加 `MSYS_NO_PATHCONV=1` 就能避免。

### 1. 第一輪：完全空的 Ubuntu（沒有 HDFS）

目的：確認 bootstrap 加上 setup 在最差的情況下也能跑完。

```bash
docker run -d --name rh1 ubuntu:24.04 sleep infinity
time docker exec rh1 bash -c 'apt-get update && apt-get install -y git && git clone https://github.com/xuanjgcarryyou/Elephant.git ~/Elephant && bash ~/Elephant/bootstrap.sh'
```

通過條件：

- setup 最後的問題清單只有「找不到 hdfs/hadoop CLI」這一項
- `docker exec rh1 bash -lc 'claude --version && cd ~/Elephant && python3 tools/tw.py && cat work/env.md'` 全部正常

接著換兩種變化各跑一次，每次都開新的容器：

- **有 curl 的版本**：容器裡先 `apt-get install -y curl`，再用 curl 一行版。
- **非 root 加 sudo**：比賽現場很可能不是 root。先 `apt-get install -y sudo`，再 `useradd -m -s /bin/bash player`，把 player 加進 sudoers 並設 NOPASSWD，然後用 `docker exec -u player` 跑。

### 2. 第二輪：有 HDFS 的完整模擬

建一個映像檔 `elephant-contest` 來模擬比賽現場：

- 基底用 `ubuntu:24.04`
- **要有** Java 和 Hadoop 3.x，`hdfs` 指令在 PATH 上。容器啟動時以單機模式啟動 NameNode 和 DataNode（`fs.defaultFS=hdfs://localhost:9000`、replication=1）
- **不要有** git、curl、python3。做法是 multi-stage build：在 builder stage 下載 Hadoop，最終映像檔只複製解壓後的目錄
- 預設使用者是非 root 的 `player`，有 sudo 權限
- 啟動時把假資料放進 HDFS 的 `/data/contest/`

Hadoop 的注意事項：

- Hadoop 3 用 root 啟動 daemon 時，需要設 `HDFS_NAMENODE_USER`、`HDFS_DATANODE_USER`；或者乾脆用 player 啟動
- `JAVA_HOME` 要寫進 `hadoop-env.sh`
- tarball 從 `https://archive.apache.org/dist/hadoop/common/` 下載，檔案有幾百 MB 到 1GB，只下載一次就好

**假資料**：寫一支產生腳本，**同時用另一種獨立的方法算好標準答案**，存成 `expected.md`（放在 repo 外面）。至少要涵蓋這些陷阱：

| 資料 | 怎麼做 | 測什麼 |
|---|---|---|
| 縣市別統計 CSV | cp950 編碼；台／臺混用；有「總計」列；民國年月寫成 `113/05`；數字有千分位；缺值寫成 `-` | 編碼、合計列、縣市正規化、北中南分區 |
| 每日銷售 parquet | Hive 分區目錄 `dt=2024-01-01/`；附上 `_SUCCESS` 檔 | 分區欄位會不會不見 |
| 交易明細 Hive textfile | `\x01` 分隔、`\N` 代表缺值、沒有表頭、分成多個 part 檔 | 格式判斷 |

**題目**：寫成比賽題目的口吻，不要提示有陷阱。

1. 「113 年北部、中部、南部、東部各區的 X 合計為何？」澎湖算南部還是離島、金馬放在哪裡，答案都會不一樣。期望 Claude 用預設定義作答，並在「歧義」列出其他定義的答案。
2. 一題需要用到分區欄位 `dt` 的題目。
3. 一題平均值剛好在小數第三位是 5 的題目（例如 2.675）。期望結果是四捨五入，而不是 Python 預設的「四捨六入五成雙」。
4. 一題類似「平均失業率」的比率題。期望 Claude 同時列出簡單平均和加權平均。
5. 要求它刪掉 HDFS 上的檔案。期望被權限設定擋下來。

### 3. 跑比賽流程

```bash
docker run -d --name rh2 elephant-contest
docker exec -it -u player rh2 bash -l     # 使用者親手在這裡打比賽當天的指令
```

- 使用者在容器裡照「比賽當天的流程」親手打指令。容器裡沒有 curl，所以要走手打兩行的版本。你在旁邊計時、記錄。
- **登入只能由使用者自己完成**：容器裡沒有瀏覽器，`claude` 會印出一個網址。使用者用主機的瀏覽器打開、登入，再把驗證碼貼回容器。
- 登入完成後，你也可以從主機直接驅動容器內的 Claude，重複測試同樣的流程：

  ```bash
  docker exec -u player rh2 bash -lc 'cd ~/Elephant && claude -p "開局"'
  docker exec -u player rh2 bash -lc 'cd ~/Elephant && claude -c -p "<題目>"'
  ```

  在 `-p` 模式下，不在白名單裡的工具會直接被拒絕。這剛好可以用來找出 `.claude/settings.json` 還缺哪些規則。

### 4. 要記錄的東西

- 每一步的耗時：bootstrap、setup 的各個階段、登入、開局、每一題
- setup 最後的問題清單，以及 `work/env.md` 的內容
- 容器內 Claude 被拒絕或跳出確認的指令（這些是要補進白名單的候選）
- 每題的答案和標準答案的對照；有沒有列出歧義；驗算是不是獨立寫法；有沒有寫進 `answers.md`、有沒有產生 `work/q*.py`

## 修 repo 的規則

以下是刻意的設計，不要去「修正」它們：

- `setup.sh` 不用 `set -e`：每一步失敗都繼續往下跑，最後再統一列出問題
- Claude Code 用官方安裝腳本安裝，npm 只是備案
- Python 套件用 `pip install --user` 安裝，失敗才加 `--break-system-packages`。如果在 24.04 上這條路真的不通，再考慮改用 venv
- `CLAUDE.md` 保持精簡，細節放在 `docs/`
- `reference/` 裡沒有縣市代碼，北中南「三大生活圈」也沒有逐縣市的對照，原因都是找不到可靠的官方來源

其他規則：

- 註解和文件用繁體中文；檔案一律用 LF 換行（`.gitattributes` 已經設定好）
- 改完之後跑 `bash -n setup.sh && bash -n bootstrap.sh && python3 tools/tw.py`
- commit 和 push 之前先問使用者。push 之後要開**新的容器**重跑驗證，不能在舊容器上修修補補就當作通過

## 結束時交給使用者

1. 時間軸表格：步驟、耗時、結果
2. 發現的問題，以及對應的修正（附 commit）
3. 還沒解決的風險
4. 比賽當天的流程需不需要調整的建議

彩排結束後，用 `docker rm -f` 刪掉容器，因為裡面存有使用者的 Claude 登入憑證。
