#!/usr/bin/env python3
"""快速看一份資料長什麼樣：格式、編碼、欄位、缺值、疑似陷阱。

用法：
    python3 tools/peek.py data/xxx.csv
    python3 tools/peek.py data/xxx.csv --nrows 200000      # 大檔只讀前 N 列
    python3 tools/peek.py data/table_dir/                  # parquet / orc / part 檔目錄（含 hive 分區）
    python3 tools/peek.py data/xxx.xlsx --sheet 1          # Excel 第 2 個工作表
    python3 tools/peek.py data/part-00000 --no-header      # Hive textfile（\\x01 分隔會自動偵測）

文字檔一律先以字串讀入，才看得到前導零、千分位、缺值記號等原貌。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tw import _MISSING, normalize_county, roc_to_date, to_number  # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 60)
pd.set_option("display.max_colwidth", 40)

TOTAL_PAT = re.compile(r"總計|合計|小計|總和|總數|^全國$|^臺灣地區$|^台灣地區$|^總$|^Total$", re.I)
SKIP_FILES = re.compile(r"^(_|\.)|\.crc$")


def h(title: str) -> None:
    print(f"\n== {title} ==")


def sniff_kind(f: Path) -> str:
    head = f.open("rb").read(4)
    if head == b"PAR1":
        return "parquet"
    if head[:3] == b"ORC":
        return "orc"
    if f.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return "excel"
    if f.suffix.lower() in (".json", ".jsonl", ".ndjson"):
        return "json"
    return "text"


def detect_encoding(f: Path) -> str:
    raw = f.open("rb").read(1 << 20)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    for enc in ("utf-8-sig", "cp950", "big5hkscs"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError as e:
            if e.start >= len(raw) - 4:  # 只是截在多位元組字元中間
                return enc
    return "latin-1"


def detect_sep(f: Path, enc: str) -> str:
    with f.open(encoding=enc, errors="replace") as fh:
        lines = [fh.readline() for _ in range(20)]
    text = "".join(lines)
    if "\x01" in text:
        return "\x01"
    try:
        return csv.Sniffer().sniff(text, delimiters=",\t;|").delimiter
    except csv.Error:
        return ","


def count_lines(files: list[Path]) -> int:
    n = 0
    for f in files:
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 24), b""):
                n += chunk.count(b"\n")
    return n


def load_text(files: list[Path], a) -> tuple[pd.DataFrame, dict]:
    enc = a.encoding or detect_encoding(files[0])
    sep = a.sep or detect_sep(files[0], enc)
    print(f"編碼：{enc}　分隔符：{sep!r}　檔案數：{len(files)}")
    h("原始前 5 行（檢查表頭、多列表頭、註解列）")
    with files[0].open(encoding=enc, errors="replace") as fh:
        for _ in range(5):
            line = fh.readline()
            if not line:
                break
            print("  " + line.rstrip("\r\n")[:200].replace("\x01", "␁"))
    frames, remaining = [], a.nrows
    for f in files:
        df = pd.read_csv(f, sep=sep, encoding=enc, dtype=str, keep_default_na=False,
                         header=None if a.no_header else "infer", nrows=remaining,
                         engine="python" if len(sep) > 1 else "c", on_bad_lines="warn")
        frames.append(df)
        if remaining is not None:
            remaining -= len(df)
            if remaining <= 0:
                break
    df = pd.concat(frames, ignore_index=True)
    total = None
    if a.nrows is not None and len(df) >= a.nrows:
        total = count_lines(files) - (0 if a.no_header else len(files))
    return df, {"raw_text": True, "total": total}


def load(path: Path, a) -> tuple[pd.DataFrame, dict]:
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file() and not SKIP_FILES.search(p.name))
        if not files:
            sys.exit(f"{path} 底下沒有資料檔")
        kind = sniff_kind(files[0])
        print(f"目錄，{len(files)} 個檔，判斷為 {kind}")
    else:
        files, kind = [path], sniff_kind(path)
        print(f"檔案，{path.stat().st_size / 1e6:.1f} MB，判斷為 {kind}")

    if kind in ("parquet", "orc"):
        import pyarrow.dataset as ds
        dataset = ds.dataset(str(path), format=kind, partitioning="hive")
        total = dataset.count_rows()
        table = dataset.head(a.nrows) if a.nrows else dataset.to_table()
        print("schema（含 hive 分區欄）：")
        print("  " + str(dataset.schema).replace("\n", "\n  "))
        return table.to_pandas(), {"raw_text": False, "total": total}

    if kind == "excel":
        sheets = pd.ExcelFile(path).sheet_names
        print(f"工作表：{sheets}，目前看第 {a.sheet} 個（--sheet 切換）")
        h("原始前 8 列（Excel 常有多列表頭、合併儲存格、單位列）")
        print(pd.read_excel(path, sheet_name=a.sheet, header=None, nrows=8, dtype=str).to_string())
        df = pd.read_excel(path, sheet_name=a.sheet, nrows=a.nrows, dtype=str,
                           header=None if a.no_header else 0, keep_default_na=False)
        return df, {"raw_text": True, "total": None}

    if kind == "json":
        lines = path.suffix.lower() in (".jsonl", ".ndjson")
        df = pd.read_json(path, lines=lines, nrows=a.nrows if lines else None, dtype=False)
        return df, {"raw_text": False, "total": None}

    return load_text(files, a)


def column_report(s: pd.Series, raw_text: bool) -> dict:
    vals = s.astype(str) if raw_text else s
    if raw_text:
        stripped = vals.str.strip()
        # 先 NFKC 再比對：全形「－」、「…」才會對上缺值記號
        is_missing = stripped.map(lambda v: unicodedata.normalize("NFKC", v).strip().lower()).isin(_MISSING)
    else:
        stripped = vals
        is_missing = s.isna()
    present = stripped[~is_missing]
    sample = present.astype(str).drop_duplicates().head(2000)

    hints, kind = [], str(s.dtype)
    if raw_text and len(sample):
        plain = pd.to_numeric(sample, errors="coerce").notna().mean()
        loose = sample.map(lambda v: to_number(v) is not None).mean()
        if plain >= 0.95:
            kind = "數字"
            if sample.str.match(r"^0\d").any():
                hints.append("有前導零→可能是代碼，保持字串")
        elif loose >= 0.8:
            kind = "數字(文字)"
            hints.append("千分位/符號→to_number")
        else:
            kind = "文字"
        if (vals[~is_missing] != stripped[~is_missing]).any():
            hints.append("有前後空白")
        markers = sorted(set(vals[is_missing].str.strip()) - {""})
        if markers:
            hints.append(f"缺值記號{markers[:5]}")
    if len(sample):
        str_sample = sample.astype(str)
        if str_sample.map(lambda v: roc_to_date(v) is not None).mean() >= 0.8:
            hints.append("疑似民國日期→roc_to_date")
        if str_sample.map(lambda v: normalize_county(v) is not None).mean() >= 0.8:
            mixed = str_sample.str.contains("台").any() and str_sample.str.contains("臺").any()
            hints.append("縣市→normalize_county/region" + ("（臺/台混用）" if mixed else ""))

    examples = ", ".join(sample.astype(str).head(3).str.slice(0, 15))
    return {
        "欄位": s.name, "型別": kind, "缺值": int(is_missing.sum()),
        "相異值": int(present.nunique()), "範例": examples, "提示": "；".join(hints),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path)
    ap.add_argument("--nrows", type=int, help="只讀前 N 列（大檔用）")
    ap.add_argument("--encoding", help="指定編碼（預設自動偵測 utf-8 / cp950 / big5hkscs）")
    ap.add_argument("--sep", help="指定分隔符（預設自動偵測）")
    ap.add_argument("--sheet", type=int, default=0, help="Excel 工作表序號（從 0 起）")
    ap.add_argument("--no-header", action="store_true", help="第一列不是表頭（Hive textfile 常見）")
    a = ap.parse_args()
    if a.sep:
        a.sep = a.sep.encode().decode("unicode_escape")  # 允許 --sep '\x01'

    h(str(a.path))
    df, meta = load(a.path, a)

    h("規模")
    total = meta["total"]
    print(f"讀入 {len(df):,} 列 × {df.shape[1]} 欄" + (f"（全檔約 {total:,} 列）" if total else ""))

    h("欄位")
    rep = pd.DataFrame([column_report(df[c], meta["raw_text"]) for c in df.columns])
    print(rep.to_string(index=False))

    h("前 5 列")
    print(df.head().to_string())

    num_cols = [r["欄位"] for _, r in rep.iterrows()
                if "前導零" not in r["提示"] and (r["型別"].startswith("數字") or
                (not meta["raw_text"] and pd.api.types.is_numeric_dtype(df[r["欄位"]])))]
    if num_cols:
        h("數值欄統計（已用 to_number 轉換）")
        num = df[num_cols].apply(lambda c: c.map(to_number).astype(float)) if meta["raw_text"] else df[num_cols]
        print(num.describe().T.to_string())

    h("檢查")
    print(f"完全重複列：{int(df.duplicated().sum()):,}")
    # pandas 3 的字串欄是 str dtype，不再是 object
    text_cols = [c for c in df.columns if df[c].dtype == object or pd.api.types.is_string_dtype(df[c])]
    if text_cols:
        mask = pd.Series(False, index=df.index)
        for c in text_cols:
            mask |= df[c].astype(str).str.strip().str.contains(TOTAL_PAT, na=False)
        if mask.any():
            print(f"疑似合計/小計列：{int(mask.sum())} 列（加總前要排除，否則重複計算）")
            print(df[mask].head(5).to_string())
        else:
            print("疑似合計/小計列：無")


if __name__ == "__main__":
    main()
