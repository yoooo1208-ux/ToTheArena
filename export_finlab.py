# -*- coding: utf-8 -*-
r"""
處置股分析 - 步驟 1：從 FinLab 匯出原始資料
執行方式（在這個檔案所在資料夾開命令列）：
    python export_finlab.py
會在同資料夾建立 finlab_export\ 子資料夾，放入：
    disposal_information.csv.gz   處置股事件
    trading_attention.csv.gz      注意股事件
    price_open/high/low/close/volume.csv.gz  2016 年起日 K
    company_basic_info.csv.gz     公司名稱、市場別
    export_log.txt                欄位與筆數紀錄（給 Claude 檢查格式用）
"""
import os
import sys
import traceback

HERE_ = os.path.dirname(os.path.abspath(__file__))
# API Key：優先讀環境變數 FINLAB_API_KEY（GitHub Actions 用 Secret 帶入），其次讀同資料夾的 finlab_key.txt
API_KEY = os.environ.get("FINLAB_API_KEY", "").strip()
if not API_KEY and os.path.exists(os.path.join(HERE_, "finlab_key.txt")):
    API_KEY = open(os.path.join(HERE_, "finlab_key.txt"), encoding="utf-8").read().strip()
if not API_KEY:
    raise SystemExit("找不到 FinLab API Key：請設定環境變數 FINLAB_API_KEY，或在同資料夾放 finlab_key.txt")
PRICE_START = "2016-01-01"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "finlab_export")
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "export_log.txt"), "w", encoding="utf-8")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    LOG.write(s + "\n")
    LOG.flush()


# finlab 2.x 需要 Python 3.10+；舊版 Python 補上 typing 缺的名稱
import typing
try:
    import typing_extensions as _te
    for _n in ("TypeAlias", "ParamSpec", "Concatenate", "TypeGuard", "Self", "Literal", "Protocol"):
        if not hasattr(typing, _n) and hasattr(_te, _n):
            setattr(typing, _n, getattr(_te, _n))
except ImportError:
    pass

try:
    import pandas as pd
    import finlab
    from finlab import data
except Exception as e:
    print("\n載入 finlab 失敗：", repr(e))
    print("你的 Python 是", sys.version.split()[0], "，finlab 2.x 需要 3.10 以上。請改用：")
    print("  conda create -n fl python=3.11 -y")
    print("  conda activate fl")
    print("  pip install finlab pandas")
    print("  python export_finlab.py")
    raise

log("python", sys.version)
log("pandas", pd.__version__, "finlab", getattr(finlab, "__version__", "?"))

finlab.login(API_KEY)
try:
    data.truncate_start = None  # 盡量拿完整歷史
except Exception:
    pass


def describe(name, df):
    log("=" * 70)
    log(f"[{name}] type={type(df).__name__} shape={getattr(df, 'shape', None)}")
    try:
        log("index name:", df.index.name, "| index dtype:", df.index.dtype)
        log("columns:", list(df.columns)[:60])
        log("dtypes:\n" + df.dtypes.astype(str).head(60).to_string())
        with pd.option_context("display.max_columns", 50, "display.width", 250,
                               "display.max_colwidth", 120):
            log("head:\n" + df.head(5).to_string())
            log("tail:\n" + df.tail(8).to_string())
    except Exception as e:
        log("describe error", e)


def save_event_table(key, fname):
    try:
        df = data.get(key)
        df = pd.DataFrame(df)
        describe(key, df)
        df = df.reset_index()
        df.to_csv(os.path.join(OUT, fname), index=False, encoding="utf-8-sig",
                  compression="gzip")
        log(f"saved {fname} rows={len(df)}")
    except Exception:
        log(f"!! {key} 失敗\n" + traceback.format_exc())


ONLY_EXTRA = len(sys.argv) > 1 and sys.argv[1] == "extra"


def save_wide(key, fname, fmt="%.2f", alts=()):
    for k in (key,) + tuple(alts):
        try:
            df = data.get(k)
            df = pd.DataFrame(df)
            df.index = pd.to_datetime(df.index)
            df = df[df.index >= PRICE_START]
            keep = [c for c in df.columns if len(str(c)) <= 6]
            df = df[keep]
            log(f"[{k}] shape={df.shape} range={df.index.min()} ~ {df.index.max()}")
            df.to_csv(os.path.join(OUT, fname), float_format=fmt, compression="gzip")
            log(f"saved {fname}")
            return
        except Exception:
            log(f"!! {k} 失敗\n" + traceback.format_exc()[-600:])


if not ONLY_EXTRA:
    # 1) 事件表
    save_event_table("disposal_information", "disposal_information.csv.gz")
    save_event_table("trading_attention", "trading_attention.csv.gz")
    save_event_table("company_basic_info", "company_basic_info.csv.gz")
    # 2) 價格表（寬表，2016 起）
    for key, fname in [("price:開盤價", "price_open.csv.gz"),
                       ("price:最高價", "price_high.csv.gz"),
                       ("price:最低價", "price_low.csv.gz"),
                       ("price:收盤價", "price_close.csv.gz")]:
        save_wide(key, fname)
    save_wide("price:成交股數", "price_volume.csv.gz", "%.0f")

# 3) 第六款：本益比、股價淨值比
save_wide("price_earning_ratio:本益比", "pe.csv.gz")
save_wide("price_earning_ratio:股價淨值比", "pb.csv.gz")
# 4) 第七款：融資融券
for key, fname in [("margin_transactions:融資今日餘額", "margin_long.csv.gz"),
                   ("margin_transactions:融資限額", "margin_long_limit.csv.gz"),
                   ("margin_transactions:融券今日餘額", "margin_short.csv.gz"),
                   ("margin_transactions:融券限額", "margin_short_limit.csv.gz")]:
    save_wide(key, fname, "%.0f")

# 5) 股票期貨標的（期交所目前清單）
try:
    import re as _re
    import requests
    html = requests.get("https://www.taifex.com.tw/cht/2/stockLists", timeout=30,
                        headers={"User-Agent": "Mozilla/5.0"}).text
    rows = []
    for tr in _re.findall(r"<tr[^>]*>(.*?)</tr>", html, _re.S):
        cells = [_re.sub(r"<[^>]+>|&nbsp;|\s+", "", c) for c in _re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, _re.S)]
        if len(cells) >= 9 and _re.fullmatch(r"[0-9A-Z]{4,6}", cells[2] or ""):
            rows.append(cells[:12])
    cols = ["商品代碼", "標的證券", "證券代號", "簡稱", "股票期貨", "股票選擇權", "週選", "上市普通股", "上櫃普通股", "上市ETF", "上櫃ETF", "股數"]
    fut = pd.DataFrame(rows, columns=cols[:len(rows[0])] if rows else cols)
    if len(fut) < 50:
        raise RuntimeError(f"期交所清單只抓到 {len(fut)} 筆（可能被擋），改用備用清單")
    fut.to_csv(os.path.join(OUT, "stock_futures.csv"), index=False, encoding="utf-8-sig")
    log(f"saved stock_futures.csv rows={len(fut)}")
    log(fut.head(5).to_string())
except Exception:
    log("!! 股票期貨清單失敗\n" + traceback.format_exc()[-600:])
    # 期交所網站常擋海外主機（例如 GitHub Actions），改用 repo 內的備用清單
    import shutil
    fb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_futures_fallback.csv")
    if os.path.exists(fb):
        shutil.copy(fb, os.path.join(OUT, "stock_futures.csv"))
        log("使用備用股票期貨清單 stock_futures_fallback.csv")

log("DONE")
LOG.close()
print("\n完成！檔案在：", OUT)
