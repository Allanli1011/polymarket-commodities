# 链上预测市场 × 大宗商品盘口追踪 — 项目设计

> 目标:把 **Polymarket(链上预测市场)** 中与大宗商品相关的盘口隐含概率,与**实际标的物价格走势**叠加在同一张时间轴上对比追踪;通过 **GitHub Actions 定时刷新数据**;最终以**纯静态 HTML** 页面(GitHub Pages)呈现。

本设计中的所有外部接口、版本号、限频均经过 2026-06 实际查证(见文末「数据源核实结论」)。

---

## 1. 核心思路(一句话)

对每一个被追踪的 Polymarket 商品盘口(例如「黄金 6 月底前是否站上 $2000」),抓取它的**隐含概率时间序列**(0–100%),同时抓取对应标的物(黄金近月期货 `GC=F`)的**真实价格时间序列**,在一张**双 Y 轴**图里叠加,并把盘口里的**阈值(如 $2000)画成价格轴上的参考线**。这样一眼就能看出「价格离阈值的距离」与「市场赌它发生的概率」是否同步——这正是这个项目的信息价值所在。

---

## 2. 系统架构

```
┌──────────────────────── GitHub Actions (定时 cron) ────────────────────────┐
│                                                                              │
│  1) fetch_polymarket.py   ──>  Gamma API  (盘口元数据 + 当前概率)            │
│                                CLOB API   (/prices-history 概率历史)         │
│                                                                              │
│  2) fetch_prices.py       ──>  Yahoo Finance /v8/chart  (标的物价格, 主源)   │
│                                FRED CSV                  (回退/校验源)        │
│                                                                              │
│  3) build_data.py         ──>  合并 + 按日对齐  ──>  site/data.json          │
│                                                                              │
│  4) git commit data 历史 (可选, 留档)  +  upload-pages-artifact             │
│                                                                              │
│  5) deploy-pages          ──>  GitHub Pages (静态发布)                       │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        site/index.html  +  Chart.js v4 (CDN)  +  fetch('./data.json')
        ── 浏览器端纯静态渲染:双轴叠加图 / 阈值参考线 / 多盘口小图矩阵 ──
```

设计原则:**所有抓取都在 CI 服务端完成并烘焙成 `data.json`**;页面本身 100% 静态,只 `fetch` 同源的 `data.json`,因此**没有 CORS、没有运行时 API key、没有限频问题**,页面在任何静态托管上都能跑。

---

## 3. 数据源(已核实)

### 3.1 Polymarket(链上预测市场)— 全部只读、免 key、可服务端调用

| 用途 | 接口 | 说明 |
|---|---|---|
| 发现/列出商品盘口 | `GET https://gamma-api.polymarket.com/markets?tag_id=<commodities的数字id>&active=true&closed=false&limit=100&order=volume24hr&ascending=false` | 也可用 `/events`。先用 `GET /tags?label=commodities` 拿到数字 `tag_id`。 |
| 盘口元数据 + 当前概率 | 同上返回字段 | `question, slug, conditionId, outcomes, outcomePrices, clobTokenIds, volume, volume24hr, liquidity, endDate, lastTradePrice, bestBid/bestAsk` |
| 概率历史(时间序列) | `GET https://clob.polymarket.com/prices-history?market=<clobTokenId>&interval=max&fidelity=60` | 返回 `{ history: [{t: <epoch秒>, p: "0.62"}] }`。`interval`∈`max/1w/1d/6h/1h/1m`;或用 `startTs/endTs`+`fidelity`(分钟)。 |
| 当前价(快照) | 直接读 Gamma 的 `outcomePrices` 即可;或 `clob.polymarket.com/midpoint?token_id=<id>` | 日级快照用 Gamma 一次取回最省事。 |

**关键坑(务必处理):**
- `outcomes / outcomePrices / clobTokenIds` 都是**字符串化的 JSON**,要 `json.loads()` 再用。
- `clobTokenIds` 与 `outcomes` 按下标 1:1 对应——**按标签取**(`token_ids[outcomes.index("Yes")]`),别按位置硬编码。
- 概率 `p` 是 0–1 小数,展示时 ×100 存成 0–100。
- 限频很宽松(`/markets` 300 req/10s,CLOB 价格类 500–1500 req/10s),我们每天只打十几个请求,完全无压力;仍建议指数退避兜底。

> 可选实时:`wss://ws-subscriptions-clob.polymarket.com/ws/market` 公共频道(免 key)。本项目日级刷新用不到,留作未来「准实时」升级路径。

### 3.2 标的物真实价格 — 主源 Yahoo,回退 FRED

> ⚠️ **Stooq 已不可用**:2026 年起 Stooq 的 CSV 下载端点加了 JS 工作量证明反爬,无浏览器的 CI 里只会拿到 HTML 挑战页,不能再用。

