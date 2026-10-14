# -*- coding: utf-8 -*-
"""
處置股分析 - 步驟 2：把 finlab_export 的原始資料算成網頁要用的資料，並產生網頁
用法：
    python process.py [finlab_export 資料夾] [輸出資料夾]
預設讀 ./finlab_export，輸出 ./處置股分析.html 與 ./disposal_data.json
"""
import os
import re
import sys
import json
import datetime as dt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "finlab_export")
OUTDIR = sys.argv[2] if len(sys.argv) > 2 else HERE
TEMPLATE = os.path.join(HERE, "template.html")

REGIME_DATE = pd.Timestamp("2026-08-10")   # 處置新制實施日
ANALYSIS_START = pd.Timestamp("2016-03-01")
# 處置後是否把「已用於處置」的注意紀錄歸零（RESET_AFTER_DISPOSAL）
RESET_AFTER_DISPOSAL = os.environ.get("RESET_AFTER_DISPOSAL", "1") == "1"
SIG_PRE, SIG_POST = 7, 25        # 聽牌訊號需要的 K 棒範圍
EVT_PRE, EVT_POST = 12, 15       # 處置事件需要的 K 棒範圍


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 讀檔
def rd(name, **kw):
    p = os.path.join(SRC, name)
    if not os.path.exists(p):
        return None
    return pd.read_csv(p, compression="gzip", **kw)


def rd_price(name):
    df = rd(name, index_col=0)
    df.index = pd.to_datetime(df.index)
    df.columns = [str(c) for c in df.columns]
    return df.sort_index()


log("讀取價格...")
C = rd_price("price_close.csv.gz")
O = rd_price("price_open.csv.gz").reindex_like(C)
H = rd_price("price_high.csv.gz").reindex_like(C)
L = rd_price("price_low.csv.gz").reindex_like(C)
V = rd_price("price_volume.csv.gz").reindex_like(C)
def rd_opt(name):
    p_ = os.path.join(SRC, name)
    if not os.path.exists(p_):
        return None
    return rd_price(name).reindex_like(C)


PE_ = rd_opt("pe.csv.gz"); PB_ = rd_opt("pb.csv.gz")
ML_ = rd_opt("margin_long.csv.gz"); MLL_ = rd_opt("margin_long_limit.csv.gz")
MS_ = rd_opt("margin_short.csv.gz"); MSL_ = rd_opt("margin_short_limit.csv.gz")
# 開盤缺值(無成交)用收盤補
O = O.fillna(C); H = H.fillna(C); L = L.fillna(C)
DATES = C.index
NDATE = len(DATES)
STOCKS = list(C.columns)
SPOS = {s: i for i, s in enumerate(STOCKS)}
LAST = DATES[-1]
log("價格", C.shape, DATES[0].date(), "~", LAST.date())


def date_to_idx(d, side="left"):
    """日期 -> 交易日序號（非交易日往後找）"""
    return int(DATES.searchsorted(pd.Timestamp(d), side=side))


def norm_sid(x):
    m = re.match(r"\s*([0-9A-Z]{4,6})", str(x))
    return m.group(1) if m else None


def find_col(df, *keys, exclude=()):
    for k in keys:
        for c in df.columns:
            if k in str(c) and not any(e in str(c) for e in exclude):
                return c
    return None


ROC_RE = re.compile(r"(\d{2,4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})")


def parse_date(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return pd.NaT
    s = str(x).strip()
    m = ROC_RE.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 1911:
            y += 1911
        try:
            return pd.Timestamp(y, mo, d)
        except Exception:
            return pd.NaT
    try:
        return pd.Timestamp(s)
    except Exception:
        return pd.NaT


CN_NUM = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "十": 10, "二十": 20, "二十五": 25,
          "六十": 60, "四十五": 45}


