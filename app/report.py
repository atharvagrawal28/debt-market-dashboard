"""Assembles the closing report for a date.

Two layers:

  auto_payload()  everything the feeds can answer on their own
  full_report()   that, with the analyst's saved edits laid over the top

Fields no free source publishes (OIS, AAA PSU spreads, top-traded G-Secs,
news, commentary) simply come through empty and stay hand-entered.
"""
from . import db, derive

# What each block of the report is sourced from, surfaced in the UI so it is
# obvious which numbers were fetched and which still need a human.
PROVENANCE = {
    "call": "CCIL money market",
    "trep": "CCIL money market",
    "mm_volume": "CCIL money market",
    "benchmark_curve": "CCIL indicative yields",
    "tbill_range": "CCIL indicative yields",
    "sdl_range": "CCIL indicative yields",
    "cd_money_market": "F-TRAC CD secondary trades",
    "cp_money_market": "F-TRAC CP secondary trades",
    "fx": "FBIL via Frankfurter",
    "brent": "FRED (EIA)",
    "spreads": "derived",
    "top_traded_gsecs": "manual",
    "aaa_psu_corp": "manual",
    "ois_curve": "manual",
    "news": "manual",
    "ai_commentary": "manual",
}

EMPTY = {
    "call": {"label": "CALL", "ltr": None, "weighted_avg": None},
    "trep": {"label": "TREP", "ltr": None, "weighted_avg": None},
    "mm_volume": {"total_crores": None, "weighted_avg": None},
    "top_traded_gsecs": [],
    "benchmark_curve": [],
    "sdl_range": [],
    "aaa_psu_corp": [],
    "cd_money_market": [],
    "cp_money_market": [],
    "ois_curve": [],
    "tbill_range": [],
    "spreads": [],
    "fx": {"pair": "USD/INR", "close": None, "open": None,
           "day_low": None, "day_high": None, "source": None},
    "brent": {"price_usd": None, "as_of": "5:00 pm", "source": None},
    "news": [],
    "ai_commentary": "",
}


def _mm_block(conn, date):
    """CALL / TREP / volume, falling back to the last published day."""
    used = db.latest_mm_date(conn, date)
    if not used:
        return {}, None
    rates = db.mm_rates(conn, used)
    call = rates.get("CALL") or {}
    trep = rates.get("TREP") or {}
    total = sum((rates[k].get("volume_cr") or 0) for k in rates)
    weights = [(rates[k].get("weighted_avg"), rates[k].get("volume_cr") or 0)
               for k in rates if rates[k].get("weighted_avg") is not None]
    block = {
        "call": {"label": "CALL", "ltr": call.get("last_trade"),
                 "weighted_avg": call.get("weighted_avg"),
                 "open": call.get("open"), "high": call.get("high"),
                 "low": call.get("low"), "volume_cr": call.get("volume_cr")},
        "trep": {"label": "TREP", "ltr": trep.get("last_trade"),
                 "weighted_avg": trep.get("weighted_avg"),
                 "open": trep.get("open"), "high": trep.get("high"),
                 "low": trep.get("low"), "volume_cr": trep.get("volume_cr")},
        "mm_volume": {"total_crores": round(total, 2) if total else None,
                      "weighted_avg": derive._wavg(weights)},
        "mm_detail": [dict(v, kind=k) for k, v in sorted(rates.items())],
    }
    return block, used