**主源:Yahoo Finance 非官方 chart 端点(免 key、JSON、日级+日内)**
```
GET https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?interval=1d&range=6mo
Header: User-Agent: Mozilla/5.0        # 必须带,否则返回 429 "Too Many Requests"
```
返回 `chart.result[0]`:`meta.regularMarketPrice`、`meta.currency`、`timestamp[]`、`indicators.quote[0].{open,high,low,close}`。

标的物 → Yahoo 符号(2026-06 实测有数据):

| 标的 | 期货符号 | 代理 ETF | 单位坑 |
|---|---|---|---|
| 黄金 | `GC=F` | `GLD` | USD/oz |
| 白银 | `SI=F` | `SLV` | USD/oz |
| WTI 原油 | `CL=F` | `USO` | USD/bbl |
| Brent 原油 | `BZ=F` | `BNO` | USD/bbl |
| 天然气 | `NG=F` | `UNG` | USD/MMBtu |
| 铜 | `HG=F` | `CPER` | **USD/lb** |
| 小麦 | `ZW=F` | `WEAT` | **美分/蒲式耳 (currency=USX)** |
| 玉米 | `ZC=F` | — | **美分/蒲式耳 (currency=USX)** |

**单位坑**:谷物是美分(`meta.currency == "USX"`,需 ÷100 或在显示层标注),铜是 USD/lb。**永远读 `meta.currency` 再决定换算**,别假设美元。

**回退源:FRED 无 key CSV**(主源 429/异常时启用,也用于交叉校验):
```
GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>   # CSV: DATE,VALUE
```
可用序列:`DCOILWTICO`(WTI)、`DCOILBRENTEU`(Brent)、`DHHNGSP`(亨利港天然气)等。注意 FRED 日级序列**滞后约 1–2 周、无周末/节假日行**,只适合回填/校验,不适合当天数据;能力强可改用注册 free key 的 FRED JSON API(无反爬)。

**鲁棒性策略**:Yahoo 主 + `query1↔query2` 双主机切换 + 指数退避 + **把最近一次成功值缓存进 `data.json`**,任何一次抓取失败都不至于让页面空白。

### 3.3(可选)LSEG / Refinitiv 本地增强源

本机环境挂了 LSEG MCP(`commodity_futures` 提供 `settlement_history` 前月连续结算价历史、`curve` 期限结构)。**数据质量高于 Yahoo**,但**只在本地可用,GitHub Actions CI 里访问不到**。

定位:作为**可选的本地增强管线**——你在本机跑一个脚本用 LSEG 拉高质量结算价 / 期限结构,生成的 JSON 提交进仓库,CI 直接复用。不强依赖,先用 Yahoo 把闭环跑通。

---

## 4. 仓库结构

```
polymarket-commodities/
├── README.md
├── DESIGN.md                  # 本文件
├── requirements.txt           # requests, PyYAML
├── config/
│   └── markets.yml            # ★ 人工维护:被追踪盘口 → 标的物/阈值的映射
├── scripts/
│   ├── discover.py            # 辅助:列出当前 commodities 标签下的活跃盘口(帮你填 markets.yml)
│   ├── fetch_polymarket.py    # Gamma 元数据/当前概率 + CLOB 概率历史
│   ├── fetch_prices.py        # Yahoo 主 + FRED 回退,含单位换算
│   ├── build_data.py          # 合并 + 按日对齐 + 写 site/data.json
│   └── fetch_lseg_local.py    # (可选)本机用 LSEG MCP 拉增强数据
├── site/                      # ★ 发布到 Pages 的静态站点
│   ├── index.html
│   ├── app.js                 # Chart.js 渲染逻辑
│   ├── style.css
│   └── data.json              # 由 CI 生成(也提交留档)
├── data/history/              # (可选)每日快照归档,留时间序列全历史
└── .github/workflows/
    └── refresh.yml            # 定时抓取 + 构建 + 部署
```

依赖极简:Python 端只需 `requests` + `PyYAML`;前端纯 CDN,无构建步骤。

---

## 5. 配置驱动:`config/markets.yml`

把「哪个 Polymarket 盘口 ↔ 哪个标的物 ↔ 阈值」做成**人工维护的配置**(因为这种映射需要人判断,自动猜不准)。`discover.py` 帮你列出候选盘口来填它。