def parse_minutes(*texts):
    for t in texts:
        if t is None or (isinstance(t, float) and np.isnan(t)):
            continue
        s = str(t)
        m = re.search(r"(\d+)\s*分", s)
        if m:
            return int(m.group(1))
        m = re.search(r"每\s*([一二兩三四五六十]+)\s*分", s)
        if m:
            w = m.group(1)
            if w in CN_NUM:
                return CN_NUM[w]
        try:
            v = float(s)
            if 0 < v < 100:
                return int(v)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------- 名稱
names = {}
shares = {}
industry = {}
cb = rd("company_basic_info.csv.gz")
if cb is not None:
    sc = find_col(cb, "stock_id")
    nc = find_col(cb, "公司簡稱", "名稱")
    mc = find_col(cb, "市場別", "market")
    shc = find_col(cb, "已發行普通股數")
    ic = find_col(cb, "產業類別")
    for _, r in cb.iterrows():
        s = norm_sid(r[sc]) if sc else None
        if not s:
            continue
        mk = str(r[mc]) if mc else ""
        mk = {"sii": "上市", "otc": "上櫃", "rotc": "興櫃", "pub": "公開"}.get(mk, mk)
        names[s] = [str(r[nc]) if nc else "", mk]
        industry[s] = str(r[ic]) if ic else ""
        try:
            shares[s] = float(str(r[shc]).replace(",", "")) / 1000.0 if shc else None
        except Exception:
            pass

# ---------------------------------------------------------------- 股票期貨標的
futset = set()
fp = os.path.join(SRC, "stock_futures.csv")
if os.path.exists(fp):
    fu = pd.read_csv(fp, dtype=str)
    cc = find_col(fu, "證券代號")
    fc_ = find_col(fu, "股票期貨")
    for _, r in fu.iterrows():
        if fc_ is None or "是" in str(r[fc_]):
            futset.add(str(r[cc]).strip())
log("股票期貨標的", len(futset))

# ---------------------------------------------------------------- 注意股
log("讀取注意股...")
att = rd("trading_attention.csv.gz")
a_date = find_col(att, "date") or att.columns[0]
a_sid = find_col(att, "stock_id")
a_name = find_col(att, "證券名稱", "名稱")
# 文字欄：優先找「注意交易資訊」，否則平均字串最長的欄
a_txt = find_col(att, "注意交易資訊", "資訊", "原因")
if a_txt is None:
    lens = {c: att[c].astype(str).str.len().mean() for c in att.columns if att[c].dtype == object}
    a_txt = max(lens, key=lens.get)
log("注意股欄位:", a_date, a_sid, a_name, a_txt)
att["_sid"] = att[a_sid].map(norm_sid)
att["_d"] = pd.to_datetime(att[a_date].map(parse_date))
att["_txt"] = att[a_txt].astype(str)
if a_name:
    for s, n in zip(att["_sid"], att[a_name]):
        if s and s not in names:
            names[s] = [str(n), ""]

CN_K = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15}
# 計入「連5日 / 10日6次 / 30日12次」的款別（處置作業要點：第一款至第八款）
COUNT_SET = set(int(x) for x in os.environ.get("COUNT_SET", "1,2,3,4,5,6,7,8").split(","))
RE_K = re.compile(r"第([一二三四五六七八九十]+|\d+)款")


def kuan(t):
    out = set()
    for x in RE_K.findall(str(t)):
        out.add(int(x) if x.isdigit() else CN_K.get(x, 0))
    return out


def classify(t):
    ks = kuan(t)
    if ks:
        return (1 in ks), bool(ks & COUNT_SET), (13 in ks)
    # 舊格式沒有款別：用文字判斷
    c1 = bool(re.search(r"(六|6)個營業日[^。；;]{0,30}累積[^。；;]{0,20}(漲|跌)", t))
    return c1, True, "當日沖銷" in t


OLD_NUMBERING_END = pd.Timestamp(os.environ.get("OLD_NUMBERING_END", "2020-01-01"))
OLD_EXCLUDE = set(int(x) for x in os.environ.get("OLD_EXCLUDE", "7").split(",") if x)


