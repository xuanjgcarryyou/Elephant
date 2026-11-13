"""台灣資料常用工具：縣市正規化、北中南分區、民國日期、四捨五入、數字清理。

用法（在 repo 根目錄）：
    import sys; sys.path.insert(0, "tools")
    from tw import normalize_county, region, roc_to_date, round_half_up, to_number

pandas 範例：
    df["縣市"] = df["縣市"].map(normalize_county)
    df["區域"] = df["縣市"].map(region)                              # 國發會/主計總處分法
    df["區域_離島"] = df["縣市"].map(lambda c: region(c, "ndc_islands"))
    df["日期"] = pd.to_datetime(df["日期"].map(roc_to_date))
    df["金額"] = df["金額"].map(to_number).astype(float)

分區定義與來源見 docs/taiwan-regions.md。直接執行本檔會跑自我測試：python3 tools/tw.py
"""
from __future__ import annotations

import csv
import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

REF_CSV = Path(__file__).resolve().parent.parent / "reference" / "taiwan_counties.csv"

with open(REF_CSV, encoding="utf-8", newline="") as _f:
    COUNTIES: list[dict] = list(csv.DictReader(_f))

COUNTY_NAMES = [r["county"] for r in COUNTIES]
_BY_NAME = {r["county"]: r for r in COUNTIES}

# 舊縣名 → 現行縣市（2010-12-25 五都改制、2014-12-25 桃園升格）
OLD_TO_NEW = {
    old: r["county"] for r in COUNTIES for old in r["old_names"].split("|") if old
}


def _en_key(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


# 中文簡稱：去掉「市/縣」後唯一的才收（新竹、嘉義有市有縣，不收）
_stems: dict[str, list[str]] = {}
for _n in COUNTY_NAMES:
    _stems.setdefault(_n[:-1], []).append(_n)
_ALIAS_ZH = {stem: names[0] for stem, names in _stems.items() if len(names) == 1}
_ALIAS_ZH["馬祖"] = "連江縣"

# 英文：正式名稱、去掉 City/County 後唯一的簡稱、常見舊拼法
_ALIAS_EN: dict[str, str] = {}
_en_stems: dict[str, list[str]] = {}
for _r in COUNTIES:
    _ALIAS_EN[_en_key(_r["county_en"])] = _r["county"]
    _stem = _en_key(re.sub(r"\s*(City|County)$", "", _r["county_en"]))
    _en_stems.setdefault(_stem, []).append(_r["county"])
_ALIAS_EN.update({k: v[0] for k, v in _en_stems.items() if len(v) == 1})
_ALIAS_EN.update({
    "changhwacounty": "彰化縣", "changhwa": "彰化縣",
    "hwaliencounty": "花蓮縣", "hwalien": "花蓮縣",
    "ilancounty": "宜蘭縣", "ilan": "宜蘭縣",
    "taitungcounty": "臺東縣", "matsu": "連江縣", "kinmen": "金門縣",
    "taipeicounty": "新北市", "taoyuancounty": "桃園市", "taichungcounty": "臺中市",
    "tainancounty": "臺南市", "kaohsiungcounty": "高雄市",
})


def normalize_county(name, merge_old: bool = True) -> str | None:
    """各種寫法 → 標準縣市名（臺北市、新北市…），認不出來回傳 None。

    處理：台/臺、全形、中間空白（「新 北 市」）、省名前綴、簡稱（「台北」）、英文名、
    舊縣名（臺北縣→新北市，merge_old=False 時保留舊名）。
    「新竹」「嘉義」有市有縣，無法判斷，回傳 None。
    """
    if name is None:
        return None
    s = unicodedata.normalize("NFKC", str(name))
    s = re.sub(r"\s+", "", s).replace("台", "臺")
    s = re.sub(r"^(中華民國|臺灣省|福建省)", "", s)
    if not s or s.lower() in ("nan", "none"):
        return None
    if s in _BY_NAME:
        return s
    if s in OLD_TO_NEW:
        return OLD_TO_NEW[s] if merge_old else s
    if s in _ALIAS_ZH:
        return _ALIAS_ZH[s]
    return _ALIAS_EN.get(_en_key(s))


SCHEMES = ("ndc", "ndc_islands", "circle7", "outlying")


def region(name, scheme: str = "ndc") -> str | None:
    """縣市 → 區域。認不出縣市回傳 None。

    scheme：
      ndc          國發會/主計總處：北部、中部、南部（含澎湖）、東部、金馬（預設）
      ndc_islands  同上，但澎湖、金門、連江合為「離島」
      circle7      七大區域生活圈：北北基宜、桃竹苗、中彰投、雲嘉南、高屏、花東、澎金馬
      outlying     本島 / 離島（縣市層級：澎湖、金門、連江）
    """
    if scheme not in SCHEMES:
        raise ValueError(f"scheme 必須是 {SCHEMES} 之一，收到 {scheme!r}")
    county = normalize_county(name)
    if county is None:
        return None
    r = _BY_NAME[county]
    if scheme == "ndc":
        return r["ndc_region"]
    if scheme == "circle7":
        return r["circle7"]
    if scheme == "outlying":
        return "離島" if r["outlying"] == "1" else "本島"
    return "離島" if r["outlying"] == "1" else r["ndc_region"]  # ndc_islands


def counties_in(region_name: str, scheme: str = "ndc") -> list[str]:
    """區域 → 縣市清單，例如 counties_in("北部")。"""
    return [n for n in COUNTY_NAMES if region(n, scheme) == region_name]


# ---------------------------------------------------------------------------
# 民國日期

_ROC_FULL = [
    re.compile(r"^(?:民國)?(\d{2,3})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?$"),  # 113/5/1、113年5月1日
    re.compile(r"^(\d{3})(\d{2})(\d{2})$"),                                  # 1130501、0990501
    re.compile(r"^(\d{2})(\d{2})(\d{2})$"),                                  # 990501（民國 99 年）
]
_ROC_MONTH = [
    re.compile(r"^(?:民國)?(\d{2,3})[年/\-.](\d{1,2})月?$"),  # 113/05、113年5月
    re.compile(r"^(\d{3})(\d{2})$"),                          # 11305、09905
]


def roc_year(y) -> int:
    """民國年 → 西元年。"""
    return int(y) + 1911


def roc_to_date(s) -> date | None:
    """民國日期 → datetime.date；只有年月時回傳當月 1 日。格式不符或日期不存在回傳 None。"""
    if s is None:
        return None
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s)))
    if t.endswith(".0"):  # pandas 把 1130501 讀成 float 的情況
        t = t[:-2]
    for pats, has_day in ((_ROC_FULL, True), (_ROC_MONTH, False)):
        for p in pats:
            m = p.match(t)
            if m:
                y, mo = int(m.group(1)), int(m.group(2))
                d = int(m.group(3)) if has_day else 1
                try:
                    return date(y + 1911, mo, d)
                except ValueError:
                    return None
    return None


