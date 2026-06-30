"""辅助脚本:列出当前 "Commodities" 标签下的活跃盘口,按 24h 成交量排序。

用途:看当前有哪些商品盘口、它们的 slug 与解析出的标的/阈值,方便:
  - 决定要不要 pin 某些盘口到 config/markets.yml
  - 排查标题解析是否正确

用法:  python scripts/discover.py [数量]
"""
from __future__ import annotations

import sys

import common as c


def main() -> None:
    cfg = c.load_config()
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    data = c.get_json(
        f"{c.GAMMA}/markets",
        params={
            "tag_id": cfg["settings"]["commodities_tag_id"],
            "active": "true",
            "closed": "false",
            "limit": 200,
            "order": "volume24hr",
            "ascending": "false",
        },
    )
    print(f"{'24h成交量':>10}  {'标的':<14} {'方向':<5} {'阈值':>9}  到期        盘口标题 / slug")
    print("-" * 120)
    shown = 0
    for m in data:
        q = m.get("question", "")
        parsed = c.parse_title(q)
        u = c.resolve_underlying(q, parsed, cfg)
        vol = round(float(m.get("volume24hr") or 0))
        sym = (u or {}).get("symbol", "—")
        thr = parsed.get("threshold")
        thr_s = f"{thr:,.2f}" if thr is not None else "—"
        end = (m.get("endDateIso") or "")[:10]
        flag = " " if u else "x"  # x = 解析不到标的,auto 模式会被过滤
        print(f"{vol:>10}  {sym:<14} {str(parsed.get('direction') or '—'):<5} {thr_s:>9}  {end:<10} {flag} {q[:62]}")
        print(f"{'':>10}  {'':<14} {'':<5} {'':>9}  {'':<10}   slug: {m.get('slug')}")
        shown += 1
        if shown >= limit:
            break
    print(f"\n共 {shown} 行(标 x 的解析不到标的物,auto 模式下会被跳过)")


if __name__ == "__main__":
    main()
