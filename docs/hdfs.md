# HDFS 速查

**HDFS 上的東西只能讀，絕對不能刪、搬或寫。** `.claude/settings.json` 已經擋掉 rm、mv、put 等指令。

下面的 `hdfs dfs` 都可以換成 `hadoop fs`，用法相同。

## 盤點

```bash
hdfs dfs -ls /                         # 從根目錄開始找
hdfs dfs -ls -R /path | head -50       # 遞迴列出（大目錄記得 head）
hdfs dfs -du -h -s /path               # 總大小：決定要全部下載還是只抽樣
hdfs dfs -count -h /path               # 目錄數、檔案數、大小
hdfs dfs -cat /path/file.csv | head    # 直接偷看開頭
hdfs dfs -text /path/file.gz | head    # 壓縮檔、SequenceFile 用 -text，會自動解壓
```

常見的資料位置：`/user/<帳號>/`、`/data/`、`/user/hive/warehouse/<db>.db/<table>/`。

## 下載

```bash
hdfs dfs -get /path/dir data/                         # 保留目錄結構
hdfs dfs -getmerge /path/dir/ data/merged.csv         # 把多個 part 檔合成一個
```

用 `-getmerge` 合併時，**每個 part 檔如果都有表頭，合併後中間會夾雜表頭列**，要記得清掉。用 `peek.py` 讀整個目錄就不會有這個問題。

## 檔案格式

| 看到的檔名或樣子 | 格式 | 讀法 |
|---|---|---|
| `part-00000-*.parquet` 或 `*.snappy.parquet` | Parquet | `pd.read_parquet("data/dir/")` |
| `*.orc`，或沒有副檔名、開頭是 `ORC` | ORC | `pyarrow.dataset.dataset(path, format="orc")` |
| `part-00000`，用 `\x01` 分隔 | Hive textfile | `pd.read_csv(f, sep="\x01", header=None, na_values=["\\N"])` |
| `*.gz`、`*.bz2` | 壓縮文字 | pandas 會自動解壓 |
| 目錄名稱是 `dt=2024-01-01/` | Hive 分區 | 見下方說明 |

**Hive 分區**：分區欄位（例如 `dt`、`city`）不在檔案裡，只存在目錄名稱上。要讀整個目錄，並加上 `partitioning="hive"`，例如 `pyarrow.dataset.dataset("data/tbl", format="parquet", partitioning="hive")`，否則分區欄位會消失。

**Hive textfile 沒有表頭**：欄位名稱要從 `DESCRIBE` 拿；如果有 `beeline` 或 `hive`，可以先查 schema：

```bash
beeline -u "jdbc:hive2://<host>:10000" -e "DESCRIBE FORMATTED db.table"
```

用 duckdb 讀 Hive textfile（例如當作驗算的第二種寫法）。SQL 要放在一般字串，不能用 `r"""`，`'\x01'` 才會是真正的控制字元；duckdb 2.x 的查詢結果用 `.fetchall()` 或 `.df()` 取，不能用 `.fetchone()`：

```python
duckdb.sql("""
    SELECT c2, SUM(c3) FROM read_csv('data/tbl/part-*', delim = '\x01', header = false, nullstr = '\\N', quote = '',
                                     columns = {'c1': 'VARCHAR', 'c2': 'VARCHAR', 'c3': 'INTEGER'})
    GROUP BY 1
""").fetchall()
```

## 大檔案

- 先看 `-du -h`。幾 GB 以內，直接下載用 pandas。
- 再大的話，用 duckdb 直接查 parquet 或 csv，只讀需要的欄位：
  `duckdb.sql("SELECT city, SUM(x) FROM 'data/dir/*.parquet' GROUP BY 1").df()`
- 環境裡有 `spark-sql` 或 `pyspark` 時，也可以直接在叢集上算（`work/env.md` 裡有列出可用的工具）。

## 疑難排解

| 症狀 | 處理 |
|---|---|
| 卡住很久才報 `Connection refused` | NameNode 連不上。檢查 `hdfs getconf -confKey fs.defaultFS` 和網路 |
| `GSS initiate failed`、`No valid credentials` | 需要 Kerberos：先 `klist`，沒有票就 `kinit <帳號>@<REALM>` |
| `JAVA_HOME is not set` | 執行 `export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))` |
| `Permission denied` | 換到有權限的路徑，或確認 `HADOOP_USER_NAME` |
| 完全沒有 hdfs 指令 | 改用 WebHDFS，見下方 |

## WebHDFS（沒有 CLI 時）

NameNode 的 HTTP port：Hadoop 3 是 9870，Hadoop 2 是 50070。

```bash
NN=http://<namenode>:9870
curl -s "$NN/webhdfs/v1/<路徑>?op=LISTSTATUS&user.name=<帳號>" | python3 -m json.tool
curl -sL "$NN/webhdfs/v1/<路徑>/file.csv?op=OPEN&user.name=<帳號>" -o data/file.csv
```

`OPEN` 會 redirect 到 DataNode；如果 DataNode 的主機名稱解析不到，要在 `/etc/hosts` 加上對應的 IP。
