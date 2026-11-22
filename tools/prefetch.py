#!/usr/bin/env python3
"""HDFS 資料預先下載：盤點 → 平行下載到 data/ → 每份資料先跑好 peek.py。只讀 HDFS，不會寫入。

setup.sh 在 HDFS 連上時會自動在背景執行 --auto，使用者登入 Claude 的同時資料就在下載。

用法：
    python3 tools/prefetch.py --wait              # 等背景下載完成，印出 work/inventory.md（「開始」時用）
                                                  # 背景沒有成功下載的話，會自己現在下載
    python3 tools/prefetch.py /hdfs/路徑 [...]    # 手動下載指定路徑（不受自動上限；已下載的會跳過）
    python3 tools/prefetch.py --auto              # 自動盤點根目錄下的資料並下載（setup.sh 用）
    python3 tools/prefetch.py --auto --list-only  # 只盤點、不下載

本機路徑 = data/ + HDFS 路徑，例如 /data/contest/sales → data/data/contest/sales
產出：work/inventory.md（清單）、work/peek/*.txt（每份資料的 peek 結果）、work/prefetch.log（過程）
環境變數：PREFETCH_MAX_GB（--auto 自動下載的總量上限，預設 5）、PREFETCH_THREADS（每個 -get 的執行緒數，預設 16）

為了快：整個 HDFS 只 ls -R 一次；能整個目錄一起下載的就合併成同一個 -get（每個 -get 都要啟動一次 JVM），
-get 用多執行緒（Hadoop 3.3+ 的 -t），不同目錄的 -get 同時跑，peek 也平行跑。
只用標準函式庫，系統內建的舊版 Python（例如 3.8）也能跑；peek 那一步才需要 pandas。
"""
import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
WORK = os.path.join(ROOT, "work")
PEEK_DIR = os.path.join(WORK, "peek")
STATUS = os.path.join(WORK, "prefetch.status")
DB = os.path.join(WORK, "prefetch.json")
INVENTORY = os.path.join(WORK, "inventory.md")
ROOT_LISTING = os.path.join(WORK, "hdfs-root.txt")   # setup.sh 存的 `hdfs dfs -ls /`
PEEK = os.path.join(ROOT, "tools", "peek.py")

MB = 1 << 20
GB = 1 << 30
MAX_BYTES = int(float(os.environ.get("PREFETCH_MAX_GB", "5")) * GB)
THREADS = int(os.environ.get("PREFETCH_THREADS", "16"))
PARALLEL_GETS = 4
TINY = MB                 # 小於這個大小的系統檔／暫存檔跟著目錄一起下載也無害（例如 _SUCCESS）
LIST_TIMEOUT = 180
LIST_MAX_LINES = 500000
PEEK_BIG = 64 * MB        # 超過就只讓 peek 讀前 20 萬列

# 自動模式不下載的系統目錄
SYS_RE = re.compile(
    r"^/(tmp|system|mr-history|app-logs|spark-history|spark2-history|spark-logs|hbase|benchmarks"
    r"|yarn|hadoop-yarn|ats|mapred|var)(/|$)"
    r"|^/user/(history|oozie|spark|yarn|mapred|hbase|ambari-qa|livy|zeppelin)(/|$)"
)
# 任何模式都不當成資料：隱藏／暫存（.Trash .staging .hive-staging _temporary _SUCCESS，但 key=value 分區除外）
JUNK_RE = re.compile(r"/[._][^/=]*(/|$)|\.(crc|jar)$|_COPYING_$")
DOC_RE = re.compile(r"^(readme|schema|欄位|說明)|\.(md|pdf|docx?|sql|ddl|hql|html?)$", re.I)
TABLE_FILE_RE = re.compile(r"^(part-|\d{6}_\d)|\.(parquet|orc|avro)$", re.I)
# hdfs dfs -ls 的一行：權限 副本 擁有者 群組 大小 日期 時間 路徑（群組可能有空白，所以從日期往回抓大小）
LS_RE = re.compile(r"^([dl-])\S*\s.*?\s(\d+)\s+\d{4}-\d\d-\d\d\s+\d\d:\d\d\s+(\S.*)$")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def rel(p):
    return os.path.relpath(p, ROOT)