def classify_row(t, d):
    c1, cnt, dt_ = classify(t)
    if d < OLD_NUMBERING_END and OLD_EXCLUDE:
        ks = kuan(t)
        if ks:  # 2020 以前款別編號不同：第6/7款不計入累計
            cnt = bool(ks & (COUNT_SET - OLD_EXCLUDE))
    return c1, cnt, dt_


cls = [classify_row(t, d) for t, d in zip(att["_txt"], att["_d"])]
att["_c1"] = [x[0] for x in cls]
att["_cnt"] = [x[1] for x in cls]
att["_dt"] = [x[2] for x in cls]
att = att[att["_sid"].isin(SPOS) & att["_d"].notna() & (att["_d"] >= DATES[0])].copy()
att["_i"] = att["_d"].map(lambda d: date_to_idx(d))
att = att[att["_i"] < NDATE]
log("注意紀錄", len(att), "第1款", int(att["_c1"].sum()), "計入", int(att["_cnt"].sum()))

N_any = np.zeros((NDATE, len(STOCKS)), dtype=np.int8)
N_c1 = np.zeros_like(N_any)
for i, s, c1, cn in zip(att["_i"], att["_sid"], att["_c1"], att["_cnt"]):
    j = SPOS[s]
    if cn:
        N_any[i, j] = 1
    if c1:
        N_c1[i, j] = 1
N_raw = N_any.copy()

# ---------------------------------------------------------------- 處置事件
log("讀取處置股...")
dis = rd("disposal_information.csv.gz")
d_date = find_col(dis, "date") or dis.columns[0]
d_sid = find_col(dis, "stock_id")
d_st = find_col(dis, "開始")
d_en = find_col(dis, "結束")
d_min = find_col(dis, "分時", "撮合")
d_why = find_col(dis, "條件", "原因")
d_cont = find_col(dis, "內容", "措施")
d_period = find_col(dis, "期間", "處置日期")
d_act = find_col(dis, "措施")
log("處置欄位:", d_date, d_sid, d_st, d_en, d_min, d_why, d_cont, d_period)

events = []
for _, r in dis.iterrows():
    s = norm_sid(r[d_sid])
    if s not in SPOS:
        continue
    st = parse_date(r[d_st]) if d_st else pd.NaT
    en = parse_date(r[d_en]) if d_en else pd.NaT
    if (pd.isna(st) or pd.isna(en)) and d_period:
        ds = ROC_RE.findall(str(r[d_period]))
        if len(ds) >= 2:
            st = parse_date("/".join(ds[0])); en = parse_date("/".join(ds[1]))
    ann = parse_date(r[d_date])
    if pd.isna(st):
        continue
    if pd.isna(en):
        en = st
    why = str(r[d_why]) if d_why and pd.notna(r[d_why]) else ""
    cont = str(r[d_cont]) if d_cont and pd.notna(r[d_cont]) else ""
    mins = parse_minutes(r[d_min] if d_min else None, cont, why)
    if re.search(r"(轉\(交\)換|督導)", why):
        continue
    act = str(r[d_act]) if d_act and pd.notna(r[d_act]) else ""
    order = 2 if re.search(r"(第二次|收足全部)", act) else 1
    if order == 1 and mins in (20, 25, 45, 60) and st < REGIME_DATE:
        order = 2
    events.append(dict(s=s, ann=ann, st=st, en=en, m=mins, ord=order,
                       why=re.sub(r"\s+", " ", why)[:160]))

ev = pd.DataFrame(events).drop_duplicates(["s", "st"]).sort_values(["st", "s"]).reset_index(drop=True)
ev = ev[ev["st"] >= ANALYSIS_START - pd.Timedelta(days=60)].reset_index(drop=True)
log("處置事件", len(ev))

bd_future = pd.bdate_range(LAST + pd.Timedelta(days=1), periods=40)


def idx_or_future(d):
    """交易日序號；未來日期回傳 NDATE-1 之後的估計序號"""
    if d <= LAST:
        return date_to_idx(d)
    return NDATE - 1 + int(bd_future.searchsorted(d) + 1)


