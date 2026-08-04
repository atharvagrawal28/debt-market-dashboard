"""CCIL F-TRAC (ftrac.co.in) trade-report downloader.

The historical-data pages are plain ASP.NET WebForms. The "Export" button is a
normal form post that returns the file directly, so no browser or login is
needed -- read the three hidden state fields off a GET, then post them back
with the date range.

The file it hands back is named ``.xls`` but is really an HTML ``<table>``,
which is why the old drag-and-drop dashboard had to lean on SheetJS's HTML
sniffing. Here we parse the table directly.

Export window (stated on the page itself): 7 calendar days during business
hours, 30 days after business hours.
"""
import datetime as dt
import os
import re

import requests

from ..config import HTTP_TIMEOUT, RAW_DIR, USER_AGENT

BASE = "https://www.ftrac.co.in/"

# instrument -> (code, supported segments)
INSTRUMENTS = {
    "CD": ("CD", ("SEC", "PRI")),   # Certificate of Deposit
    "CP": ("CP", ("SEC", "PRI")),   # Commercial Paper
    "CB": ("CB", ("SEC",)),         # Corporate Bond Repo (no primary market)
    "NC": ("NC", ("SEC", "PRI")),   # Non-convertible debentures (no public rows today)
}

_CTL = "ctl00$SuperMainContent$"

# The export form rejects a range wider than this outright -- it returns the
# page instead of a file, with no warning. The limit is on the *span*, not on
# how far back you reach, so any period is available at any time of day as
# long as it is requested in short enough windows.
MAX_SPAN_DAYS = 7


class FtracError(RuntimeError):
    pass


def page_name(instrument, segment):
    return f"{instrument}_{segment}_MEM_TRAD_MARK_WATC_VIEW.aspx"


def _hidden(name, html):
    m = re.search(r'name="%s"[^>]*?value="([^"]*)"' % re.escape(name), html)
    return m.group(1) if m else ""


def _state(html):
    return {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATEENCRYPTED": "",
        "__VIEWSTATE": _hidden("__VIEWSTATE", html),
        "__VIEWSTATEGENERATOR": _hidden("__VIEWSTATEGENERATOR", html),
        "__EVENTVALIDATION": _hidden("__EVENTVALIDATION", html),
    }


def _ddmmyyyy(d):
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    return d.strftime("%d/%m/%Y")


def fetch_export(instrument, segment, from_date, to_date, save_raw=True):
    """Download one F-TRAC export. Returns (bytes, filename) or (None, reason)."""
    page = page_name(instrument, segment)
    url = BASE + page
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT,
                      "Accept-Language": "en-US,en;q=0.9"})

    r = s.get(url, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        return None, f"GET {r.status_code}"
    if "An Error Occurred" in r.text:
        return None, "page not available (no such report)"

    # If the form controls we post to are gone, the site has been rebuilt.
    # Say so explicitly -- otherwise the empty result that follows is
    # indistinguishable from a market holiday, and the feed rots unnoticed.
    missing = [f for f in ("txtFrmDealDate", "txtToDealDate", "btnExprtExcel")
               if f not in r.text]
    if missing:
        raise FtracError(f"{page}: expected form fields missing ({', '.join(missing)}) "
                         "- the export page has changed")
    if not _hidden("__VIEWSTATE", r.text):
        raise FtracError(f"{page}: no __VIEWSTATE on the page - not an ASP.NET form any more")

    post_headers = {"Referer": url, "Origin": "https://www.ftrac.co.in"}
    payload = _state(r.text)
    payload.update({
        _CTL + "txtFrmDealDate": _ddmmyyyy(from_date),
        _CTL + "txtToDealDate": _ddmmyyyy(to_date),
        _CTL + "ddlExport": "EXCEL",
        _CTL + "btnExprtExcel": "Export",
    })

    p = s.post(url, data=payload, headers=post_headers, timeout=HTTP_TIMEOUT)
    disposition = p.headers.get("Content-Disposition", "")

    if not disposition:
        # NCD pages need the grid rendered ("View") before Export is armed.
        view = dict(_state(r.text))
        view.update({
            _CTL + "txtFrmDealDate": _ddmmyyyy(from_date),
            _CTL + "txtToDealDate": _ddmmyyyy(to_date),
            _CTL + "ddlExport": "EXCEL",
            _CTL + "btnRefresh": "View",
        })
        v = s.post(url, data=view, headers=post_headers, timeout=HTTP_TIMEOUT)
        payload = _state(v.text)
        payload.update({
            _CTL + "txtFrmDealDate": _ddmmyyyy(from_date),
            _CTL + "txtToDealDate": _ddmmyyyy(to_date),
            _CTL + "ddlExport": "EXCEL",
            _CTL + "btnExprtExcel": "Export",
        })
        p = s.post(url, data=payload, headers=post_headers, timeout=HTTP_TIMEOUT)
        disposition = p.headers.get("Content-Disposition", "")

    if not disposition:
        return None, "no rows published for this range"

    m = re.search(r'filename=([^;\s]+)', disposition)
    filename = m.group(1) if m else f"{instrument}_{segment}.xls"

    if save_raw:
        with open(os.path.join(RAW_DIR, filename), "wb") as fh:
            fh.write(p.content)

    return p.content, filename


_TAG = re.compile(r"<[^>]+>")
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)


def _text(cell):
    return _TAG.sub("", cell).replace("&nbsp;", " ").strip()


