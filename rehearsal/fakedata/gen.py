#!/usr/bin/env python3
"""彩排用假資料產生器（放在 repo 外面，不要 commit 進 Elephant）。

產生 seed/ 底下三份資料（之後會原樣放進 HDFS /data/contest/），
並且「直接從產生時的乾淨數值」算出標準答案 → expected_gen.json。
verify.py 會再從產生出來的檔案用另一種方法重算，兩邊一致才算數。

陷阱：
- county_stats CSV：cp950、台/臺混用、每月有「總計」列、民國年月 113/05、千分位、缺值「-」
- daily_sales parquet：Hive 分區 dt=YYYY-MM-DD/、_SUCCESS、檔案裡沒有日期欄
- transactions：Hive textfile，\\x01 分隔、\\N 缺值、無表頭、4 個 part 檔
"""
import csv
import json
import random
import shutil
import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
OUT = HERE / "seed"
rng = random.Random(20261113)


def rhu(x: Decimal, nd: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)


# (縣市, 國發會分區, 是否離島, 觀光人次基準, 勞動力(千人), 失業率基準)
COUNTIES = [
    ("新北市", "北部", 0, 2_400_000, 2135.0, 3.55),
    ("臺北市", "北部", 0, 2_900_000, 1310.0, 3.45),
    ("桃園市", "北部", 0, 1_500_000, 1205.0, 3.62),
    ("臺中市", "中部", 0, 2_200_000, 1435.0, 3.58),
    ("臺南市", "南部", 0, 1_700_000, 950.0, 3.71),
    ("高雄市", "南部", 0, 2_000_000, 1395.0, 3.79),
    ("宜蘭縣", "北部", 0, 1_100_000, 225.0, 3.88),
    ("新竹縣", "北部", 0, 500_000, 305.0, 3.32),
    ("苗栗縣", "中部", 0, 600_000, 265.0, 3.95),
    ("彰化縣", "中部", 0, 700_000, 640.0, 3.66),
    ("南投縣", "中部", 0, 1_300_000, 245.0, 4.12),
    ("雲林縣", "中部", 0, 650_000, 345.0, 4.05),
    ("嘉義縣", "南部", 0, 900_000, 255.0, 4.18),
    ("屏東縣", "南部", 0, 1_400_000, 400.0, 4.21),
    ("臺東縣", "東部", 0, 550_000, 105.0, 4.33),
    ("花蓮縣", "東部", 0, 800_000, 160.0, 4.27),
    ("澎湖縣", "南部", 1, 280_000, 52.0, 3.21),
    ("基隆市", "北部", 0, 450_000, 185.0, 4.02),
    ("新竹市", "北部", 0, 350_000, 225.0, 3.28),
    ("嘉義市", "南部", 0, 300_000, 132.0, 3.93),
    ("金門縣", "金馬", 1, 160_000, None, None),   # 人力調查不含金馬 → 勞動欄位為「-」
    ("連江縣", "金馬", 1, 18_000, None, None),
]
SEASON = [1.15, 1.25, 1.0, 1.05, 0.95, 0.95, 1.2, 1.25, 0.9, 1.0, 0.95, 1.05]
TOURISM_MISSING = {("113/01", "連江縣"), ("113/02", "連江縣"), ("113/04", "花蓮縣"), ("112/08", "澎湖縣")}
MONTHS = [f"{y}/{m:02d}" for y in (112, 113) for m in range(1, 13)]