ev["sti"] = ev["st"].map(idx_or_future)
ev["eni"] = ev["en"].map(lambda d: idx_or_future(d) if d > LAST else int(DATES.searchsorted(d, side="right") - 1))
ev["n"] = ev["eni"] - ev["sti"] + 1
ev = ev[ev["n"] >= 1].reset_index(drop=True)

# 處置期間標記 & 注意紀錄歸零
in_disp = np.zeros_like(N_any, dtype=bool)
for _, e in ev.iterrows():
    j = SPOS[e.s]
    a, b = e.sti, min(e.eni, NDATE - 1)
    if a < NDATE:
        in_disp[a:b + 1, j] = True

# ---------------------------------------------------------------- 聽牌狀態計算
log("計算聽牌狀態...")


def runs(M):
    R = np.zeros(M.shape, dtype=np.int16)
    R[0] = M[0]
    for i in range(1, M.shape[0]):
        R[i] = (R[i - 1] + 1) * M[i]
    return R


def rsum(M, w):
    cs = np.cumsum(M.astype(np.int32), axis=0)
    out = cs.copy()
    out[w:] = cs[w:] - cs[:-w]
    return out


def compute_state(Nany, Nc1, reset_mask=None):
    A = Nany.copy(); B = Nc1.copy()
    if reset_mask is not None:
        A[reset_mask] = 0; B[reset_mask] = 0
    return dict(r1=runs(B), ra=runs(A), c9=rsum(A, 9), c29=rsum(A, 29),
                c8=rsum(A, 8), c28=rsum(A, 28), c10=rsum(A, 10), c30=rsum(A, 30))


# 歸零：每次處置的觸發日(含)以前的注意紀錄，不再計入觸發日之後的累計
# 做法：對每檔股票記錄「最近一次觸發日」，計算時只看該日之後的紀錄。
# 實作上用逐日掃描（股票數 x 日數），以 numpy 欄向量處理
last_trig = np.full(len(STOCKS), -1, dtype=np.int32)
trig_at = {}
for _, e in ev.iterrows():
    T = e.sti - 1
    if 0 <= T < NDATE:
        trig_at.setdefault(T, []).append(SPOS[e.s])

# 逐日計算（含歸零）
NS = len(STOCKS)
r1 = np.zeros((NDATE, NS), np.int16); ra = np.zeros_like(r1)
c9 = np.zeros_like(r1); c29 = np.zeros_like(r1); c8 = np.zeros_like(r1); c28 = np.zeros_like(r1)
c10 = np.zeros_like(r1); c30 = np.zeros_like(r1)
csA = np.cumsum(N_any.astype(np.int32), axis=0)


def wsum(i, w, lo):
    """sum of N_any over (i-w, i], but only rows > lo (per stock)"""
    start = np.maximum(i - w, lo)          # exclusive lower bound per stock
    hi = csA[i]
    base = np.where(start >= 0, csA[np.clip(start, 0, None), np.arange(NS)], 0)
    return hi - base


prev_r1 = np.zeros(NS, np.int16); prev_ra = np.zeros(NS, np.int16)
for i in range(NDATE):
    lo = last_trig if RESET_AFTER_DISPOSAL else np.full(NS, -1, np.int32)
    a = N_any[i].astype(np.int16); b = N_c1[i].astype(np.int16)
    valid = i > lo
    a = a * valid; b = b * valid
    prev_r1 = (prev_r1 + 1) * b
    prev_ra = (prev_ra + 1) * a
    r1[i] = prev_r1; ra[i] = prev_ra
    c9[i] = wsum(i, 9, lo); c29[i] = wsum(i, 29, lo)
    c8[i] = wsum(i, 8, lo); c28[i] = wsum(i, 28, lo)
    c10[i] = wsum(i, 10, lo); c30[i] = wsum(i, 30, lo)
    if RESET_AFTER_DISPOSAL and i in trig_at:
        for j in trig_at[i]:
            last_trig[j] = i
            prev_r1[j] = 0; prev_ra[j] = 0
            # 觸發日當天已確定進處置，不再算聽牌
            r1[i, j] = ra[i, j] = c9[i, j] = c29[i, j] = c8[i, j] = c28[i, j] = 0

