"""Everything computed from the raw feeds rather than typed in.

The closing report used to ask the analyst to type CD/CP tenor ranges by hand
while the trade file that determines them sat in the Downloads folder. These
functions close that loop: bucket the actual trades by residual maturity and
read the range straight off them.
"""
import datetime as dt
import re

# Residual-maturity buckets, in days, matched to how the desk quotes money
# market paper. Contiguous so every trade lands somewhere.
TENOR_BUCKETS = [
    (0, 45, "1 Month"),
    (46, 75, "2 Months"),
    (76, 135, "3 Months"),
    (136, 225, "6 Months"),
    (226, 300, "9 Months"),
    (301, 400, "12 Months"),
    (401, 100000, "Above 1 Year"),
]

# Order used when rendering, so tables read short -> long.
BUCKET_ORDER = {label: i for i, (_, _, label) in enumerate(TENOR_BUCKETS)}

_SUFFIXES = [
    r"\s+\d+D\s+(?:CP|CD)\s+\d{1,2}[A-Z]{3}\d{2,4}\s*$",
    r"\s+(?:CP|CD)\s+\d{1,2}[A-Z]{3}\d{2,4}\s*$",
    r"\s+\d+D\s+(?:CP|CD)\s*$",
    r"\s+(?:CP|CD)\s*$",
]
_LEGAL = [
    r"\s+PRIVATE\s+LIMITED$", r"\s+PVT\.?\s+LTD\.?$", r"\s+PVT\.?\s+LIMITED$",
    r"\s+LIMITED$", r"\s+LTD\.?$", r"\s+CORPORATION$",
]