def local_path(hdfs_path):
    return os.path.join(DATA, hdfs_path.lstrip("/"))


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def set_status(state, msg):
    write_json(STATUS, {"state": state, "msg": msg, "pid": os.getpid(), "updated": time.time()})
    log("[%s] %s" % (state, msg))


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def hdfs_base():
    if shutil.which("hdfs"):
        return ["hdfs", "dfs"]
    if shutil.which("hadoop"):
        return ["hadoop", "fs"]
    raise SystemExit("找不到 hdfs / hadoop 指令")


def parse_ls_line(line):
    m = LS_RE.match(line.rstrip("\n"))
    if not m:
        return None
    path = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*", "", m.group(3)) or "/"
    return path, m.group(1) == "d", int(m.group(2))


# ---------------------------------------------------------------------------
# 盤點

class Tree:
    def __init__(self):
        self.info = {}                  # 路徑 → (是目錄, 大小)
        self.kids = defaultdict(list)
        self._size = {}

    def add(self, path, is_dir, size):
        if path in self.info:
            self.info[path] = (is_dir, size)
            return
        self.info[path] = (is_dir, size)
        parent = posixpath.dirname(path)
        if parent != path:
            self.kids[parent].append(path)

    def is_dir(self, p):
        return self.info[p][0]

    def size(self, p):
        """整個子樹的大小（包含系統檔、暫存檔）。"""
        if p not in self._size:
            self._size[p] = sum(self.size(k) for k in self.kids[p]) if self.is_dir(p) else self.info[p][1]
        return self._size[p]

    def files_under(self, p, skip):
        if skip(p):
            return []
        if not self.is_dir(p):
            return [p]
        out = []
        for k in self.kids[p]:
            out.extend(self.files_under(k, skip))
        return out


def list_recursive(base, roots, tree, problems):
    """一次 ls -R 所有根目錄；超過時間或行數上限就停，清單標為不完整。"""
    for r in roots:
        tree.add(r, True, 0)
    err_path = os.path.join(WORK, "prefetch-ls.err")
    with open(err_path, "w") as err:
        p = subprocess.Popen(base + ["-ls", "-R"] + roots, stdout=subprocess.PIPE, stderr=err,
                             universal_newlines=True, encoding="utf-8", errors="replace")
        timer = threading.Timer(LIST_TIMEOUT, p.kill)
        timer.start()
        n = 0
        for line in p.stdout:
            parsed = parse_ls_line(line)
            if parsed:
                tree.add(*parsed)
                n += 1
                if n >= LIST_MAX_LINES:
                    p.kill()
                    problems.append("清單超過 %d 筆，只盤點到這裡；其餘路徑請手動 `hdfs dfs -ls`" % LIST_MAX_LINES)
                    break
        p.wait()
        timer.cancel()
    if p.returncode != 0 and not problems:
        with open(err_path, encoding="utf-8", errors="replace") as f:
            tail = [l.strip() for l in f if l.strip() and "WARN" not in l][-3:]
        if p.returncode in (-9, 137):
            problems.append("ls -R 超過 %d 秒被中止，清單可能不完整" % LIST_TIMEOUT)
        elif tail:
            problems.append("ls -R 有錯誤：" + " / ".join(tail))
    return n