# 聽牌（差 1 次）旗標：1=連3日第1款 2=連5日 4=10日6次 8=30日12次
F1 = ((r1 >= 2) * 1 + (ra >= 4) * 2 + (c9 >= 5) * 4 + (c29 >= 11) * 8).astype(np.int8)
F2 = ((r1 == 1) * 1 + (ra == 3) * 2 + (c8 >= 4) * 4 + (c28 >= 10) * 8).astype(np.int8)

# ---------------------------------------------------------------- 反向檢查：模型認為該處置、實際有沒有處置
trigT = set()
for _, e in ev.iterrows():
    if 0 <= e.sti - 1 < NDATE:
        trigT.add((int(e.sti - 1), SPOS[e.s]))
pred = np.zeros_like(N_any, dtype=bool)
pred[1:] = (((F1[:-1] & 14) > 0) & (N_any[1:] > 0)) | (((F1[:-1] & 1) > 0) & (N_c1[1:] > 0))
pred &= ~in_disp
a0_ = date_to_idx(ANALYSIS_START)
pi, pj = np.nonzero(pred[a0_:]); pi = pi + a0_
hit = sum((int(i), int(j)) in trigT for i, j in zip(pi, pj))
DIAG = dict(pred=int(len(pi)), hit=int(hit))
log("反向檢查：模型預測觸發", len(pi), "其中實際處置", hit)

# ---------------------------------------------------------------- 輔助數列
Cv = C.values; Ov = O.values; Hv = H.values; Lv = L.values; Vv = V.values
with np.errstate(divide="ignore", invalid="ignore"):
    RET6 = Cv / np.vstack([np.full((6, NS), np.nan), Cv[:-6]]) - 1
    V20 = pd.DataFrame(Vv).rolling(20, min_periods=5).mean().values / 1000.0

# ---------------------------------------------------------------- 訊號
log("整理聽牌訊號...")
a0 = date_to_idx(ANALYSIS_START)
ev_by_stock = {}
for k, e in ev.iterrows():
    ev_by_stock.setdefault(e.s, []).append((e.sti, k))

need = {}  # 需要的價格區段 sid -> list of (a,b)


def want(s, a, b):
    need.setdefault(s, []).append((max(0, a), min(NDATE - 1, b)))


signals = []
ii, jj = np.nonzero(F1[a0:])
ii = ii + a0
order = np.lexsort((ii, jj))
prev_key = None
for t, j in zip(ii[order], jj[order]):
    s = STOCKS[j]
    if np.isnan(Cv[t, j]):
        prev_key = (j, t); continue
    first = not (prev_key == (j, t - 1))
    prev_key = (j, t)
    nxt = -1
    for sti, k in ev_by_stock.get(s, []):
        if t < sti <= t + 10:
            nxt = int(sti - t); break
    signals.append([j, int(t), int(F1[t, j]), int(r1[t, j]), int(ra[t, j]), int(c9[t, j]), int(c29[t, j]),
                    None if np.isnan(RET6[t, j]) else round(float(RET6[t, j]) * 100, 2),
                    None if np.isnan(V20[t, j]) else int(round(V20[t, j])),
                    int(in_disp[t, j]), int(first), nxt])
    want(s, t - SIG_PRE, t + SIG_POST)
log("聽牌訊號", len(signals))