def _curve_block(conn, date):
    """Benchmark G-Sec / T-Bill / SDL, with the prior day's print attached."""
    used = db.latest_curve_date(conn, date)
    if not used:
        return {}, None
    points = db.curve(conn, used)
    prev_date = None
    row = conn.execute("SELECT MAX(date) d FROM curve_points WHERE date<?",
                       (used,)).fetchone()
    if row and row["d"]:
        prev_date = row["d"]
    prev_points = db.curve(conn, prev_date) if prev_date else []

    def block(name, value_key, prev_key):
        rows = [{"tenor_label": p["tenor_label"], "security_name": p["security"],
                 "bucket": p["bucket"], value_key: p["ytm"]}
                for p in points if p["curve"] == name]
        priors = [{"tenor_label": p["tenor_label"], value_key: p["ytm"]}
                  for p in prev_points if p["curve"] == name]
        return derive.attach_previous(rows, priors, "tenor_label", value_key, prev_key)

    return {
        "benchmark_curve": block("gsec", "yield_today", "yield_prev"),
        "tbill_range": block("tbill", "rate_today", "rate_prev"),
        "sdl_range": block("sdl", "rate_today", "rate_prev"),
    }, used


def _paper_block(conn, date, instrument):
    """CD or CP tenor ranges derived from that day's actual trades.

    Falls back to the last date that has trades -- a report opened before the
    day's F-TRAC file is published should show the previous close rather than
    nothing -- and reports which date it used so the UI can flag it.
    """
    used = date
    trades = db.trades(conn, date, instrument, "SEC")
    if not trades:
        used = db.previous_trade_date(conn, date)
        if not used:
            return [], [], [], None
        trades = db.trades(conn, used, instrument, "SEC")
        if not trades:
            return [], [], [], None
    tenors = derive.tenor_summary(trades)
    prev_date = db.previous_trade_date(conn, used)
    if prev_date:
        prior = derive.tenor_summary(db.trades(conn, prev_date, instrument, "SEC"))
        derive.attach_previous(tenors, prior, "tenor_label", "weighted_avg", "prev_wavg")
    return tenors, derive.issuer_summary(trades), derive.top_traded(trades), used


def auto_payload(conn, date):
    """Everything that can be answered without an analyst typing."""
    out = dict(EMPTY)
    out["report_date"] = date
    sources, stale = [], {}

    mm, mm_date = _mm_block(conn, date)
    if mm:
        out.update(mm)
        sources.append("CCIL money market")
        if mm_date != date:
            stale["money_market"] = mm_date

    curves, curve_date = _curve_block(conn, date)
    if curves:
        out.update(curves)
        sources.append("CCIL indicative yields")
        if curve_date != date:
            stale["curves"] = curve_date

    for instrument, key in (("CD", "cd_money_market"), ("CP", "cp_money_market")):
        tenors, issuers, top, used = _paper_block(conn, date, instrument)
        out[key] = tenors
        out[f"{instrument.lower()}_issuers"] = issuers
        out[f"{instrument.lower()}_top_traded"] = top
        if tenors:
            sources.append(f"F-TRAC {instrument} secondary")
            if used != date:
                stale[key] = used

    out["spreads"] = derive.spreads(out.get("cd_money_market"),
                                    out.get("cp_money_market"),
                                    out.get("tbill_range"))

    fx = db.fx(conn, date)
    if not fx:
        row = conn.execute(
            "SELECT MAX(date) d FROM fx_rates WHERE date<=? AND pair='USD/INR'",
            (date,)).fetchone()
        if row and row["d"]:
            fx = db.fx(conn, row["d"])
            stale["fx"] = row["d"]
    if fx:
        out["fx"] = {"pair": fx["pair"], "close": fx["close"], "open": fx["open"],
                     "day_low": fx["day_low"], "day_high": fx["day_high"],
                     "source": fx["source"], "as_of": fx["as_of"]}
        sources.append(fx["source"] or "FX")

    brent = db.commodity(conn, date, "Brent")
    if not brent:
        row = conn.execute(
            "SELECT MAX(date) d FROM commodities WHERE date<=? AND name='Brent'",
            (date,)).fetchone()
        if row and row["d"]:
            brent = db.commodity(conn, row["d"], "Brent")
            stale["brent"] = row["d"]
    if brent:
        out["brent"] = {"price_usd": brent["price_usd"], "as_of": brent["as_of"],
                        "source": brent["source"]}
        sources.append(brent["source"] or "Brent")

    out["_sources"] = sources
    out["_stale"] = stale
    out["_provenance"] = PROVENANCE
    return out