```yaml
defaults:
  polymarket_history_interval: "max"   # CLOB prices-history 的 interval
  price_range: "6mo"                   # Yahoo 拉取区间
  price_interval: "1d"

markets:
  - key: gold-above-2000-june
    polymarket:
      slug: will-gold-close-above-2000-june   # 或直接写 condition_id
      track_outcome: "Yes"                     # 追踪哪个结果的概率
    underlying:
      source: yahoo          # yahoo | fred
      symbol: "GC=F"
      label: "黄金 COMEX 近月期货"
      unit: "USD/oz"
    threshold: 2000          # 可选:在价格轴画一条参考线
    display:
      title: "黄金 6 月底前站上 $2000?"
      color_prob: "#22d3ee"
      color_price: "#f59e0b"
```

---

## 6. 数据契约:`site/data.json`

页面只认这个文件。Schema:

```json
{
  "generated_at": "2026-06-30T02:00:00Z",
  "markets": [
    {
      "key": "gold-above-2000-june",
      "title": "黄金 6 月底前站上 $2000?",
      "polymarket_url": "https://polymarket.com/event/...",
      "underlying": { "symbol": "GC=F", "label": "黄金 COMEX 近月期货", "unit": "USD/oz" },
      "threshold": 2000,
      "end_date": "2026-06-30T23:59:59Z",
      "track_outcome": "Yes",
      "prob":  [{ "t": "2026-06-01", "v": 55.0 }, { "t": "2026-06-02", "v": 57.2 }],
      "price": [{ "t": "2026-06-01", "v": 1985.3 }, { "t": "2026-06-02", "v": 1992.1 }],
      "aligned": [{ "t": "2026-06-01", "prob": 55.0, "price": 1985.3 }],
      "latest": { "prob": 62.0, "price": 2012.4, "volume": 2450000, "asof": "2026-06-30" },
      "stale": false
    }
  ]
}
```

- `prob` / `price`:各自原始序列(可不同频率,Chart.js 时间轴按真实时间各自落点)。
- `aligned`:按日对齐表(慢的价格序列前向填充),给 tooltip「同一天同时显示概率和价格」用。
- `stale`:本轮抓取失败、用了缓存旧值时置 `true`,页面打标提醒。

---

## 7. 可视化(前端,Chart.js v4)

选型结论:**Chart.js v4**(双轴 `yAxisID`、时间轴、组合 tooltip、CDN 体积小 ~70KB gzip、纯静态零构建)。ECharts 为美观备选,体积更大。

页面布局:
- **总览页**:响应式 CSS 网格的**小图矩阵**(每个盘口一张卡片:迷你叠加图 + 当前概率 + 当前价 + 距阈值距离 + 成交量)。
- **详情**:点卡片展开大图(完整双轴叠加 + 阈值参考线 + 时间范围切换 30D/90D/全部 + 暗色)。

每张图的核心配置(配置键,非完整代码):
```js
new Chart(ctx, {
  type: 'line',
  data: { datasets: [
    { label: '隐含概率', yAxisID: 'prob',  data: m.prob.map(p => ({x:p.t, y:p.v})),  borderColor: '#22d3ee' },
    { label: m.underlying.label, yAxisID: 'price', data: m.price.map(p => ({x:p.t, y:p.v})), borderColor: '#f59e0b' },
  ]},
  options: {
    parsing: false,
    interaction: { mode: 'index', intersect: false },
    scales: {
      x:     { type: 'time', time: { unit: 'day' } },               // 需 chartjs-adapter-luxon
      prob:  { position: 'left',  min: 0, max: 100, ticks: { callback: v => v + '%' } },
      price: { position: 'right', grid: { drawOnChartArea: false }, ticks: { callback: v => '$' + v } },
    },
    plugins: {
      tooltip: { callbacks: { label: c => c.dataset.yAxisID === 'prob' ? c.parsed.y + '%' : '$' + c.parsed.y } },
      annotation: { /* 阈值参考线:price 轴上 y=threshold 画虚线 */ },
    },
  },
});
```

CDN(jsDelivr,顺序很重要):`chart.js@4` → `luxon@3` → `chartjs-adapter-luxon@1` →(可选)`chartjs-plugin-annotation@3`。

前端坑:① 时间轴**必须**带日期适配器(luxon/date-fns),否则静默不渲染;② 右轴设 `grid.drawOnChartArea:false` 避免双网格线;③ 暗色模式要手动设 `Chart.defaults.color` 和网格色;④ 两序列时间栅格不同,`mode:'index'` 按下标对齐可能错配——用 `aligned` 表做 tooltip 或自定义最近时间匹配。

---

## 8. 自动化:`.github/workflows/refresh.yml`

已核实当前版本:`setup-python@v6`、`configure-pages@v5`、`upload-pages-artifact@v5`、`deploy-pages@v4`、`checkout@v4`。