# ---------------------------------------------------------------- 事件輸出
evo = []
cov = {"old": [0, 0], "new": [0, 0]}
for k, e in ev.iterrows():
    if e.st < ANALYSIS_START:
        continue
    j = SPOS[e.s]
    T = e.sti - 1
    tp = int(F1[T - 1, j]) if 1 <= T < NDATE else -1
    rg = 1 if e.st >= REGIME_DATE else 0
    if T < NDATE and tp >= 0:
        key = "new" if rg else "old"
        cov[key][1] += 1
        cov[key][0] += int(tp > 0)
    trig_txt = ""
    if 0 <= T < NDATE:
        rows = att[(att["_sid"] == e.s) & (att["_i"] == T)]
        if len(rows):
            trig_txt = re.sub(r"\s+", " ", rows["_txt"].iloc[0])[:160]
    evo.append(dict(s=j, a=int(date_to_idx(e.ann)) if pd.notna(e.ann) else None,
                    st=int(e.sti), en=int(e.eni), n=int(e.n), m=int(e.m) if pd.notna(e.m) else None, o=int(e.ord), rg=rg,
                    tp=tp, why=e.why, tw=trig_txt,
                    v=None if T >= NDATE or T < 0 or np.isnan(V20[T, j]) else int(round(V20[T, j]))))
    want(e.s, e.sti - EVT_PRE, e.eni + EVT_POST)
log("事件輸出", len(evo), "聽牌覆蓋率", cov)

# ---------------------------------------------------------------- 今日預判
log("計算最新預判...")
t = NDATE - 1
fc = []
recent_att = att[att["_i"] >= t - 35]
indpb = {}
if PB_ is not None:
    cl_t = C.iloc[t]; pb_t = PB_.iloc[t]
    agg = {}
    for s_ in STOCKS:
        shv, pbv, cv = shares.get(s_), pb_t.get(s_), cl_t.get(s_)
        if not shv or pbv is None or not (pbv > 0) or not (cv > 0):
            continue
        key = (names.get(s_, ["", ""])[1], industry.get(s_, ""))
        capv = cv * shv
        a_, b_ = agg.get(key, (0.0, 0.0)); agg[key] = (a_ + capv, b_ + capv / pbv)
    for s_ in STOCKS:
        key = (names.get(s_, ["", ""])[1], industry.get(s_, ""))
        if key in agg and agg[key][1] > 0:
            indpb[s_] = agg[key][0] / agg[key][1]


def fv(M, i, j):
    if M is None or i < 0:
        return None
    v = M.iat[i, j]
    return None if pd.isna(v) else float(v)


def margin_at(i, j):
    l, ll, sh_, shl = fv(ML_, i, j), fv(MLL_, i, j), fv(MS_, i, j), fv(MSL_, i, j)
    ssr = sh_ / l * 100 if l and sh_ is not None and l > 0 else None
    return ssr, (l / ll * 100 if l is not None and ll else None), (sh_ / shl * 100 if sh_ is not None and shl else None)
for j, s in enumerate(STOCKS):
    if in_disp[t, j] and F1[t, j] == 0:
        continue
    f1, f2 = int(F1[t, j]), int(F2[t, j])
    if not (f1 or f2) or np.isnan(Cv[t, j]):
        continue
    rows = recent_att[(recent_att["_sid"] == s) & (recent_att["_i"] > last_trig[j])].sort_values("_i")
    hist = [[int(i), sorted(kuan(txt)), txt[:220]] for i, txt in zip(rows["_i"], rows["_txt"])]

    def cpx(k):
        return None if t - k < 0 or np.isnan(Cv[t - k, j]) else float(Cv[t - k, j])
    with np.errstate(invalid="ignore"):
        dr = Cv[t - 4:t + 1, j] / Cv[t - 5:t, j] - 1
        dr6 = Cv[t - 5:t + 1, j] / Cv[t - 6:t, j] - 1
    av60 = np.nanmean(Vv[max(0, t - 59):t + 1, j]) / 1000.0
    fc.append(dict(s=j, lv=1 if f1 else 2, f1=f1, f2=f2, r1=int(r1[t, j]), ra=int(ra[t, j]),
                   c10=int(c9[t, j]), c30=int(c29[t, j]), cl=float(Cv[t, j]),
                   k5=round(float(np.nansum(dr)) * 100, 2), s6=round(float(np.nansum(dr6)) * 100, 2),
                   b6=cpx(4), b30=cpx(28), b60=cpx(58), b90=cpx(88),
                   av60=None if np.isnan(av60) else round(float(av60), 1),
                   sh=shares.get(s), vt=None if np.isnan(Vv[t, j]) else round(float(Vv[t, j]) / 1000.0, 1),
                   v=None if np.isnan(V20[t, j]) else int(round(V20[t, j])),
                   ind=int(in_disp[t, j]), h=hist,
                   pT=max([int(e_.sti) - 1 for e_ in ev[ev["s"] == s].itertuples() if int(e_.sti) - 1 <= t], default=None),
                   pe=fv(PE_, t, j), pb=fv(PB_, t, j), ipb=round(indpb[s], 3) if s in indpb else None,
                   ssr=margin_at(t, j)[0], ssrmin=(min([x for x in [margin_at(t - k, j)[0] for k in range(6)] if x is not None], default=None)),
                   lu=margin_at(t, j)[1], su=margin_at(t, j)[2]))
    want(s, t - 30, t)
