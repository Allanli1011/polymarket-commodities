"""共享工具:HTTP 抓取(含重试/退避)、配置加载、盘口标题解析、标的物解析。

所有抓取脚本都从这里取公共能力,保证 User-Agent、退避策略、路径常量一致。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests
import yaml

# ── 路径常量 ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "markets.yml")
RAW_DIR = os.path.join(ROOT, "data", "raw")
SITE_DIR = os.path.join(ROOT, "site")
DATA_JSON = os.path.join(SITE_DIR, "data.json")
RAW_POLYMARKET = os.path.join(RAW_DIR, "polymarket.json")
RAW_PRICES = os.path.join(RAW_DIR, "prices.json")

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
# Yahoo 必须带 User-Agent,否则返回 429;query1 / query2 互为备用主机。
YAHOO_HOSTS = [
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
]
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

UA = "Mozilla/5.0 (compatible; polymarket-commodities/1.0; +https://github.com)"
HEADERS = {"User-Agent": UA, "Accept": "application/json"}


# ── 通用 HTTP ───────────────────────────────────────────────────────────────
def get_json(url: str, *, params: dict | None = None, tries: int = 4,
             timeout: int = 30) -> Any:
    """GET 并解析 JSON,带指数退避。失败抛出最后一次异常。"""
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 429:
                raise requests.HTTPError("429 rate limited")
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — 抓取层统一退避重试
            last = e
            time.sleep(min(2 ** i, 8) + 0.25 * i)
    raise RuntimeError(f"GET 失败 {url}: {last}")


def get_text(url: str, *, tries: int = 3, timeout: int = 30) -> str | None:
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception:  # noqa: BLE001
            time.sleep(min(2 ** i, 6))
    return None


# ── 配置 / IO ───────────────────────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dirs() -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(SITE_DIR, exist_ok=True)


def read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ── 盘口标题解析 ─────────────────────────────────────────────────────────────
_CODE_RE = re.compile(r"\(([A-Z]{2,6})\)")
_DIR_RE = re.compile(r"\((HIGH|LOW)\)", re.IGNORECASE)
_NUM_RE = re.compile(r"\$\s*([0-9][0-9,]*\.?[0-9]*)")


def parse_title(question: str) -> dict:
    """从盘口标题解析 {code, direction, threshold}。任一项可能为 None。

    标题高度结构化,例如:
      "Will Gold (GC) hit (HIGH) $6,000 by end of December?"
      "Will WTI Crude Oil (WTI) hit (LOW) $65 in June?"
      "Will Crude Oil reach a new all-time high by September 30?"  (无代码/阈值)
    """
    code = None
    m = _CODE_RE.search(question)
    if m and m.group(1) not in ("HIGH", "LOW"):
        code = m.group(1)

    direction = None
    md = _DIR_RE.search(question)
    if md:
        direction = md.group(1).upper()
    elif "<" in question or "below" in question.lower():
        direction = "LOW"
    elif ">" in question or "above" in question.lower():
        direction = "HIGH"

    threshold = None
    mt = _NUM_RE.search(question)
    if mt:
        try:
            threshold = float(mt.group(1).replace(",", ""))
        except ValueError:
            threshold = None

    return {"code": code, "direction": direction, "threshold": threshold}


def resolve_underlying(question: str, parsed: dict, cfg: dict) -> dict | None:
    """把盘口映射到标的物 {symbol,label,unit,code}。映射不到返回 None(等于过滤掉)。"""
    symbol_map: dict = cfg["symbol_map"]
    keyword_map: dict = cfg.get("keyword_map", {})

    code = parsed.get("code")
    if code and code in symbol_map:
        return {**symbol_map[code], "code": code}

    ql = question.lower()
    # 关键词回退(长关键词优先,避免 "gold" 命中 "goldman" 之类)
    for kw in sorted(keyword_map, key=len, reverse=True):
        if kw in ql:
            c = keyword_map[kw]
            if c in symbol_map:
                return {**symbol_map[c], "code": c}
    return None


def parse_json_field(value: Any) -> Any:
    """Gamma 的 outcomes/outcomePrices/clobTokenIds 是字符串化 JSON,这里统一解码。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return None
    return value