def _is_blank(value):
    if value is None or value == "":
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    return False


def full_report(conn, date):
    """Saved analyst edits laid over the auto-fetched base."""
    base = auto_payload(conn, date)
    saved = db.get_report(conn, date)
    if not saved:
        return base
    merged = dict(base)
    overridden = []
    for key, value in saved.items():
        if key.startswith("_"):
            continue
        if not _is_blank(value):
            merged[key] = value
            overridden.append(key)
    merged["_saved_at"] = True
    # Which blocks the analyst's saved copy supplied rather than a live feed.
    # Without this a value typed months ago still reads as "Auto" in the UI.
    merged["_overridden"] = overridden
    return merged


# Tenors the desk actually quotes. Capped at four so the trend chart stays
# inside the series count where colour alone is still safe to read.
CHART_TENORS = ["1 Month", "3 Months", "6 Months", "12 Months"]


def tenor_series(conn, instrument, days=30, end_date=None):
    """Per-date tenor yields and volume for the trend charts.

    Returns dates oldest-first so a line chart can consume them directly.
    """
    end = end_date or db.today()
    all_dates = [d for d in db.trade_dates(conn) if d <= end]
    dates = sorted(all_dates[:days])

    series = {t: [] for t in CHART_TENORS}
    volume, spread = [], []

    for date in dates:
        trades = db.trades(conn, date, instrument, "SEC")
        if not trades:
            continue
        summary = {r["tenor_label"]: r for r in derive.tenor_summary(trades)}
        for tenor in CHART_TENORS:
            row = summary.get(tenor)
            series[tenor].append({
                "date": date,
                "wavg": row["weighted_avg"] if row else None,
                "low": row["low"] if row else None,
                "high": row["high"] if row else None,
                "volume_cr": row["volume_cr"] if row else None,
            })
        volume.append({
            "date": date,
            "volume_cr": round(sum(t.get("amount_cr") or 0 for t in trades), 2),
            "trade_count": len(trades),
        })

    # Term structure: the most recent curve against the one a week earlier.
    def curve_on(date):
        trades = db.trades(conn, date, instrument, "SEC")
        rows = derive.tenor_summary(trades)
        order = {t: i for i, (_, _, t) in enumerate(derive.TENOR_BUCKETS)}
        return sorted(
            [{"tenor_label": r["tenor_label"], "wavg": r["weighted_avg"],
              "low": r["low"], "high": r["high"], "volume_cr": r["volume_cr"]}
             for r in rows if r["weighted_avg"] is not None],
            key=lambda r: order.get(r["tenor_label"], 99))

    latest = dates[-1] if dates else None
    prior = None
    if latest:
        earlier = [d for d in dates if d < latest]
        prior = earlier[-5] if len(earlier) >= 5 else (earlier[0] if earlier else None)

    return {
        "instrument": instrument,
        "dates": dates,
        "tenors": series,
        "tenor_order": CHART_TENORS,
        "volume": volume,
        "term_structure": {
            "latest": {"date": latest, "points": curve_on(latest)} if latest else None,
            "prior": {"date": prior, "points": curve_on(prior)} if prior else None,
        },
    }


def coverage(conn, date):
    """How much of the report the machine filled in, for the status strip."""
    payload = auto_payload(conn, date)
    auto_keys = [k for k, v in PROVENANCE.items() if v != "manual"]
    filled = [k for k in auto_keys if not _is_blank(payload.get(k))]
    return {
        "auto_fields": len(auto_keys),
        "auto_filled": len(filled),
        "filled": filled,
        "missing": [k for k in auto_keys if k not in filled],
        "manual_fields": [k for k, v in PROVENANCE.items() if v == "manual"],
        "sources": payload.get("_sources", []),
        "stale": payload.get("_stale", {}),
    }
