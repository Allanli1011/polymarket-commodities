"""合并 Polymarket 概率与标的物价格 → site/data.json(页面唯一数据源)。

- 把每个盘口的概率序列与其标的价格序列对齐成按日表(供 tooltip 同日取值)。
- 计算 latest / 距阈值距离 / stale 标记。
- 任一序列缺失时回退上一份 data.json,保证页面永不空白。
"""
from __future__ import annotations

from datetime import datetime, timezone

import common as c


def _round3(x):
    return None if x is None else round(x, 3)


def _round2(x):
    return None if x is None else round(x, 2)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / (sx ** 0.5 * sy ** 0.5)


def _paired_on_price_dates(prob: list[dict], price: list[dict]) -> tuple[list, list]:
    """把概率前向填充到每个价格日(交易日),得到一一对应的 (prob, price)。"""
    if not prob or not price:
        return [], []
    prob_sorted = sorted(prob, key=lambda x: x["t"])
    price_sorted = sorted(price, key=lambda x: x["t"])
    first_prob = prob_sorted[0]["t"]
    last_prob = prob_sorted[-1]["t"]
    out_p, out_pr = [], []
    j, last = 0, None
    for pr in price_sorted:
        if pr["t"] > last_prob:
            break
        while j < len(prob_sorted) and prob_sorted[j]["t"] <= pr["t"]:
            last = prob_sorted[j]["v"]
            j += 1
        if last is not None and pr["t"] >= first_prob:
            out_p.append(last)
            out_pr.append(pr["v"])
    return out_p, out_pr


def _lagged_corr(dpr: list[float], dp: list[float], k: int) -> float | None:
    """价格变动领先概率变动 k 天(k>0)时的相关性。配对 dpr[t] 与 dp[t+k]。"""
    if k >= 0:
        a, b = dpr[: len(dpr) - k], dp[k:]
    else:
        a, b = dpr[-k:], dp[: len(dp) + k]
    if len(a) < 5:
        return None
    return pearson(a, b)


def compute_stats(prob: list[dict], price: list[dict]) -> dict:
    """概率 vs 价格的关系指标:水平相关、日变动相关、领先滞后。"""
    p, pr = _paired_on_price_dates(prob, price)
    n = len(p)
    base = {"n": n, "corr_levels": None, "corr_changes": None,
            "lead_lag_days": None, "lead_lag_corr": None}
    if n < 8:
        return base
    base["corr_levels"] = _round3(pearson(p, pr))
    dp = [p[i + 1] - p[i] for i in range(len(p) - 1)]
    dpr = [pr[i + 1] - pr[i] for i in range(len(pr) - 1)]
    base["corr_changes"] = _round3(pearson(dp, dpr))
    best_k, best_c = None, None
    for k in range(-7, 8):
        c = _lagged_corr(dpr, dp, k)
        if c is None:
            continue
        if best_c is None or abs(c) > abs(best_c):
            best_k, best_c = k, c
    if best_c is not None:
        base["lead_lag_days"] = best_k       # >0 价格领先概率;<0 概率领先价格
        base["lead_lag_corr"] = _round3(best_c)
    return base


def series_change(series: list[dict]) -> dict:
    """最近两个有效点之间的变化。概率的 abs 代表百分点,价格的 abs 代表价格差。"""
    valid = [pt for pt in sorted(series, key=lambda x: x["t"]) if pt.get("v") is not None]
    if len(valid) < 2:
        return {"from_t": None, "to_t": None, "from": None, "to": None,
                "abs": None, "pct": None}
    prev, cur = valid[-2], valid[-1]
    diff = cur["v"] - prev["v"]
    pct = None if prev["v"] == 0 else diff / prev["v"] * 100
    return {
        "from_t": prev["t"],
        "to_t": cur["t"],
        "from": prev["v"],
        "to": cur["v"],
        "abs": _round2(diff),
        "pct": _round2(pct),
    }