cur = []
for k, e in enumerate(evo):
    if e["en"] > t and e["st"] <= t + 3:
        cur.append(k)
log("差1次", sum(1 for x in fc if x["lv"] == 1), "差2次", sum(1 for x in fc if x["lv"] == 2), "處置中/將處置", len(cur))

# ---------------------------------------------------------------- 價格區段
log("打包價格...")
px = {}
for s, segs in need.items():
    segs.sort()
    merged = []
    for a, b in segs:
        if merged and a <= merged[-1][1] + 15:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    j = SPOS[s]
    out = []
    for a, b in merged:
        def col(M):
            seg = M[a:b + 1, j]
            return [None if np.isnan(x) else round(float(x), 2) for x in seg]
        out.append([a, col(Ov), col(Hv), col(Lv), col(Cv)])
    px[int(j)] = out

used = sorted(set([x[0] for x in signals] + [e["s"] for e in evo] + [f["s"] for f in fc] + list(px.keys())))
stocks_out = {int(j): [STOCKS[j]] + names.get(STOCKS[j], ["", ""])[:2] + [1 if STOCKS[j] in futset else 0] for j in used}

payload = dict(
    meta=dict(generated=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"), last=str(LAST.date()),
              regime=str(REGIME_DATE.date()), regimeIdx=date_to_idx(REGIME_DATE),
              start=str(ANALYSIS_START.date()), reset=RESET_AFTER_DISPOSAL, coverage=cov,
              nAtt=int(len(att)), diag=DIAG, countSet=sorted(COUNT_SET), hasFut=len(futset) > 0),
    dates=[d.strftime("%Y-%m-%d") for d in DATES],
    stocks=stocks_out,
    sigCols=["s", "t", "f", "r1", "ra", "c10", "c30", "ret6", "v", "ind", "first", "nxt"],
    signals=signals,
    events=evo,
    forecast=dict(t=t, rows=fc, cur=cur),
    px=px,
)
def _conv(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, float) and np.isnan(o):
        return None
    raise TypeError(type(o))


js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_conv)
with open(os.path.join(OUTDIR, "disposal_data.json"), "w", encoding="utf-8") as f:
    f.write(js)
log("JSON 大小 %.1f MB" % (len(js.encode()) / 1e6))

if os.path.exists(TEMPLATE):
    html = open(TEMPLATE, encoding="utf-8").read()
    html = html.replace("/*__DATA__*/null", js.replace("</", "<\\/"))
    # 給 Claude 發布用（無外框）
    with open(os.path.join(OUTDIR, "artifact_body.html"), "w", encoding="utf-8") as f:
        f.write(html)
    html = ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '</head><body>' + html + '</body></html>')
    with open(os.path.join(OUTDIR, "處置股分析.html"), "w", encoding="utf-8") as f:
        f.write(html)
    log("已產生 處置股分析.html")
