"""FX and commodity reference prints.

USD/INR comes from the FBIL reference rate republished by Frankfurter (free,
no key). Brent comes from FRED's EIA series; that host is not reachable from
every network, so it is best-effort and the field stays manual when it fails.
"""
import datetime as dt

import requests

from ..config import USER_AGENT

FRANKFURTER = "https://api.frankfurter.dev/v1/"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"


def usdinr(date=None):
    """FBIL USD/INR reference rate on or before `date`. Returns dict or None.

    Frankfurter answers a non-publishing day with the previous print, so the
    returned `as_of` can be earlier than the date asked for.
    """
    when = date or dt.date.today().isoformat()
    url = f"{FRANKFURTER}{when}?base=USD&symbols=INR"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError):
        return None
    rate = (payload.get("rates") or {}).get("INR")
    if rate is None:
        return None
    return {
        "pair": "USD/INR",
        "close": float(rate),
        "as_of": payload.get("date"),
        "source": "FBIL reference rate via Frankfurter",
        "stale": payload.get("date") != when,
    }


def brent(date=None):
    """Latest Brent print at or before `date` from FRED. None if unreachable."""
    try:
        r = requests.get(FRED_CSV, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return None

    cutoff = date or dt.date.today().isoformat()
    latest = None
    for line in r.text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        day, value = parts[0].strip(), parts[1].strip()
        if value in ("", "."):
            continue
        if day <= cutoff:
            latest = (day, value)
    if not latest:
        return None
    return {
        "price_usd": float(latest[1]),
        "as_of": latest[0],
        "source": "FRED DCOILBRENTEU (EIA)",
        "stale": latest[0] != cutoff,
    }


def fetch_all(date=None):
    data, notes = {}, {}
    for name, fn in (("usdinr", usdinr), ("brent", brent)):
        value = fn(date)
        data[name] = value
        if value is None:
            notes[name] = "unavailable (source unreachable) - stays manual"
        elif value.get("stale"):
            notes[name] = f"last print {value['as_of']} (no publish for {date})"
        else:
            notes[name] = f"as of {value['as_of']}"
    return data, notes