```yaml
name: Refresh & Deploy

on:
  schedule:
    - cron: '0 2 * * *'        # 每天 UTC 02:00(最小间隔 5 分钟;免费仓库 60 天无活动会自动停)
  workflow_dispatch:           # 支持手动触发

permissions:
  contents: write              # 提交 data.json 留档
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v6
        with: { python-version: '3.12', cache: 'pip' }
      - run: pip install -r requirements.txt

      - name: Fetch + build data.json
        run: |
          python scripts/fetch_polymarket.py
          python scripts/fetch_prices.py
          python scripts/build_data.py
        # 若改用 FRED JSON API 等需要 key 的源,在此注入:
        # env: { FRED_API_KEY: ${{ secrets.FRED_API_KEY }} }

      - name: Commit data history (留档, 防触发循环)
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add site/data.json data/history || true
          git commit -m "chore: refresh data [skip ci]" || echo "no changes"
          git push || echo "nothing to push"

      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v5
        with: { path: './site' }

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - id: deploy
        uses: actions/deploy-pages@v4
```

要点:
- 仓库 **Settings → Pages → Source 选「GitHub Actions」**(不是 gh-pages 分支)。
- **一次抓取**:在 `build` 里抓取→构建→提交→上传产物,避免重复抓取。
- **防循环**:提交信息带 `[skip ci]`;也可在 push 触发上加 `paths-ignore: [site/data.json, data/**]`(本工作流无 push 触发,主要靠 `[skip ci]`)。
- **留档 vs 仅产物**:时间序列数据**建议提交进仓库**(免费拿到全历史、可回填、diff 可审计);若担心仓库膨胀可改为只放 Pages 产物。
- **可选拆分**:商品价格(日更)和概率(可更频)可拆成两个 cron,或加一个仅 `workflow_dispatch` 的回填工作流。

---

## 9. 抓取脚本逻辑要点(伪代码)

```
fetch_polymarket.py
  tag_id = GET /tags?label=commodities
  for m in config.markets:
     meta = GET /markets?slug=m.slug            # 或按 condition_id
     outcomes, prices, token_ids = json.loads(各字段)
     tok = token_ids[outcomes.index(m.track_outcome)]
     hist = GET /prices-history?market=tok&interval=max&fidelity=60
     写出 prob 序列(×100)+ latest 概率/成交量/endDate

fetch_prices.py
  for m in config.markets:
     try Yahoo /v8/chart(带 UA, query1→query2 退避)
         读 meta.currency 做单位换算(USX/100, USD/lb 等)
     except → 回退 FRED CSV;再不行 → 用上次缓存值, stale=True
     写出 price 序列

build_data.py
  合并两套序列 → 按日对齐(price 前向填充)→ 生成 aligned/latest/stale
  写 site/data.json
```

---

## 10. 边界与注意事项汇总

- **盘口结算后**:`endDate` 过后 Polymarket 概率收敛到 0/1。页面对已结算盘口打「已结算」标并保留历史。
- **盘口与标的物对不齐**:Polymarket 商品盘口数量有限且问题措辞多样(「站上 $X」「年底前」等),需人工在 `markets.yml` 里挑选并绑定标的/阈值——这是项目质量的关键人工环节。
- **单位/币种**:谷物美分、铜 USD/lb——统一在 `fetch_prices.py` 落库时换算并在 `unit` 字段标注。
- **抓取失败容错**:任何源失败都回退到缓存值并标 `stale`,保证页面永不空白。
- **时区**:全部用 UTC 存储,展示层本地化。

---

## 11. 实施里程碑

1. **M1 闭环(单盘口)**:手填 1 个盘口(如黄金)→ 跑通 4 个脚本 → 本地生成 `data.json` → `index.html` 出图。
2. **M2 自动化**:接上 `refresh.yml` + GitHub Pages,验证定时刷新与留档。
3. **M3 多盘口总览**:小图矩阵 + 详情展开 + 阈值参考线 + 时间范围切换 + 暗色。
4. **M4 增强(可选)**:LSEG 本地高质量数据、概率/价格相关性指标、准实时 WebSocket、邮件/推送告警。

---

## 12. 数据源核实结论(2026-06)

- ✅ Polymarket Gamma/CLOB 只读端点**免 key、可服务端调用**;字段为字符串化 JSON 需解析;限频宽松。
- ✅ 标的物价格主源 **Yahoo `/v8/chart`(必带 User-Agent)**;回退 **FRED CSV**;**Stooq 已被反爬封死**,弃用。
- ✅ GitHub Pages 走**官方 artifact 法**(`upload-pages-artifact@v5` + `deploy-pages@v4`),`[skip ci]` 防循环,`concurrency` 防重叠。
- ✅ 前端 **Chart.js v4** 双轴叠加,纯静态 `fetch('./data.json')`。
- ◐ LSEG MCP 数据质量高但仅本地可用,列为可选增强。
