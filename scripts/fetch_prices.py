"""抓取标的物真实价格:Yahoo 主源 → FRED 回退 → 上次缓存兜底。

读 data/raw/polymarket.json 得到需要的标的符号,去重后逐个抓取,
输出 data/raw/prices.json,按符号索引。
"""
from __future__ import annotations

from datetime import datetime, timezone

import common as c

# 部分符号的 FRED 回退序列(仅能源较可靠;贵金属 FRED 日序常被反爬/为月度,故不设)
FRED_FALLBACK = {
    "CL=F": "DCOILWTICO",
    "BZ=F": "DCOILBRENTEU",
    "NG=F": "DHHNGSP",
}


def fetch_yahoo(symbol: str, cfg: dict) -> list[dict]:
    """Yahoo /v8/chart → [{t:'YYYY-MM-DD', v: float}]。query1/query2 互备。"""
    s = cfg["settings"]
    params = {"interval": s.get("price_interval", "1d"), "range": s.get("price_range", "1y")}
    last_err = None
    for host in c.YAHOO_HOSTS:
        try:
            res = c.get_json(f"{host}/v8/finance/chart/{symbol}", params=params, tries=3)
            result = (res.get("chart", {}).get("result") or [None])[0]
            if not result:
                continue
            meta = result.get("meta", {})
            # 单位换算:谷物等以美分计价(currency == 'USX'),换成美元主单位
            scale = 0.01 if meta.get("currency") == "USX" else 1.0
            ts = result.get("timestamp") or []
            quote = (result.get("indicators", {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []
            out = []
            for t, cl in zip(ts, closes):
                if cl is None:
                    continue
                day = datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d")
                out.append({"t": day, "v": round(float(cl) * scale, 4)})
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            last_err = e
    print(f"    ! Yahoo 抓取失败 {symbol}: {last_err}")
    return []


def fetch_fred(symbol: str) -> list[dict]:
    sid = FRED_FALLBACK.get(symbol)
    if not sid:
        return []
    txt = c.get_text(c.FRED_CSV.format(sid=sid))
    if not txt or "," not in txt or "<html" in txt[:200].lower():
        return []
    out = []
    for line in txt.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        day, val = parts[0].strip(), parts[1].strip()
        if val in ("", "."):
            continue
        try:
            out.append({"t": day, "v": round(float(val), 4)})
        except ValueError:
            continue
    return out


def main() -> None:
    cfg = c.load_config()
    c.ensure_dirs()

    poly = c.read_json(c.RAW_POLYMARKET, {"markets": []})
    cache = c.read_json(c.RAW_PRICES, {})  # 上次成功的价格,用于兜底

    # 收集去重的标的符号及其展示信息
    symbols: dict[str, dict] = {}
    for m in poly.get("markets", []):
        u = m.get("underlying")
        if u and u.get("symbol"):
            symbols[u["symbol"]] = {"label": u.get("label", u["symbol"]), "unit": u.get("unit", "")}

    print(f"需要抓取 {len(symbols)} 个标的符号:{', '.join(symbols) or '(无)'}")
    out: dict[str, dict] = {}
    for sym, meta in symbols.items():
        series = fetch_yahoo(sym, cfg)
        source = "yahoo"
        if not series:
            series = fetch_fred(sym)
            source = "fred" if series else source
        stale = False
        if not series and sym in cache and cache[sym].get("price"):
            series = cache[sym]["price"]
            source = cache[sym].get("source", "cache")
            stale = True
            print(f"    ~ {sym} 用缓存兜底({len(series)} 点)")
        latest = series[-1]["v"] if series else None
        print(f"  · {sym:<6} {meta['label']:<12} 点数={len(series):<4} 现价={latest} 源={source}{' [stale]' if stale else ''}")
        out[sym] = {
            "symbol": sym,
            "label": meta["label"],
            "unit": meta["unit"],
            "source": source,
            "stale": stale,
            "latest": latest,
            "price": series,
        }

    c.write_json(c.RAW_PRICES, out)
    print(f"写出 {c.RAW_PRICES}")


if __name__ == "__main__":
    main()