def gen_county_csv(exp: dict) -> None:
    rows = []          # 要寫進 CSV 的字串列
    clean = []         # 乾淨數值：(月, 縣市, 觀光 or None, 勞動力 Decimal or None, 失業 Decimal or None, 失業率 Decimal or None)
    for ym in MONTHS:
        m = int(ym[-2:])
        month_rows = []
        for name, _reg, _isl, tbase, labor, rbase in COUNTIES:
            tour = None if (ym, name) in TOURISM_MISSING else int(tbase * SEASON[m - 1] * rng.uniform(0.9, 1.1))
            if labor is None:
                lab = une = rate = None
            else:
                lab = rhu(Decimal(str(labor * rng.uniform(0.995, 1.005))), 1)
                une = rhu(Decimal(str(float(lab) * rbase / 100 * rng.uniform(0.93, 1.07))), 1)
                rate = rhu(une / lab * 100, 2)
            clean.append((ym, name, tour, lab, une, rate))
            shown = name.replace("臺", "台") if ("臺" in name and rng.random() < 0.5) else name
            month_rows.append([
                ym, shown,
                "-" if tour is None else f"{tour:,}",
                "-" if lab is None else f"{lab:,.1f}",
                "-" if une is None else f"{une:,.1f}",
                "-" if rate is None else f"{rate:.2f}",
            ])
        mc = [c for c in clean if c[0] == ym]
        t_tour = sum(c[2] for c in mc if c[2] is not None)
        t_lab = sum(c[3] for c in mc if c[3] is not None)
        t_une = sum(c[4] for c in mc if c[4] is not None)
        t_rate = rhu(t_une / t_lab * 100, 2)
        rows.append([ym, "總計", f"{t_tour:,}", f"{t_lab:,.1f}", f"{t_une:,.1f}", f"{t_rate:.2f}"])
        rows.extend(month_rows)

    d = OUT / "county_stats"
    d.mkdir(parents=True)
    with open(d / "county_monthly_stats.csv", "w", encoding="cp950", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")   # 政府資料常見 CRLF
        w.writerow(["統計期", "縣市別", "觀光遊憩人次", "勞動力人口(千人)", "失業人口(千人)", "失業率(%)"])
        w.writerows(rows)

    # ---- Q1：113 年各區觀光人次 ----
    reg = {c[0]: c[1] for c in COUNTIES}
    isl = {c[0]: c[2] for c in COUNTIES}
    y113 = [c for c in clean if c[0].startswith("113/") and c[2] is not None]
    ndc = {r: sum(c[2] for c in y113 if reg[c[1]] == r) for r in ("北部", "中部", "南部", "東部", "金馬")}
    islands = {r: sum(c[2] for c in y113 if (("離島" if isl[c[1]] else reg[c[1]]) == r))
               for r in ("北部", "中部", "南部", "東部", "離島")}
    penghu = sum(c[2] for c in y113 if c[1] == "澎湖縣")
    exp["q1"] = {
        "ndc": ndc, "ndc_islands": islands, "penghu_113": penghu,
        "four_region_total": sum(ndc[r] for r in ("北部", "中部", "南部", "東部")),
        "all_22_total": sum(c[2] for c in y113),
        "missing_cells_113": sorted(k for k in TOURISM_MISSING if k[0].startswith("113/")),
    }

    # ---- Q4：113/06 平均失業率 ----
    jun = [c for c in clean if c[0] == "113/06" and c[5] is not None]
    simple = sum(c[5] for c in jun) / len(jun)
    weighted = sum(c[4] for c in jun) / sum(c[3] for c in jun) * 100
    exp["q4"] = {
        "n_counties": len(jun),
        "simple_mean_raw": str(simple), "simple_mean": str(rhu(simple, 2)),
        "weighted_raw": str(weighted), "weighted": str(rhu(weighted, 2)),
        "total_row_rate": str(rhu(sum(c[4] for c in jun) / sum(c[3] for c in jun) * 100, 2)),
        "sum_labor": str(sum(c[3] for c in jun)), "sum_unemp": str(sum(c[4] for c in jun)),
    }


STORES = [f"S{i:03d}" for i in range(1, 31)]
CATS = {"生鮮": 180, "飲料": 45, "零食": 60, "日用品": 120, "家電": 1500}
SPECIAL = {date(2024, 2, 10): 1.8, date(2024, 3, 30): 2.0, date(2024, 1, 1): 1.6}


def day_factor(d: date) -> float:
    if d in SPECIAL:
        return SPECIAL[d]
    f = 1.3 if d.weekday() >= 5 else 1.0
    if date(2024, 2, 8) <= d <= date(2024, 2, 14):
        f *= 1.15
    return f


def gen_daily_sales(exp: dict) -> None:
    root = OUT / "daily_sales"
    root.mkdir(parents=True)
    per_day = {}
    d = date(2024, 1, 1)
    while d <= date(2024, 3, 31):
        f = day_factor(d)
        recs = {"store_id": [], "category": [], "qty": [], "amount": []}
        for s in STORES:
            for cat, price in CATS.items():
                q = max(1, int(rng.gauss(40 if cat != "家電" else 3, 8 if cat != "家電" else 1.5) * f))
                amt = int(q * price * rng.uniform(0.85, 1.15))
                recs["store_id"].append(s); recs["category"].append(cat)
                recs["qty"].append(q); recs["amount"].append(amt)
        per_day[d] = sum(recs["amount"])
        tbl = pa.table({
            "store_id": pa.array(recs["store_id"], pa.string()),
            "category": pa.array(recs["category"], pa.string()),
            "qty": pa.array(recs["qty"], pa.int32()),
            "amount": pa.array(recs["amount"], pa.int64()),
        })
        part = root / f"dt={d.isoformat()}"
        part.mkdir()
        pq.write_table(tbl, part / f"part-00000-{uuid.UUID(int=rng.getrandbits(128))}.c000.snappy.parquet",
                       compression="snappy")
        d += timedelta(days=1)
    (root / "_SUCCESS").write_bytes(b"")

    feb = {k: v for k, v in per_day.items() if k.month == 2}
    best = max(feb, key=feb.get)
    exp["q2"] = {
        "feb_total": sum(feb.values()), "feb_days": len(feb),
        "feb_max_day": best.isoformat(), "feb_max_amount": feb[best],
        "feb_total_without_29": sum(v for k, v in feb.items() if k.day != 29),
        "overall_max_day": max(per_day, key=per_day.get).isoformat(),
    }


def gen_transactions(exp: dict) -> None:
    n_total, n_gold, n_gold_null = 6000, 405, 5
    # 金卡：400 筆有件數，合計 1070 → 平均 2.675（四捨五入 2.68；Python round 會給 2.67）
    gq = [rng.choice([1, 2, 2, 3, 3, 3, 4, 4, 5]) for _ in range(n_gold - n_gold_null)]
    while sum(gq) != 1070:
        i = rng.randrange(len(gq))
        if sum(gq) > 1070 and gq[i] > 1:
            gq[i] -= 1
        elif sum(gq) < 1070 and gq[i] < 6:
            gq[i] += 1
    gold_qty = gq + [None] * n_gold_null
    rng.shuffle(gold_qty)

    levels = ["金卡"] * n_gold
    rest = n_total - n_gold
    levels += rng.choices(["一般", "銀卡", None], weights=[45, 25, 30], k=rest)
    rng.shuffle(levels)

    t0 = datetime(2024, 1, 1)
    rows, gi = [], 0
    for i, lv in enumerate(levels):
        if lv == "金卡":
            q = gold_qty[gi]; gi += 1
        else:
            q = None if rng.random() < 0.01 else rng.randint(1, 8)
        price = rng.choice([35, 49, 59, 89, 120, 199, 250])
        ts = t0 + timedelta(seconds=rng.randrange(91 * 86400))
        rows.append([
            f"T{i + 1:07d}", ts.strftime("%Y-%m-%d %H:%M:%S"),
            None if lv is None else f"M{rng.randrange(1, 200000):06d}", lv,
            rng.choice(STORES), q, price, None if q is None else q * price,
        ])
    root = OUT / "transactions"
    root.mkdir(parents=True)
    sizes = [1600, 1500, 1450, 1450]
    k = 0
    part_gold = []
    for p, sz in enumerate(sizes):
        chunk = rows[k:k + sz]; k += sz
        with open(root / f"part-{p:05d}", "w", encoding="utf-8", newline="\n") as f:
            for r in chunk:
                f.write("\x01".join("\\N" if v is None else str(v) for v in r) + "\n")
        g = [r[5] for r in chunk if r[3] == "金卡" and r[5] is not None]
        part_gold.append((len(g), sum(g)))
    gold = [r[5] for r in rows if r[3] == "金卡"]
    nn = [q for q in gold if q is not None]
    exp["q3"] = {
        "gold_rows": len(gold), "gold_qty_nonnull": len(nn), "gold_qty_sum": sum(nn),
        "mean_exact": str(Decimal(sum(nn)) / Decimal(len(nn))),
        "answer_half_up": str(rhu(Decimal(sum(nn)) / Decimal(len(nn)), 2)),
        "python_round": round(sum(nn) / len(nn), 2),
        "null_as_zero": str(rhu(Decimal(sum(nn)) / Decimal(len(gold)), 2)),
        "part00000_only": str(rhu(Decimal(part_gold[0][1]) / Decimal(part_gold[0][0]), 2)),
        "per_part": part_gold,
    }


README = """資料說明

1. county_stats/county_monthly_stats.csv
   縣市別月統計。欄位：統計期、縣市別、觀光遊憩人次、勞動力人口(千人)、失業人口(千人)、失業率(%)

2. daily_sales/
   各門市每日銷售彙總。欄位：store_id 門市代碼、category 商品類別、qty 銷售件數、amount 銷售金額（新臺幣元）

3. transactions/
   交易明細（Hive 資料表 contest.transactions 的資料檔）。欄位依序為：
   txn_id 交易編號、txn_time 交易時間、member_id 會員編號、member_level 會員等級、
   store_id 門市代碼、qty 購買件數、unit_price 單價、amount 金額
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    exp: dict = {}
    gen_county_csv(exp)
    gen_daily_sales(exp)
    gen_transactions(exp)
    (OUT / "README.txt").write_text(README, encoding="utf-8")
    (HERE / "expected_gen.json").write_text(json.dumps(exp, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(exp, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
