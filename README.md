# Elephant

2026 金象杯資料分析競賽準備用 repo。團隊賽、單人操作，比賽現場用 Claude Code CLI 直接讀 HDFS 資料做分析。

## 檔案

- `setup.sh` — 現場開局腳本：裝 Node.js、裝 Claude Code CLI、檢查 hdfs/hadoop CLI 是否存在。
  跑完後手動執行 `claude` 觸發瀏覽器 OAuth 登入（這步無法腳本化）。
- `prompt-template.md` — 解題固定提示詞模板，每題套用，附提交判斷原則。

## 比賽當天流程

1. 拿到 Ubuntu 環境，第一件事跑 `bash setup.sh`。
2. 跑完立刻手動執行 `claude` 完成登入驗證，不要先去看資料。
3. 確認 HDFS 實際存取方式（指令、路徑規則），把資料拉到本地。
4. 解題時套用 `prompt-template.md` 的格式，讓 Claude 一次回覆答案、假設、歧義版本、驗算結果。
5. 只有一種合理解讀才直接交；兩種算法數字不一致先查原因，不要提交。
