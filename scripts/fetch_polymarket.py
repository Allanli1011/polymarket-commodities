"""抓取 Polymarket 商品盘口:发现 → 解析标的/阈值 → 拉概率历史。

输出 data/raw/polymarket.json,供 build_data.py 合并。
所有接口均为只读、免 key、可服务端调用。
"""
from __future__ import annotations

from datetime import datetime, timezone

import common as c


def _market_url(market: dict) -> str:
    slug = market.get("slug", "")
    return f"https://polymarket.com/market/{slug}" if slug else "https://polymarket.com"


def discover_markets(cfg: dict) -> list[dict]:
    """按配置返回要追踪的盘口原始对象列表(已去重)。"""
    s = cfg["settings"]
    mode = s.get("mode", "auto")
    by_slug: dict[str, dict] = {}

    if mode == "auto":
        data = c.get_json(
            f"{c.GAMMA}/markets",
            params={
                "tag_id": s["commodities_tag_id"],
                "active": "true",
                "closed": "false",
                "limit": 200,
                "order": "volume24hr",
                "ascending": "false",
            },
        )
        min_vol = float(s.get("min_volume_24hr", 0))
        for m in data:
            vol = float(m.get("volume24hr") or 0)
            if vol < min_vol:
                continue
            # 只保留能映射到标的物的(等于过滤掉非商品/无法识别的)
            parsed = c.parse_title(m.get("question", ""))
            if c.resolve_underlying(m.get("question", ""), parsed, cfg) is None:
                continue
            by_slug[m["slug"]] = m
            if len(by_slug) >= int(s.get("top_n", 12)):
                break

    # pinned:无论哪种模式都强制纳入
    for slug in cfg.get("pinned", []) or []:
        if slug in by_slug:
            continue
        res = c.get_json(f"{c.GAMMA}/markets", params={"slug": slug})
        if isinstance(res, list) and res:
            by_slug[slug] = res[0]

    return list(by_slug.values())


def fetch_prob_history(token_id: str, cfg: dict) -> list[dict]:
    """拉某个结果 token 的概率历史 → [{t: 'YYYY-MM-DD', v: 0..100}]。"""
    s = cfg["settings"]
    try:
        res = c.get_json(
            f"{c.CLOB}/prices-history",
            params={
                "market": token_id,
                "interval": s.get("polymarket_history_interval", "max"),
                "fidelity": s.get("polymarket_history_fidelity", 1440),
            },
        )
    except Exception as e:  # noqa: BLE001
        print(f"    ! 概率历史抓取失败 token={token_id[:12]}…: {e}")
        return []

    out = []
    for pt in res.get("history", []):
        ts = pt.get("t")
        p = pt.get("p")
        if ts is None or p is None:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({"t": day, "v": round(float(p) * 100, 2)})
    # 同一天多点时保留最后一个(日级 fidelity 下通常已是每天一点)
    dedup: dict[str, dict] = {pt["t"]: pt for pt in out}
    return [dedup[k] for k in sorted(dedup)]


def load_cached_markets() -> list[dict]:
    """从 raw 缓存或已发布的 site/data.json 恢复盘口,供 API 失败时兜底。"""
    raw = c.read_json(c.RAW_POLYMARKET, {}) or {}
    raw_markets = raw.get("markets") or []
    if raw_markets:
        return [{**m, "stale": True} for m in raw_markets]

    site = c.read_json(c.DATA_JSON, {}) or {}
    cached = []
    for m in site.get("markets", []) or []:
        key = m.get("key")
        if not key:
            continue
        underlying = None
        if m.get("symbol"):
            underlying = {
                "symbol": m.get("symbol"),
                "label": m.get("commodity"),
                "unit": m.get("unit"),
                "code": m.get("commodity_code"),
            }
        cached.append({
            "key": key,
            "slug": key,
            "question": m.get("title"),
            "polymarket_url": m.get("polymarket_url"),
            "end_date": m.get("end_date"),
            "track_outcome": m.get("track_outcome"),
            "direction": m.get("direction"),
            "threshold": m.get("threshold"),
            "underlying": underlying,
            "volume24hr": m.get("volume24hr"),
            "volume": m.get("volume"),
            "latest_prob": m.get("latest_prob"),
            "prob": m.get("prob") or [],
            "stale": True,
        })
    return cached