def get_flags(base):
    """這個 Hadoop 版本的 -get 支援哪些選項（-t 多執行緒是 3.3+ 才有）。"""
    try:
        out = subprocess.run(base + ["-usage", "get"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    flags = []
    if "[-f]" in out:
        flags.append("-f")
    if "-t <thread" in out:
        flags += ["-t", str(THREADS)]
    return flags


def auto_roots(base, problems):
    """根目錄底下的項目，扣掉系統目錄。優先用 setup.sh 剛存的 ls / 結果，省一次 JVM 啟動。"""
    lines = None
    if os.path.exists(ROOT_LISTING) and time.time() - os.path.getmtime(ROOT_LISTING) < 1800:
        with open(ROOT_LISTING, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    if not lines:
        r = subprocess.run(base + ["-ls", "/"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, encoding="utf-8", errors="replace", timeout=90)
        if r.returncode != 0:
            raise RuntimeError("hdfs -ls / 失敗：" + r.stderr.strip()[-300:])
        lines = r.stdout.splitlines()
    roots, skipped = [], []
    for line in lines:
        parsed = parse_ls_line(line)
        if not parsed:
            continue
        path = parsed[0]
        (skipped if SYS_RE.search(path) or JUNK_RE.search(path) else roots).append(path)
    return roots, skipped


def classify(tree, p, skip, datasets, docs):
    """找出一份份資料：分區表或 part 檔目錄算一份；同一系列的檔案（只差數字）算一份；其餘每個檔案各算一份。"""
    if skip(p):
        return
    name = posixpath.basename(p)
    if not tree.is_dir(p):
        if DOC_RE.search(name):
            docs.append({"path": p, "kind": "doc"})
        else:
            datasets.append({"path": p, "kind": "file"})
        return
    kids = [k for k in tree.kids[p] if not skip(k)]
    files = [k for k in kids if not tree.is_dir(k)]
    dirs = [k for k in kids if tree.is_dir(k)]
    data_files = [f for f in files if not DOC_RE.search(posixpath.basename(f))]
    partitioned = any("=" in posixpath.basename(d) for d in dirs)
    part_files = bool(data_files) and all(TABLE_FILE_RE.search(posixpath.basename(f)) for f in data_files)
    series = len(data_files) >= 2 and not dirs and \
        len({re.sub(r"\d+", "#", posixpath.basename(f)) for f in data_files}) == 1
    if partitioned or part_files or series:
        datasets.append({"path": p, "kind": "table"})
        for f in files:
            if f not in data_files:
                docs.append({"path": f, "kind": "doc"})
        return
    for k in files + dirs:
        classify(tree, k, skip, datasets, docs)


def describe(tree, d, skip):
    files = tree.files_under(d["path"], skip)
    if d["kind"] == "table":   # 資料表目錄裡的說明檔另外列在 docs
        files = [f for f in files if not DOC_RE.search(posixpath.basename(f))]
    d["files"] = files
    d["nfiles"] = len(files)
    d["bytes"] = sum(tree.info[f][1] for f in files)
    if d["kind"] == "table":
        keys = []
        for f in files:
            for part in f[len(d["path"]):].split("/"):
                if "=" in part:
                    k = part.split("=", 1)[0]
                    if k not in keys:
                        keys.append(k)
        exts = sorted({os.path.splitext(posixpath.basename(f))[1].lower() or "無副檔名" for f in files})
        d["fmt"] = "資料表（%s）" % "、".join(exts[:3]) + ("，分區：" + "、".join(keys) if keys else "")
    else:
        d["fmt"] = os.path.splitext(d["path"])[1].lower() or "無副檔名"


# ---------------------------------------------------------------------------
# 下載

def plan_downloads(tree, roots, wanted):
    """把要下載的檔案合併成最少的 -get：整個目錄都要的就整個目錄一起抓。回傳 {本機目的目錄: [HDFS 來源…]}。"""
    has_memo, full_memo = {}, {}

    def has_wanted(p):
        if p not in has_memo:
            has_memo[p] = any(has_wanted(k) for k in tree.kids[p]) if tree.is_dir(p) else p in wanted
        return has_memo[p]

    def harmless(p):
        return not has_wanted(p) and tree.size(p) < TINY

    def full(p):
        if p not in full_memo:
            if not tree.is_dir(p):
                full_memo[p] = p in wanted
            else:
                full_memo[p] = all(full(k) or harmless(k) for k in tree.kids[p])
        return full_memo[p]

    def plan(p):
        if not has_wanted(p):
            return []
        if not tree.is_dir(p) or full(p):
            return [p]
        out = []
        for k in tree.kids[p]:
            out.extend(plan(k))
        return out

    groups = defaultdict(list)
    for r in roots:
        if r in tree.info:
            for src in plan(r):
                groups[local_path(posixpath.dirname(src))].append(src)
    return groups


def local_complete(tree, src, wanted):
    files = [f for f in tree.files_under(src, lambda p: False) if f in wanted]
    for f in files:
        lp = local_path(f)
        if not os.path.isfile(lp) or os.path.getsize(lp) != tree.info[f][1]:
            return False
    return True


def run_get(base, flags, dest, srcs):
    os.makedirs(dest, exist_ok=True)
    errors = []
    for i in range(0, len(srcs), 200):     # 一次最多 200 個來源，避免參數過長
        chunk = srcs[i:i + 200]
        cmd = base + ["-get"] + flags + chunk + [dest.rstrip("/") + "/"]
        t0 = time.time()
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 and flags and "Illegal option" in r.stderr:
            r = subprocess.run(cmd[:len(base) + 1] + chunk + [dest.rstrip("/") + "/"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", errors="replace")
        msg = [l for l in r.stderr.splitlines() if l.strip() and "WARN" not in l and "INFO" not in l]
        log("get %d 個來源 → %s：%s（%.1f 秒）" % (len(chunk), rel(dest), "ok" if r.returncode == 0 else "失敗",
                                                time.time() - t0))
        if r.returncode != 0:
            errors.append("；".join(msg[-3:]) or "exit %d" % r.returncode)
    return errors


# ---------------------------------------------------------------------------
# peek

def slug(p):
    s = p.strip("/").replace("/", "__") or "root"
    return s if len(s) < 180 else s[:150] + "__%x" % (hash(s) & 0xffffffff)


def run_peek(d):
    out = os.path.join(PEEK_DIR, slug(d["path"]) + ".txt")
    cmd = [sys.executable, PEEK, local_path(d["path"])]
    if d["bytes"] > PEEK_BIG:
        cmd += ["--nrows", "200000"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                           encoding="utf-8", errors="replace", timeout=300, cwd=ROOT)
        text, ok = r.stdout, r.returncode == 0
    except subprocess.TimeoutExpired:
        text, ok = "peek 超過 300 秒，請手動用 --nrows 跑", False
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    log("peek %s：%s（%.1f 秒）" % (d["path"], "ok" if ok else "失敗", time.time() - t0))
    return rel(out), ok


def wait_for_pandas(peek_after):
    """等任一個標記檔出現（setup.sh 的 Python 套件裝好），最多 15 分鐘。"""
    if not peek_after:
        return
    t0 = time.time()
    while not any(os.path.exists(p) for p in peek_after) and time.time() - t0 < 900:
        time.sleep(0.3)
    log("Python 環境就緒（等了 %.1f 秒）" % (time.time() - t0))


# ---------------------------------------------------------------------------
# 清單

def render_inventory(db):
    ds = sorted(db["datasets"].values(), key=lambda d: d["path"])
    docs = sorted(db["docs"].values(), key=lambda d: d["path"])
    L = ["# HDFS 資料盤點", "",
         "更新：%s（`tools/prefetch.py`）。本機路徑 = `data` + HDFS 路徑。" % time.strftime("%Y-%m-%d %H:%M:%S"), ""]
    for run in db.get("runs", [])[-3:]:
        L.append("- " + run)
    L += ["", "| HDFS 路徑 | 大小 | 檔案數 | 類型 | 狀態 | peek 結果 |", "|---|---|---|---|---|---|"]
    for d in ds:
        L.append("| `%s` | %s | %d | %s | %s | %s |" % (
            d["path"], human(d["bytes"]), d["nfiles"], d["fmt"], d["status"],
            "`%s`" % d["peek"] if d.get("peek") else "—"))
    if not ds:
        L.append("| （沒有找到資料） | | | | | |")
    if docs:
        L += ["", "## 說明檔（開局時一定要讀）", ""]
        for d in docs:
            L.append("- `%s`（%s）%s" % (rel(local_path(d["path"])), human(d["bytes"]),
                                       "" if d["status"].startswith("✅") else "：" + d["status"]))
    if db.get("skipped"):
        L += ["", "## 自動略過的路徑（系統目錄、暫存、隱藏檔；需要的話手動下載）", ""]
        L += ["- " + s for s in db["skipped"][:40]]
    if db.get("problems"):
        L += ["", "## 問題", ""] + ["- " + p for p in db["problems"]]
    pending = [d["path"] for d in ds if not d["status"].startswith("✅")]
    if pending:
        L += ["", "沒下載的資料要用時：`python3 tools/prefetch.py %s`" % " ".join(pending[:5])]
    with open(INVENTORY, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# ---------------------------------------------------------------------------

def fetch(roots, auto, list_only, peek_after):
    t_start = time.time()
    base = hdfs_base()
    problems, skipped_paths = [], []

    set_status("running", "盤點 HDFS")
    flags_box = {}
    t_flags = threading.Thread(target=lambda: flags_box.setdefault("f", get_flags(base)))
    t_flags.start()   # 跟 ls -R 同時跑，各自啟動一次 JVM
    if auto:
        roots, skipped_paths = auto_roots(base, problems)

        def skip(p):
            return bool(SYS_RE.search(p) or JUNK_RE.search(p))
    else:
        def skip(p):
            for r in roots:
                if p == r or p.startswith(r.rstrip("/") + "/"):
                    return bool(JUNK_RE.search(p[len(r.rstrip("/")):]))
            return True

    tree = Tree()
    n = list_recursive(base, roots, tree, problems) if roots else 0
    t_list = time.time() - t_start
    log("盤點完成：%d 筆，%.1f 秒" % (n, t_list))

    datasets, docs = [], []
    for r in roots:
        if r in tree.info:
            classify(tree, r, skip, datasets, docs)
    for d in datasets + docs:
        describe(tree, d, skip)
    # 大於 1MB 的暫存／隱藏目錄列出來，讓 Claude 知道有東西沒抓
    for p, (is_dir, _) in tree.info.items():
        if skip(p) and not skip(posixpath.dirname(p)) and tree.size(p) >= TINY:
            skipped_paths.append("%s（%s）" % (p, human(tree.size(p))))

    # 決定下載哪些：小的先，自動模式受總量上限限制
    free = shutil.disk_usage(DATA).free
    limit = min(MAX_BYTES, int(free * 0.8)) if auto else int(free * 0.9)
    total = 0
    wanted = set()
    for d in sorted(docs, key=lambda d: d["bytes"]) + sorted(datasets, key=lambda d: d["bytes"]):
        if list_only:
            d["status"] = "⏸ 只盤點"
        elif total + d["bytes"] <= limit or (d["kind"] == "doc" and d["bytes"] < 50 * MB):
            total += d["bytes"]
            wanted.update(d["files"])
            d["status"] = "⏳"
        else:
            d["status"] = "⏭ 沒下載：超過%s上限 %s" % ("自動下載" if auto else "磁碟空間", human(limit))

    t_flags.join()
    flags = flags_box.get("f", [])
    groups = plan_downloads(tree, roots, wanted)
    todo = {dest: [s for s in srcs if not local_complete(tree, s, wanted)] for dest, srcs in groups.items()}
    todo = {k: v for k, v in todo.items() if v}
    n_src = sum(len(v) for v in todo.values())
    if todo:
        set_status("running", "下載 %s：%d 個 -get 同時跑、共 %d 個來源（-get %s）" % (
            human(total), len(todo), n_src, " ".join(flags) or "無額外選項"))
    else:
        set_status("running", "要的資料本機都已經有了，不用下載")
    t0 = time.time()
    errors = []
    if todo:
        with ThreadPoolExecutor(max_workers=PARALLEL_GETS) as ex:
            for errs in ex.map(lambda kv: run_get(base, flags, kv[0], kv[1]), todo.items()):
                errors.extend(errs)
    t_get = time.time() - t0
    problems.extend("下載錯誤：" + e for e in errors)

    # 狀態一律看本機實際有沒有：小於 1MB、沒排到的資料可能跟著整個目錄一起下載了
    for d in datasets + docs:
        ok = bool(d["files"]) and all(
            os.path.isfile(local_path(f)) and os.path.getsize(local_path(f)) == tree.info[f][1] for f in d["files"])
        if ok:
            d["status"] = "✅ 已下載"
            d["local"] = rel(local_path(d["path"]))
        elif d["status"] == "⏳":
            d["status"] = "❌ 下載不完整"

    # peek：需要 pandas，等 setup 的 Python 安裝完
    to_peek = [d for d in datasets if d["status"].startswith("✅")]
    t_peek = 0.0
    if to_peek:
        set_status("running", "等 Python 套件裝好，準備 peek %d 份資料" % len(to_peek))
        wait_for_pandas(peek_after)
        set_status("running", "peek %d 份資料" % len(to_peek))
        os.makedirs(PEEK_DIR, exist_ok=True)
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 1))) as ex:
            for d, (out, ok) in zip(to_peek, ex.map(run_peek, to_peek)):
                d["peek"] = out if ok else out + "（失敗）"
        t_peek = time.time() - t0

    # 跟之前的結果合併（手動下載的會補進同一份清單）
    db = read_json(DB) or {}
    db.setdefault("datasets", {})
    db.setdefault("docs", {})
    for d in datasets:
        old = db["datasets"].get(d["path"], {})
        if old.get("status", "").startswith("✅") and not d["status"].startswith("✅"):
            continue
        d.pop("files", None)
        db["datasets"][d["path"]] = d
    for d in docs:
        d.pop("files", None)
        db["docs"][d["path"]] = d
    db["skipped"] = sorted(set(db.get("skipped", [])) | set(skipped_paths))
    db["problems"] = problems
    db.setdefault("runs", []).append(
        "%s %s：根目錄 %s；盤點 %.1f 秒、下載 %s 用 %.1f 秒、peek %.1f 秒，共 %.1f 秒" % (
            time.strftime("%H:%M:%S"), "自動" if auto else "手動", "、".join(roots) or "（無）",
            t_list, human(total), t_get, t_peek, time.time() - t_start))
    write_json(DB, db)
    render_inventory(db)
    set_status("done", db["runs"][-1])


def fetch_now(reason):
    """背景沒有成功下載時，現在直接在前景跑一次自動下載。"""
    print("%s，現在直接從 HDFS 下載……" % reason, flush=True)
    try:
        fetch([], True, False, None)
    except Exception as e:   # fetch 已經把狀態設成 error
        print("下載失敗：%s: %s\n先確認 `hdfs dfs -ls /` 能不能用（見 docs/hdfs.md），再手動：python3 tools/prefetch.py <HDFS 路徑>"
              % (type(e).__name__, e))
        return None
    return read_json(STATUS)


def wait(timeout):
    """等背景下載完成並印出清單。背景沒在跑（setup 時 HDFS 不通、被中斷、失敗）就自己下載。"""
    t0 = time.time()
    last = None
    while True:
        st = read_json(STATUS)
        if st is None:
            st = fetch_now("沒有背景下載的紀錄（setup 時 HDFS 可能沒連上）")
        elif st.get("state") == "error":
            st = fetch_now("背景下載失敗過（%s）" % st.get("msg"))
        elif st.get("state") == "running" and not pid_alive(st.get("pid")):
            st = fetch_now("背景下載的程序已經不在了（被中斷？）")
        if st is None:
            return 1
        if st.get("state") != "running":
            break
        if time.time() - t0 > timeout:
            print("等了 %d 秒還沒完成（%s）。再跑一次 --wait，或看 work/prefetch.log" % (timeout, st.get("msg")))
            return 3
        if st.get("msg") != last:
            last = st.get("msg")
            print("…", last, flush=True)
        time.sleep(0.5)
    if st.get("state") == "error":
        print("背景下載失敗：%s（詳細見 work/prefetch.log）" % st.get("msg"))
    if os.path.exists(INVENTORY):
        with open(INVENTORY, encoding="utf-8") as f:
            print(f.read())
    return 0 if st.get("state") == "done" else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="要下載的 HDFS 路徑（手動模式）")
    ap.add_argument("--auto", action="store_true", help="自動盤點根目錄（扣掉系統目錄）")
    ap.add_argument("--list-only", action="store_true", help="只盤點，不下載")
    ap.add_argument("--wait", action="store_true", help="等背景下載完成並印出清單")
    ap.add_argument("--timeout", type=int, default=100, help="--wait 最多等背景幾秒（預設 100）")
    ap.add_argument("--peek-after", nargs="+", help=argparse.SUPPRESS)   # setup.sh 用：等任一個檔案出現（pandas 裝好）才 peek
    a = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)

    if a.wait:
        sys.exit(wait(a.timeout))
    if not a.auto and not a.paths:
        ap.error("要給 HDFS 路徑，或用 --auto / --wait")
    # hdfs://namenode:8020/x → /x；相對路徑在 HDFS 是相對於 /user/<帳號>，容易搞錯，要求寫絕對路徑
    paths = [re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*", "", p) for p in a.paths]
    bad = [p for p in paths if not p.startswith("/")]
    if bad:
        ap.error("請給 HDFS 絕對路徑（/ 開頭）：%s" % " ".join(bad))
    a.paths = [p.rstrip("/") or "/" for p in paths]
    st = read_json(STATUS)
    if st and st.get("state") == "running" and pid_alive(st.get("pid")) and st.get("pid") != os.getpid():
        sys.exit("已經有一個下載在跑（pid %s：%s），先用 --wait 等它完成" % (st.get("pid"), st.get("msg")))
    try:
        fetch(a.paths, a.auto, a.list_only, a.peek_after)
    except BaseException as e:   # 包含 Ctrl+C：狀態一定要更新，--wait 才不會一直等
        set_status("error", "%s: %s" % (type(e).__name__, e))
        raise


if __name__ == "__main__":
    main()