def parse_export(content):
    """Parse an F-TRAC export into a list of {column: value} dicts."""
    if content is None:
        return []
    html = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    rows = _ROW.findall(html)
    if not rows:
        return []
    header = [_text(c) for c in _CELL.findall(rows[0])]
    out = []
    for row in rows[1:]:
        cells = [_text(c) for c in _CELL.findall(row)]
        if len(cells) != len(header):
            continue
        out.append(dict(zip(header, cells)))
    return out


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_date(value):
    """F-TRAC prints dates as 25-Aug-2026. Returns an ISO string or None."""
    if not value:
        return None
    v = value.strip()
    m = re.match(r"^(\d{1,2})[-/]([A-Za-z]{3})[-/](\d{4})$", v)
    if m:
        day, mon, year = int(m.group(1)), _MONTHS.get(m.group(2).lower()), int(m.group(3))
        if mon:
            try:
                return dt.date(year, mon, day).isoformat()
            except ValueError:
                return None
    try:
        return dt.date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        return None


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


def normalize(rows, instrument, segment):
    """Map raw F-TRAC columns onto one flat schema shared by all instruments."""
    out = []
    for r in rows:
        rec = {
            "instrument": instrument,
            "segment": segment,
            "isin": (r.get("ISIN") or "").strip(),
            "description": (r.get("ISIN Description") or "").strip(),
            "deal_date": parse_date(r.get("Deal Date")),
            "maturity_date": parse_date(r.get("Maturity Date")),
            "sett_type": (r.get("Sett Type") or "").strip(),
            "sett_date": parse_date(r.get("Sett Date")),
            "deal_time": (r.get("Deal Time") or "").strip() or None,
        }

        if segment == "SEC":
            rec["amount_rs"] = _num(r.get("Trade Amount (Rs)"))
            rec["trade_count"] = 1
            if instrument == "CB":
                # Repo prints a rate, not a yield.
                rec["price"] = _num(r.get("Price (Rs.)"))
                rec["yield_pct"] = _num(r.get("Repo Rate (%)"))
                rec["wap"] = None
                rec["way"] = _num(r.get("WAR (%)"))
                rec["repo_tenor_days"] = _num(r.get("Repo Tenor (Days)"))
            else:
                rec["price"] = _num(r.get("Traded Price (Rs.)"))
                rec["yield_pct"] = _num(r.get("Traded Yield (%)"))
                rec["wap"] = _num(r.get("WAP (Rs.)"))
                rec["way"] = _num(r.get("WAY (%)"))
                rec["repo_tenor_days"] = None
            rec["yield_low"] = rec["yield_high"] = rec["yield_pct"]
            rec["issuer_raw"] = None
        else:  # PRI - one aggregated row per ISIN per day
            rec["amount_rs"] = _num(r.get("Issue Amount (Rs.)"))
            rec["trade_count"] = int(_num(r.get("Trade Count")) or 1)
            rec["price"] = _num(r.get("WAP (Rs.)"))
            rec["wap"] = _num(r.get("WAP (Rs.)"))
            rec["way"] = _num(r.get("WAY (%)"))
            rec["yield_pct"] = rec["way"]
            rec["yield_low"] = _num(r.get("Minimum Yield (%)"))
            rec["yield_high"] = _num(r.get("Maximum Yield (%)"))
            rec["repo_tenor_days"] = None
            rec["issuer_raw"] = (r.get("Issuer") or "").strip() or None

        if not rec["isin"] or not rec["deal_date"]:
            continue
        rec["amount_cr"] = round(rec["amount_rs"] / 1e7, 4) if rec["amount_rs"] else 0.0
        out.append(rec)
    return out


def fetch(instrument, segment, from_date, to_date):
    """Download + parse + normalize in one call. Returns (records, note)."""
    content, info = fetch_export(instrument, segment, from_date, to_date)
    if content is None:
        return [], info
    records = normalize(parse_export(content), instrument, segment)
    return records, info


def date_chunks(from_date, to_date, max_span=MAX_SPAN_DAYS):
    """Split a range into windows the export form will accept."""
    start = dt.date.fromisoformat(str(from_date))
    end = dt.date.fromisoformat(str(to_date))
    if end < start:
        start, end = end, start
    out = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + dt.timedelta(days=max_span - 1), end)
        out.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + dt.timedelta(days=1)
    return out


def fetch_range(instrument, segment, from_date, to_date, progress=None):
    """Fetch a range of any length, chunked to respect the export window."""
    records, notes = [], []
    for start, stop in date_chunks(from_date, to_date):
        try:
            recs, info = fetch(instrument, segment, start, stop)
        except requests.RequestException as e:
            recs, info = [], f"network error: {e.__class__.__name__}"
        except FtracError as e:
            # Structural break -- surface it rather than reporting zero rows.
            recs, info = [], f"LAYOUT CHANGED: {e}"
        records.extend(recs)
        # Carry the reason through when something went wrong; a bare row count
        # would make a broken page look like a quiet holiday.
        failed = info.startswith(("LAYOUT CHANGED", "network error"))
        notes.append(f"{start}..{stop}: {info}" if failed else f"{start}..{stop}: {len(recs)}")
        if progress:
            progress(instrument, segment, start, stop, len(recs))
    return records, "; ".join(notes)


def fetch_all(from_date, to_date, instruments=("CD", "CP", "CB"), progress=None):
    """Pull every supported instrument/segment for a date range of any length."""
    results, notes = [], {}
    for inst in instruments:
        if inst not in INSTRUMENTS:
            continue
        for seg in INSTRUMENTS[inst][1]:
            recs, info = fetch_range(inst, seg, from_date, to_date, progress)
            notes[f"{inst}/{seg}"] = f"{len(recs)} rows ({info})"
            results.extend(recs)
    return results, notes
