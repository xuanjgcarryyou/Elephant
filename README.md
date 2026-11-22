# Elephant

2026 金象杯資料分析競賽用。在比賽的 Linux 上裝好 Claude Code，讓它在這個 repo 裡讀 HDFS 資料、解題。

**預設環境**：官方在網頁上開的 **Ubuntu Server**（用網頁終端機操作）、**沒有 sudo 權限**、已經有 HDFS（`hdfs` 指令）。所有東西都裝在家目錄，不需要 root，也不需要 pip。

## 比賽當天步驟

1. **進入官方開好的 Ubuntu Server 網頁終端機。**

2. **貼上這一行安裝**（全程不用輸入）：

   ```bash
   curl -fsSL https://raw.githubusercontent.com/xuanjgcarryyou/Elephant/main/bootstrap.sh | bash
   ```

   萬一出現 `curl: command not found`，改貼這一行通用版。它會自己判斷有 curl、wget 還是 python3，用有的那個下載：

   ```bash
   U=https://raw.githubusercontent.com/xuanjgcarryyou/Elephant/main/bootstrap.sh; if command -v curl >/dev/null; then curl -fsSL $U; elif command -v wget >/dev/null; then wget -qO- $U; else python3 -c "import sys,urllib.request as u;sys.stdout.write(u.urlopen(sys.argv[1]).read().decode())" $U; fi | bash
   ```

   安裝時，Claude Code、Python 環境、HDFS 檢查三件事同時進行，大約 20～30 秒。HDFS 一連上，就在背景開始把資料下載到 `~/Elephant/data/`，並先看過每份資料。

3. **看安裝最後的問題清單**：顯示「全部完成，沒有問題」就繼續；有 ⚠️ 的先處理。

4. **啟動 Claude**：整行一起貼上，不要拆成兩行。

   ```bash
   source ~/.bashrc; cd ~/Elephant && claude
   ```

5. **登入**：
   - 複製終端機印出的網址，貼到瀏覽器新分頁打開。網址很長會折行，要整段選到。
   - 登入後，把驗證碼貼回終端機。網頁終端機的貼上通常是 Ctrl+Shift+V 或按右鍵。

網頁終端機的複製貼上常常不完整，所以登入之後，**跟 Claude 只講兩次話：「開始」和「go」**。題目和答案都透過檔案交換，不在 Claude 的畫面上複製貼上。Claude 不會停下來問問題或等你按允許：權限設定是 `dontAsk`，白名單外的操作會直接拒絕，它會自己改用別的方法。

6. **跟 Claude 說「開始」**：Claude 會把 HDFS 的資料集拉完（大多在登入時就已經下載好了），讀懂每份資料，寫好資料筆記和讀資料用的程式。完成時會說「可以 go 了」。

7. **同時另開一個終端機分頁寫題目**：

   ```bash
   nano ~/Elephant/1.txt
   ```

   把第 1～10 題貼進去，按 Ctrl+O、Enter 存檔，再按 Ctrl+X 離開。setup 已經確認過 nano 可以用：系統沒有的話，會不用 sudo 裝到家目錄。

8. **回到 Claude 說「go」**：Claude 會同時開 5 個 subagent 分頭算，每題都用兩種獨立寫法驗算，全部算完後一次寫進 `answer.md`。

9. **在另一個分頁看答案**，照開頭的總表提交：

   ```bash
   nano ~/Elephant/answer.md
   ```

   判斷方式：
   - ✅ 只有一種合理解讀 → 直接交
   - ⚠️ 有分歧 → 自己判斷採用哪個版本，或先交總表裡的版本（機率最高的那個）
   - ❌ 兩種獨立算法數字不一致 → 不要交，先查原因

## 出狀況時

| 狀況 | 處理 |
|---|---|
| 分頁關掉或斷線 | 重新進入後執行 `cd ~/Elephant && claude -c`，會接回上一段對話 |
| `claude: command not found` | 先執行 `source ~/.bashrc` 再試 |
| 貼上沒反應 | 改用 Ctrl+Shift+V 或按右鍵貼上 |
| repo 有更新、要重新安裝 | 重貼步驟 2 那一行：會先拉最新版，再重跑安裝（已裝好的會跳過，約 2 秒） |
| 想看背景下載的進度 | `cat ~/Elephant/work/prefetch.log` |
| 資料超過 5 GB，有些沒自動下載 | 跟 Claude 說要用哪份，它會執行 `python3 tools/prefetch.py <HDFS 路徑>` |
| `1.txt` 是亂碼 | 檔案是 Big5 編碼，Claude 會自己用 `iconv` 轉換，不用處理 |
| 題目改了 | 用 nano 改 `1.txt` 存檔，再跟 Claude 說一次「go」：只重做有變的題目 |
| nano 打不開 | 改用 `vi ~/Elephant/1.txt`（按 i 開始輸入，Esc 後打 `:wq` 存檔離開） |
| 想知道 Python 裝好了沒 | `grep Python ~/Elephant/work/env.md`：寫「還在背景安裝」就是還沒好。不用等它，直接說「開始」，Claude 會自己等 |

## 檔案

| 檔案 | 用途 |
|---|---|
| `bootstrap.sh` | 取得 repo（有 git 就 clone，沒有就下載 tarball）→ 執行 setup.sh |
| `setup.sh` | 同時安裝 Claude Code、Python 環境（uv venv，裝在 `~/.elephant-venv`），並檢查 HDFS。結果寫進 `work/env.md`，再在背景啟動 prefetch |
| `CLAUDE.md` | 給做題 Claude 的規則：只對話兩次（開始、go）、批次解題、驗算、`answer.md` 格式（啟動時自動讀入） |
| `docs/` | 參考資料：北中南定義、常見陷阱、HDFS 速查 |
| `tools/` | `tw.py`（縣市、分區、民國日期、四捨五入、數字清理）、`peek.py`（快速看一份資料）、`prefetch.py`（HDFS 平行下載與盤點） |
| `reference/taiwan_counties.csv` | `tw.py` 用的 22 縣市對照表 |
| `.claude/settings.json` | `dontAsk` 模式：白名單內直接執行、白名單外直接拒絕，不跳確認；HDFS 寫入、刪除一律禁止 |
| `1.txt`、`answer.md` | 比賽當天的題目和答案（不會被 commit） |