def main() -> None:
    cfg = c.load_config()
    c.ensure_dirs()
    track_outcome = cfg["settings"].get("track_outcome", "Yes")

    discovery_stale = False
    try:
        markets = discover_markets(cfg)
        print(f"发现 {len(markets)} 个待追踪盘口")
    except Exception as e:  # noqa: BLE001
        markets = load_cached_markets()
        if not markets:
            raise
        discovery_stale = True
        print(f"  ! 盘口发现失败,使用缓存兜底({len(markets)} 个盘口): {e}")

    results = []
    for m in markets:
        q = m.get("question") or ""
        outcomes = c.parse_json_field(m.get("outcomes")) or []
        prices = c.parse_json_field(m.get("outcomePrices")) or []
        token_ids = c.parse_json_field(m.get("clobTokenIds")) or []
        if not outcomes or not token_ids:
            cached_prob = m.get("prob") or []
            if cached_prob:
                print(f"  ~ 缓存兜底:{q[:60]}")
                results.append({
                    "key": m.get("key") or m.get("slug"),
                    "slug": m.get("slug") or m.get("key"),
                    "question": q,
                    "polymarket_url": m.get("polymarket_url") or _market_url(m),
                    "end_date": m.get("end_date") or m.get("endDateIso") or m.get("endDate"),
                    "track_outcome": m.get("track_outcome") or track_outcome,
                    "direction": m.get("direction"),
                    "threshold": m.get("threshold"),
                    "underlying": m.get("underlying"),
                    "volume24hr": m.get("volume24hr"),
                    "volume": m.get("volume"),
                    "latest_prob": m.get("latest_prob") if m.get("latest_prob") is not None else cached_prob[-1]["v"],
                    "prob": cached_prob,
                    "stale": True,
                })
                continue
            print(f"  跳过(缺 outcomes/tokens):{q[:50]}")
            continue

        # 按标签取结果 token,取不到则退回第一个
        try:
            idx = outcomes.index(track_outcome)
        except ValueError:
            idx = 0
        token_id = token_ids[idx] if idx < len(token_ids) else token_ids[0]
        outcome_price = None
        if idx < len(prices):
            try:
                outcome_price = round(float(prices[idx]) * 100, 2)
            except (TypeError, ValueError):
                outcome_price = None

        parsed = c.parse_title(q)
        underlying = c.resolve_underlying(q, parsed, cfg)

        print(f"  · {q[:60]}  →  {underlying['symbol'] if underlying else '?'}")
        prob = fetch_prob_history(token_id, cfg)

        results.append({
            "key": m["slug"],
            "slug": m["slug"],
            "question": q,
            "polymarket_url": _market_url(m),
            "end_date": m.get("endDateIso") or m.get("endDate"),
            "track_outcome": outcomes[idx] if idx < len(outcomes) else track_outcome,
            "direction": parsed.get("direction"),
            "threshold": parsed.get("threshold"),
            "underlying": underlying,          # {symbol,label,unit,code} 或 None
            "volume24hr": round(float(m.get("volume24hr") or 0)),
            "volume": round(float(m.get("volume") or 0)),
            "latest_prob": (prob[-1]["v"] if prob else outcome_price),
            "prob": prob,
            "stale": bool(m.get("stale") or discovery_stale),
        })

    c.write_json(c.RAW_POLYMARKET, {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "markets": results,
    })
    print(f"写出 {c.RAW_POLYMARKET}({len(results)} 个盘口)")


if __name__ == "__main__":
    main()