def detect_prob_spikes(prob: list[dict], threshold_pp: float) -> list[dict]:
    """识别相邻概率点之间的绝对变化超过阈值的日期。"""
    valid = [pt for pt in sorted(prob, key=lambda x: x["t"]) if pt.get("v") is not None]
    out = []
    for prev, cur in zip(valid, valid[1:]):
        diff = cur["v"] - prev["v"]
        if abs(diff) < threshold_pp:
            continue
        pct = None if prev["v"] == 0 else diff / prev["v"] * 100
        out.append({
            "t": cur["t"],
            "v": cur["v"],
            "prev_t": prev["t"],
            "prev": prev["v"],
            "change_pp": _round2(diff),
            "change_pct": _round2(pct),
        })
    return out


def build_aligned(prob: list[dict], price: list[dict]) -> list[dict]:
    """对重叠日期窗口内的概率日期,取该日(含之前)最近的价格。"""
    if not prob:
        return []
    price_sorted = sorted(price, key=lambda x: x["t"])
    if not price_sorted:
        return []
    first_price = price_sorted[0]["t"]
    last_price_day = price_sorted[-1]["t"]
    out = []
    j = 0
    last_price_value = None
    for pp in sorted(prob, key=lambda x: x["t"]):
        if pp["t"] < first_price:
            continue
        if pp["t"] > last_price_day:
            break
        while j < len(price_sorted) and price_sorted[j]["t"] <= pp["t"]:
            last_price_value = price_sorted[j]["v"]
            j += 1
        out.append({"t": pp["t"], "prob": pp["v"], "price": last_price_value})
    return out


def main() -> None:
    cfg = c.load_config()
    c.ensure_dirs()
    spike_threshold_pp = float(cfg.get("settings", {}).get("prob_spike_abs_pp", 10))

    poly = c.read_json(c.RAW_POLYMARKET, {"markets": []})
    prices = c.read_json(c.RAW_PRICES, {})
    prev = {m["key"]: m for m in (c.read_json(c.DATA_JSON, {}) or {}).get("markets", [])}

    markets = []
    for m in poly.get("markets", []):
        u = m.get("underlying") or {}
        sym = u.get("symbol")
        pinfo = prices.get(sym, {}) if sym else {}
        prob = m.get("prob") or []
        price = pinfo.get("price") or []

        prev_m = prev.get(m["key"], {})
        stale = bool(pinfo.get("stale") or m.get("stale"))

        # 概率缺失 → 回退上一份
        if not prob and prev_m.get("prob"):
            prob = prev_m["prob"]
            stale = True
        # 价格缺失 → 回退上一份
        if not price and prev_m.get("price"):
            price = prev_m["price"]
            stale = True

        latest_prob = prob[-1]["v"] if prob else m.get("latest_prob")
        latest_price = price[-1]["v"] if price else pinfo.get("latest")

        markets.append({
            "key": m["key"],
            "title": m.get("question"),
            "polymarket_url": m.get("polymarket_url"),
            "end_date": m.get("end_date"),
            "track_outcome": m.get("track_outcome"),
            "direction": m.get("direction"),
            "threshold": m.get("threshold"),
            "commodity": u.get("label"),
            "commodity_code": u.get("code"),
            "symbol": sym,
            "unit": u.get("unit"),
            "price_source": pinfo.get("source"),
            "volume24hr": m.get("volume24hr"),
            "volume": m.get("volume"),
            "latest_prob": latest_prob,
            "latest_price": latest_price,
            "stale": stale,
            "prob": prob,
            "price": price,
            "aligned": build_aligned(prob, price),
            "changes": {
                "prob": series_change(prob),
                "price": series_change(price),
            },
            "prob_spikes": detect_prob_spikes(prob, spike_threshold_pp),
            "stats": compute_stats(prob, price),
        })

    # 排序:成交量降序
    markets.sort(key=lambda x: (x.get("volume24hr") or 0), reverse=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_count": len(markets),
        "markets": markets,
    }
    c.write_json(c.DATA_JSON, out)
    print(f"写出 {c.DATA_JSON}({len(markets)} 个盘口)")


if __name__ == "__main__":
    main()