def normalize_issuer(description):
    """'L AND T FINANCE LIMITED 91D CP 28OCT26' -> 'L AND T FINANCE'.

    Same intent as the old extractCD/extractCP pair, but one function driven
    by the instrument tag inside the description rather than by which upload
    box the file came from.
    """
    if not description:
        return ""
    s = str(description).strip().upper()
    for pattern in _SUFFIXES:
        s = re.sub(pattern, "", s, flags=re.I)
    s = re.sub(r"^THE\s+", "", s)
    for pattern in _LEGAL:
        s = re.sub(pattern, "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def residual_days(deal_date, maturity_date):
    if not deal_date or not maturity_date:
        return None
    try:
        d0 = dt.date.fromisoformat(deal_date)
        d1 = dt.date.fromisoformat(maturity_date)
    except (TypeError, ValueError):
        return None
    return (d1 - d0).days


def bucket_for(days):
    if days is None or days < 0:
        return None
    for lo, hi, label in TENOR_BUCKETS:
        if lo <= days <= hi:
            return label
    return None


def enrich(records):
    """Add issuer + residual_days to normalized F-TRAC records, in place."""
    for r in records:
        r["issuer"] = normalize_issuer(r.get("description"))
        r["residual_days"] = residual_days(r.get("deal_date"), r.get("maturity_date"))
    return records


def _wavg(pairs):
    """Volume-weighted average of (value, weight); falls back to simple mean."""
    pairs = [(v, w) for v, w in pairs if v is not None]
    if not pairs:
        return None
    total_w = sum(w for _, w in pairs if w)
    if not total_w:
        return round(sum(v for v, _ in pairs) / len(pairs), 4)
    return round(sum(v * w for v, w in pairs if w) / total_w, 4)


def aggregate(trades):
    """Collapse trades that share ISIN + maturity + rate, as the CD/CP dashboard does.

    Keyed on ISIN rather than the cleaned-up name: two issuers can normalize to
    the same display string, and one issuer can have several papers outstanding.
    """
    grouped = {}
    for t in trades:
        rate = t.get("yield_pct")
        if rate is None:
            continue
        key = (t.get("isin"), t.get("maturity_date"), round(rate, 2))
        row = grouped.get(key)
        if not row:
            row = grouped[key] = {
                "isin": t.get("isin"),
                "issuer": t.get("issuer") or normalize_issuer(t.get("description")),
                "description": t.get("description"),
                "instrument": t.get("instrument"),
                "deal_date": t.get("deal_date"),
                "maturity_date": t.get("maturity_date"),
                "residual_days": t.get("residual_days"),
                "yield_pct": round(rate, 2),
                "amount_cr": 0.0,
                "trades": 0,
            }
        row["amount_cr"] += t.get("amount_cr") or 0.0
        row["trades"] += t.get("trade_count") or 1
        # Keep the last print of the day as the headline deal time.
        if (t.get("deal_time") or "") > (row.get("deal_time") or ""):
            row["deal_time"] = t.get("deal_time")
    rows = list(grouped.values())
    for r in rows:
        r["amount_cr"] = round(r["amount_cr"], 2)
    rows.sort(key=lambda r: (r["maturity_date"] or "", r["issuer"], r["yield_pct"]))
    return rows


def tenor_summary(trades):
    """Yield range per residual-maturity bucket -- the auto-filled CD/CP table.

    Returns rows of {tenor_label, low, high, weighted_avg, volume_cr,
    trade_count, issuer_count}.
    """
    buckets = {}
    for t in trades:
        rate = t.get("yield_pct")
        days = t.get("residual_days")
        if days is None:
            days = residual_days(t.get("deal_date"), t.get("maturity_date"))
        label = bucket_for(days)
        if rate is None or label is None:
            continue
        b = buckets.setdefault(label, {
            "tenor_label": label, "rates": [], "volume_cr": 0.0,
            "trade_count": 0, "issuers": set(),
        })
        volume = t.get("amount_cr") or 0.0
        b["rates"].append((rate, volume))
        b["volume_cr"] += volume
        b["trade_count"] += t.get("trade_count") or 1
        if t.get("issuer"):
            b["issuers"].add(t["issuer"])

    rows = []
    for b in buckets.values():
        rates = [r for r, _ in b["rates"]]
        rows.append({
            "tenor_label": b["tenor_label"],
            "low": round(min(rates), 2),
            "high": round(max(rates), 2),
            "weighted_avg": _wavg(b["rates"]),
            "volume_cr": round(b["volume_cr"], 2),
            "trade_count": b["trade_count"],
            "issuer_count": len(b["issuers"]),
        })
    rows.sort(key=lambda r: BUCKET_ORDER.get(r["tenor_label"], 99))
    return rows


def issuer_summary(trades, limit=15):
    """Most active issuers by traded volume."""
    by_issuer = {}
    for t in trades:
        name = t.get("issuer") or normalize_issuer(t.get("description"))
        if not name:
            continue
        row = by_issuer.setdefault(name, {
            "issuer": name, "volume_cr": 0.0, "trade_count": 0, "rates": [],
        })
        volume = t.get("amount_cr") or 0.0
        row["volume_cr"] += volume
        row["trade_count"] += t.get("trade_count") or 1
        if t.get("yield_pct") is not None:
            row["rates"].append((t["yield_pct"], volume))
    rows = []
    for r in by_issuer.values():
        rates = [x for x, _ in r["rates"]]
        rows.append({
            "issuer": r["issuer"],
            "volume_cr": round(r["volume_cr"], 2),
            "trade_count": r["trade_count"],
            "weighted_avg": _wavg(r["rates"]),
            "low": round(min(rates), 2) if rates else None,
            "high": round(max(rates), 2) if rates else None,
        })
    rows.sort(key=lambda r: -r["volume_cr"])
    return rows[:limit]


def top_traded(trades, limit=10):
    """Highest-volume papers of the day."""
    rows = aggregate(trades)
    by_isin = {}
    for r in rows:
        cur = by_isin.setdefault(r["isin"], {
            "isin": r["isin"], "issuer": r["issuer"],
            "description": r["description"],
            "maturity_date": r["maturity_date"],
            "volume_cr": 0.0, "rates": [],
        })
        cur["volume_cr"] += r["amount_cr"]
        cur["rates"].append((r["yield_pct"], r["amount_cr"]))
    out = []
    for r in by_isin.values():
        out.append({
            "isin": r["isin"], "issuer": r["issuer"],
            "description": r["description"],
            "maturity_date": r["maturity_date"],
            "volume_cr": round(r["volume_cr"], 2),
            "weighted_avg": _wavg(r["rates"]),
        })
    out.sort(key=lambda r: -r["volume_cr"])
    return out[:limit]


def attach_previous(rows, prev_rows, key, value_field, prev_field):
    """Fill each row's `prev_field` from the matching previous-day row.

    This is what removes the second column of hand-typed numbers: every
    `*_prev` in the report is a lookup against the day before, not an input.
    """
    lookup = {r.get(key): r.get(value_field) for r in prev_rows or []}
    for r in rows:
        prior = lookup.get(r.get(key))
        r[prev_field] = prior
        if prior is not None and r.get(value_field) is not None:
            r["change_bps"] = round((r[value_field] - prior) * 100, 1)
        else:
            r["change_bps"] = None
    return rows


def spreads(cd_tenors, cp_tenors, tbill_points):
    """CD and CP spread over the comparable T-Bill, in basis points."""
    tbill = {}
    for p in tbill_points or []:
        label = p.get("tenor_label") or ""
        # Raw curve rows carry `ytm`; once assembled into the report they are
        # renamed `rate_today`. Accept either so this works on both.
        rate = p.get("ytm") if p.get("ytm") is not None else p.get("rate_today")
        if rate is None:
            continue
        if "91" in label:
            tbill["3 Months"] = rate
        elif "182" in label:
            tbill["6 Months"] = rate
        elif "364" in label:
            tbill["12 Months"] = rate

    cd = {r["tenor_label"]: r.get("weighted_avg") for r in cd_tenors or []}
    cp = {r["tenor_label"]: r.get("weighted_avg") for r in cp_tenors or []}

    rows = []
    for label in ("3 Months", "6 Months", "12 Months"):
        base = tbill.get(label)
        if base is None:
            continue
        rows.append({
            "tenor_label": label,
            "tbill_ytm": round(base, 4),
            "cd_wavg": cd.get(label),
            "cp_wavg": cp.get(label),
            "cd_spread_bps": round((cd[label] - base) * 100, 1)
            if cd.get(label) is not None else None,
            "cp_spread_bps": round((cp[label] - base) * 100, 1)
            if cp.get(label) is not None else None,
            "cd_cp_spread_bps": round((cp[label] - cd[label]) * 100, 1)
            if cd.get(label) is not None and cp.get(label) is not None else None,
        })
    return rows
