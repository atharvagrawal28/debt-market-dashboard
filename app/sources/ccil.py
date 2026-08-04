"""CCIL (ccilindia.com) public data pages.

Two pages carry their tables server-rendered in the HTML, so a plain GET is
enough -- no portlet/AJAX handshake:

  money-market-rates-and-volumes-most-liquid-tenor-
      Call / TREP / Basket Repo / Special Repo -- open, high, low, last
      trade, weighted average, volume. Covers the whole Money Market block.

  tenorwise-indicative-yields
      T-Bill (91D/182D/364D), G-Sec benchmark buckets (1Y-2Y ... 28Y-30Y)
      and SDL (5Y/10Y/15Y) indicative yields with the underlying security.

Both publish the last two business days, so a run picks up yesterday's close
even if it fires before today's numbers land.

Note: CCIL's site terms restrict commercial redistribution of this data. This
reads the public pages for desk use; check with CCIL before republishing.
"""
import re

import requests

from ..config import HTTP_TIMEOUT, USER_AGENT

BASE = "https://www.ccilindia.com/"

MONEY_MARKET_PAGE = "money-market-rates-and-volumes-most-liquid-tenor-"
TENOR_YIELD_PAGE = "tenorwise-indicative-yields"
SETTLEMENT_PAGE = "outright-and-repo-settlement"

_TAG = re.compile(r"<[^>]+>")
_TABLE = re.compile(r"<table.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)


class LayoutError(RuntimeError):
    """The page loaded but the table we scrape is not in it any more.

    Raised so a CCIL redesign surfaces as a failure instead of quietly
    looking like 'no data published today' -- the two are indistinguishable
    from the outside, and confusing them is how a scraper rots unnoticed.
    """


def _get(slug):
    r = requests.get(BASE + slug, headers={"User-Agent": USER_AGENT},
                     timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text


def _text(cell):
    return re.sub(r"\s+", " ", _TAG.sub("", cell)).replace("&nbsp;", " ").strip()


def _tables(html):
    """Yield every table as (header_list, [row_list, ...])."""
    for m in _TABLE.finditer(html):
        rows = _ROW.findall(m.group(0))
        if len(rows) < 2:
            continue
        parsed = [[_text(c) for c in _CELL.findall(r)] for r in rows]
        parsed = [p for p in parsed if any(p)]
        if len(parsed) < 2:
            continue
        yield parsed[0], parsed[1:]


def _num(value):
    if value is None:
        return None
    v = str(value).replace(",", "").strip()
    if not v or v == "-":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _iso(value):
    """CCIL prints '2026-07-31 00:00:00.0'."""
    if not value:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value.strip())
    return m.group(1) if m else None


def money_market():
    """Call / TREP / Basket Repo / Special Repo by date.

    Returns {date: {'CALL': {...}, 'TREP': {...}, ...}}
    """
    html = _get(MONEY_MARKET_PAGE)
    out = {}
    found = False
    for header, rows in _tables(html):
        idx = {h.lower(): i for i, h in enumerate(header)}
        if "type" not in idx or "wtd avg" not in idx:
            continue
        found = True
        for row in rows:
            if len(row) < len(header):
                continue
            date = _iso(row[idx["date"]])
            if not date:
                continue
            key = row[idx["type"]].strip().upper().replace(" ", "_")
            out.setdefault(date, {})[key] = {
                "open": _num(row[idx.get("open", -1)]),
                "high": _num(row[idx.get("high", -1)]),
                "low": _num(row[idx.get("low", -1)]),
                "last_trade": _num(row[idx.get("last trade", -1)]),
                "weighted_avg": _num(row[idx.get("wtd avg", -1)]),
                "volume_cr": _num(row[idx.get("volume('cr.)", -1)]),
            }
    if not found:
        raise LayoutError(
            f"no 'Type'/'Wtd Avg' table on /{MONEY_MARKET_PAGE} - page layout changed")
    return out


# CCIL tenor buckets -> the labels the closing report uses.
GSEC_BUCKETS = {
    "1Y-2Y": "1-2 Years", "2Y-3Y": "2-3 Years", "3Y-4Y": "3-4 Years",
    "4Y-5Y": "5 Years", "5Y-6Y": "5-6 Years", "6Y-7Y": "6-7 Years",
    "7Y-8Y": "7-8 Years", "8Y-9Y": "8-9 Years", "9Y-10Y": "10 Years",
    "10Y-12Y": "10-12 Years", "13Y-15Y": "15 Years", "15Y-20Y": "20 Years",
    "20Y-25Y": "25 Years", "28Y-30Y": "30 Years", "30Y-40Y": "40 Years",
}
TBILL_BUCKETS = {"91D": "91 Days", "182D": "182 Days", "364D": "364 Days"}
SDL_BUCKETS = {"5Y": "5 Year", "10Y": "10 Year", "15Y": "15 Year"}


def tenorwise_yields():
    """Indicative yields split into tbill / gsec / sdl by date.

    Returns {date: {'tbill': [...], 'gsec': [...], 'sdl': [...]}} where each
    entry is {'tenor_label', 'bucket', 'security', 'ytm'}.
    """
    html = _get(TENOR_YIELD_PAGE)
    out = {}
    found = False
    for header, rows in _tables(html):
        idx = {h.lower(): i for i, h in enumerate(header)}
        if "tenor bucket" not in idx or "ytm (%)" not in idx:
            continue
        found = True
        for row in rows:
            if len(row) < len(header):
                continue
            date = _iso(row[idx["date"]])
            bucket = row[idx["tenor bucket"]].strip()
            security = row[idx["security"]].strip()
            ytm = _num(row[idx["ytm (%)"]])
            if not date or ytm is None:
                continue
            day = out.setdefault(date, {"tbill": [], "gsec": [], "sdl": []})
            if bucket in TBILL_BUCKETS:
                kind, label = "tbill", TBILL_BUCKETS[bucket]
            elif "SGS" in security.upper() or bucket in SDL_BUCKETS:
                kind, label = "sdl", SDL_BUCKETS.get(bucket, bucket)
            else:
                kind, label = "gsec", GSEC_BUCKETS.get(bucket, bucket)
            day[kind].append({
                "tenor_label": label,
                "bucket": bucket,
                "security": security,
                "ytm": ytm,
            })
    if not found:
        raise LayoutError(
            f"no 'Tenor Bucket'/'YTM (%)' table on /{TENOR_YIELD_PAGE} - page layout changed")
    return out


def settlement_volumes():
    """Daily G-Sec / T-Bill / SDL trade counts and volumes."""
    html = _get(SETTLEMENT_PAGE)
    out = {}
    for header, rows in _tables(html):
        idx = {h.lower(): i for i, h in enumerate(header)}
        if "g-sec trades" not in idx:
            continue
        for row in rows:
            if len(row) < len(header):
                continue
            date = _iso(row[idx["date"]])
            if not date:
                continue
            out[date] = {re.sub(r"[^a-z0-9]+", "_", h.lower()).strip("_"): _num(row[i])
                         for h, i in idx.items() if h.lower() != "date"}
    return out


def fetch_all():
    """Everything CCIL publishes here, with per-source error isolation."""
    data, notes = {}, {}
    for name, fn in (("money_market", money_market),
                     ("tenorwise_yields", tenorwise_yields),
                     ("settlement", settlement_volumes)):
        try:
            data[name] = fn()
            notes[name] = f"{len(data[name])} date(s)"
        except Exception as e:                                  # noqa: BLE001
            data[name] = {}
            notes[name] = f"failed: {e.__class__.__name__}: {e}"
    return data, notes
