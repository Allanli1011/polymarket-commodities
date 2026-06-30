# 链上预测市场 × 大宗商品盘口追踪

把 **Polymarket** 上与大宗商品相关盘口的**隐含概率**,与标的物的**真实价格走势**叠加在同一张图里对比追踪。数据由 **GitHub Actions 每日自动刷新**,以**纯静态 HTML** 页面(GitHub Pages)呈现。

> 完整设计文档见 [DESIGN.md](DESIGN.md)。

![dashboard](https://img.shields.io/badge/output-static%20HTML-blue) ![data](https://img.shields.io/badge/data-Polymarket%20%2B%20Yahoo-green)

---

## 它怎么工作

```
Polymarket Gamma/CLOB ─┐
                        ├─► build_data.py ─► site/data.json ─► Chart.js 渲染 ─► GitHub Pages
Yahoo Finance / FRED ──┘        (按日对齐)        (提交留档)      (双轴叠加 + 阈值线)
```

- **预测市场**:Polymarket Gamma API(盘口/当前概率)+ CLOB `/prices-history`(概率历史),只读免 key。
- **标的物价格**:Yahoo Finance `/v8/chart`(主源)+ FRED(回退),覆盖黄金/原油/白银/天然气等。
- **抓取全部在 CI 完成**并烘焙成 `site/data.json`;页面纯静态,只 `fetch` 同源 JSON,无 CORS、无运行时密钥。
- **自动发现**:默认按 24h 成交量自动追踪当前最活跃的商品盘口(盘口按月轮换无需手动维护),标的与阈值从盘口标题自动解析。

---

## 目录结构

```
config/markets.yml        # ★ 改这里调整追踪范围 / 刷新行为
scripts/
  discover.py             # 列出当前商品盘口(辅助排查/选盘)
  fetch_polymarket.py     # 抓盘口元数据 + 概率历史
  fetch_prices.py         # 抓标的物价格(Yahoo→FRED→缓存)
  build_data.py           # 合并 + 按日对齐 → site/data.json
  common.py               # 公共工具(HTTP/解析/配置)
site/                     # ★ 发布到 Pages 的静态站点
  index.html  app.js  style.css  data.json
.github/workflows/refresh.yml   # 每日抓取 + 构建 + 部署
```

---

## 本地运行

需要 Python 3.10+。

```bash
pip install -r requirements.txt

# 生成数据
python scripts/fetch_polymarket.py
python scripts/fetch_prices.py
python scripts/build_data.py

# 本地预览(必须用 HTTP,不能直接 file:// 打开,否则 fetch 失败)
python -m http.server 8000 --directory site
# 浏览器打开 http://localhost:8000
```

辅助:`python scripts/discover.py 40` 列出当前 Commodities 标签下的活跃盘口及其解析结果。

---

## 部署到 GitHub Pages

1. 把本目录推到一个 GitHub 仓库。
2. 仓库 **Settings → Pages → Build and deployment → Source** 选 **GitHub Actions**。
3. 完成。`refresh.yml` 会每天 UTC 02:00 自动跑;也可在 **Actions** 页签手动 `Run workflow`。

**无需任何密钥**——所有数据源都是免 key 的公开接口。

---

## 配置(`config/markets.yml`)

| 项 | 说明 |
|---|---|
| `mode` | `auto`=按成交量自动发现;`pinned`=只追踪 `pinned` 列表 |
| `top_n` | auto 模式追踪的盘口数量 |
| `min_volume_24hr` | auto 模式的成交量下限过滤 |
| `price_range` | Yahoo 拉取区间(如 `1y`/`6mo`) |
| `symbol_map` | 商品代码 → 标的物(Yahoo 符号/展示名/单位) |
| `keyword_map` | 标题无代码时的关键词回退 |
| `pinned` | 始终强制追踪的盘口(按 slug) |

**改刷新频率**:编辑 `.github/workflows/refresh.yml` 里的 `cron`(如每 6 小时 `0 */6 * * *`)。

**加新标的**:在 `symbol_map` 增加「商品代码 → Yahoo 符号」即可,例如 `BZ: { symbol: "BZ=F", label: "Brent 原油", unit: "USD/bbl" }`。

---

## 数据与历史

- 每次刷新把 `site/data.json` 提交回仓库(`[skip ci]` 防触发循环),因此可通过 **git 历史**回溯任意日期的快照。
- 这会让仓库随时间增长。若不想留历史,把 `refresh.yml` 里的 “Commit refreshed data” 步骤删掉即可(数据只存在于最新一次 Pages 产物)。

---

## 关键技术注意点

- Yahoo `/v8/chart` 必须带 `User-Agent`(已在 `common.py` 处理),否则 429。
- 谷物以美分计价(`currency=USX`)、铜以 USD/lb 计价——`fetch_prices.py` 按 `meta.currency` 自动换算。
- Stooq 已加 JS 反爬,CI 不可用,故未采用。
- 前端用 `setTimeout` 而非 `requestAnimationFrame` 绘图,避免后台标签页/无头预览下 rAF 不触发导致图表不画。
- (可选)本机若有 LSEG/Refinitiv 终端,可用其 `commodity_futures` 拉更高质量的结算价/期限结构作增强源;但 CI 无法访问,属本地增强。

---

## 免责声明

仅供研究参考,非投资建议。概率来自预测市场盘口,价格来自公开行情源,可能存在延迟或误差。