# ---------------------------------------------------------------------------
# 數字

def round_half_up(x, ndigits: int = 0):
    """真正的四捨五入。

    Python round() 與 pandas/numpy .round() 是「四捨六入五成雙」，而且受浮點誤差影響：
    round(2.5) == 2、round(2.675, 2) == 2.67。本函式回傳 3 與 2.68。
    ndigits <= 0 回傳 int，否則回傳 float；None/NaN 原樣回傳。
    """
    if x is None or (isinstance(x, float) and x != x):
        return x
    try:
        q = Decimal(1).scaleb(-ndigits)
        d = Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return int(d) if ndigits <= 0 else float(d)


_MISSING = {
    "", "-", "—", "–", "−", "...", "..", "x", "X", "*", "**", "na", "n/a",
    "nan", "none", "null", "\\n", "無", "不詳", "未詳", "#n/a", "#value!", "#div/0!",
}


def to_number(s) -> float | None:
    """政府/報表數字 → float；缺值記號回傳 None。

    處理：千分位「1,234」、全形數字、前後空白、百分比「12.3%」（回傳 12.3，不除以 100）、
    括號負數「(123)」、會計負號「△123」、缺值記號（-、…、x、*、NA、\\N 等）。
    """
    if s is None:
        return None
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return None if s != s else float(s)
    t = unicodedata.normalize("NFKC", str(s)).strip()
    if t.lower() in _MISSING:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t[:1] in ("△", "▲"):
        neg, t = True, t[1:]
    t = t.replace(",", "").replace(" ", "").rstrip("%")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


# ---------------------------------------------------------------------------

def _selftest() -> None:
    assert len(COUNTIES) == 22
    assert normalize_county("台北市") == "臺北市"
    assert normalize_county(" 新 北 市 ") == "新北市"
    assert normalize_county("臺北縣") == "新北市"
    assert normalize_county("臺北縣", merge_old=False) == "臺北縣"
    assert normalize_county("福建省金門縣") == "金門縣"
    assert normalize_county("台中") == "臺中市"
    assert normalize_county("新竹") is None          # 市縣不明
    assert normalize_county("Hwalien County") == "花蓮縣"
    assert normalize_county("New Taipei") == "新北市"
    assert normalize_county("Taipei") == "臺北市"
    assert normalize_county("Hsinchu") is None
    assert normalize_county("馬祖") == "連江縣"
    assert normalize_county("火星市") is None
    assert region("宜蘭縣") == "北部" and region("苗栗縣") == "中部"
    assert region("雲林縣") == "中部" and region("澎湖縣") == "南部"
    assert region("澎湖縣", "ndc_islands") == "離島"
    assert region("苗栗縣", "circle7") == "桃竹苗"
    assert region("金門縣") == "金馬"
    assert region("高雄縣") == "南部"
    assert counties_in("東部") == ["花蓮縣", "臺東縣"]
    assert [len(counties_in(r)) for r in ("北部", "中部", "南部", "東部", "金馬")] == [7, 5, 6, 2, 2]
    assert len(set(r["circle7"] for r in COUNTIES)) == 7
    assert roc_to_date("113/05/01") == date(2024, 5, 1)
    assert roc_to_date("113年5月1日") == date(2024, 5, 1)
    assert roc_to_date("民國99年12月") == date(2010, 12, 1)
    assert roc_to_date("1130501") == date(2024, 5, 1)
    assert roc_to_date("990501") == date(2010, 5, 1)
    assert roc_to_date("11305") == date(2024, 5, 1)
    assert roc_to_date(1130501.0) == date(2024, 5, 1)
    assert roc_to_date("113/02/30") is None
    assert roc_to_date("2024-05-01") is None
    assert round_half_up(2.5) == 3 and round_half_up(-2.5) == -3
    assert round_half_up(2.675, 2) == 2.68
    assert round_half_up(1234, -2) == 1200
    assert to_number("1,234") == 1234.0
    assert to_number("１２３") == 123.0
    assert to_number("12.3%") == 12.3
    assert to_number("(1,000)") == -1000.0
    assert to_number("△50") == -50.0
    assert to_number("-12") == -12.0
    for m in ("-", "－", "…", "x", "NA", "\\N", "", "  "):
        assert to_number(m) is None, m
    print("tw.py 自我測試通過")


if __name__ == "__main__":
    _selftest()
