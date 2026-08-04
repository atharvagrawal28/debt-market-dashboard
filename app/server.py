"""Flask app: serves both dashboards and the JSON API behind them.

    python -m app.server        ->  http://127.0.0.1:5000

Routes
  /                         closing report UI
  /cdcp                     CD/CP secondary dashboard
  /api/status               what's in the database, what the last fetch did
  /api/dates                dates with trade data
  /api/auto-fetch/<date>    everything the feeds can answer for a date
  /api/report/<date>        GET saved report / POST to save
  /api/report/<date>/export/<fmt>
  /api/cdcp/<date>          aggregated CD+CP rows for the dashboard
  /api/cdcp/<date>/export/<instrument>
  /api/ingest               POST: run a fetch now
  /api/parse-particulars    POST: best-effort parse of a pasted block
"""
import datetime as dt
import re
import threading

from flask import Flask, Response, jsonify, request, send_from_directory

from . import db, derive, exports, ingest, report as report_mod
from .config import BASE_DIR

app = Flask(__name__, static_folder=None)

CDCP_PAGE = "cd_cp_secondary_processorhtml.html"
_ingest_lock = threading.Lock()


def conn():
    return db.init()


# ------------------------------------------------------------------ pages

@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/cdcp")
def cdcp_page():
    return send_from_directory(BASE_DIR, CDCP_PAGE)


@app.route("/<path:filename>")
def static_file(filename):
    return send_from_directory(BASE_DIR, filename)


# ------------------------------------------------------------------ status

@app.get("/api/status")
def status():
    c = conn()
    dates = db.trade_dates(c)
    latest = dates[0] if dates else None
    counts = {r[0]: r[1] for r in c.execute(
        "SELECT instrument || '/' || segment, COUNT(*) FROM trades GROUP BY 1")}
    payload = {
        "database": db.DB_PATH,
        "trade_dates": dates[:60],
        "latest_trade_date": latest,
        "row_counts": counts,
        "total_trades": c.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
        "report_dates": db.report_dates(c),
        "recent_ingests": db.ingest_history(c, 12),
        "coverage": report_mod.coverage(c, latest) if latest else None,
    }
    c.close()
    return jsonify(payload)


@app.get("/api/dates")
def dates():
    c = conn()
    out = {"trade_dates": db.trade_dates(c), "report_dates": db.report_dates(c)}
    c.close()
    return jsonify(out)


# ------------------------------------------------------------------ report

@app.get("/api/auto-fetch/<date>")
def auto_fetch(date):
    c = conn()
    payload = report_mod.auto_payload(c, date)
    c.close()
    # The existing front end reads CCIL fields off `auto.ccil`; keep that shape
    # and hand the whole payload over as well so new blocks come through.
    fx = payload.get("fx") or {}
    brent = payload.get("brent") or {}
    return jsonify({
        "ccil": payload,
        "report": payload,
        "usdinr": {"rate": fx.get("close"), "source": fx.get("source"),
                   "as_of_date": fx.get("as_of")} if fx.get("close") else None,
        "brent": {"price": brent.get("price_usd"), "source": brent.get("source"),
                  "as_of_date": brent.get("as_of")} if brent.get("price_usd") else None,
        "coverage": {"sources": payload.get("_sources", []),
                     "stale": payload.get("_stale", {})},
    })


@app.get("/api/report/<date>")
def get_report(date):
    c = conn()
    saved = db.get_report(c, date)
    c.close()
    if not saved:
        return jsonify(None), 404
    return jsonify(saved)


@app.post("/api/report/<date>")
def post_report(date):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    payload["report_date"] = date
    c = conn()
    db.save_report(c, date, payload)
    c.close()
    return jsonify({"saved": date})


@app.get("/api/report/<date>/full")
def get_full(date):
    c = conn()
    payload = report_mod.full_report(c, date)
    c.close()
    return jsonify(payload)


@app.get("/api/report/<date>/export/<fmt>")
def export_report(date, fmt):
    c = conn()
    payload = report_mod.full_report(c, date)
    c.close()
    try:
        body, mimetype, ext = exports.render(payload, fmt.lower())
    except ValueError:
        return jsonify({"error": f"unknown format {fmt}"}), 400
    filename = f"Fixed_Income_Closing_Report_{date}.{ext}"
    return Response(body, mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------ CD / CP

@app.get("/api/cdcp/<date>")
def cdcp(date):
    c = conn()
    out = {"date": date, "available_dates": db.trade_dates(c)[:60]}
    prev = db.previous_trade_date(c, date)
    for instrument in ("CD", "CP"):
        trades = db.trades(c, date, instrument, "SEC")
        primary = db.trades(c, date, instrument, "PRI")
        tenors = derive.tenor_summary(trades)
        if prev:
            derive.attach_previous(
                tenors, derive.tenor_summary(db.trades(c, prev, instrument, "SEC")),
                "tenor_label", "weighted_avg", "prev_wavg")
        out[instrument.lower()] = {
            "rows": derive.aggregate(trades),
            "tenors": tenors,
            "issuers": derive.issuer_summary(trades, 20),
            "top_traded": derive.top_traded(trades, 10),
            "primary": derive.aggregate(primary),
            "raw_trade_count": len(trades),
            "total_volume_cr": round(sum(t.get("amount_cr") or 0 for t in trades), 2),
        }
    out["previous_date"] = prev
    c.close()
    return jsonify(out)


@app.get("/api/series/<instrument>")
def series(instrument):
    """Time series behind the yield-trend charts."""
    instrument = instrument.upper()
    if instrument not in ("CD", "CP"):
        return jsonify({"error": "instrument must be CD or CP"}), 400
    try:
        days = max(2, min(int(request.args.get("days", 30)), 365))
    except ValueError:
        days = 30
    c = conn()
    payload = report_mod.tenor_series(c, instrument, days, request.args.get("date"))
    c.close()
    return jsonify(payload)


@app.get("/api/cdcp/<date>/export/<instrument>")
def cdcp_export(date, instrument):
    instrument = instrument.upper()
    if instrument not in ("CD", "CP"):
        return jsonify({"error": "instrument must be CD or CP"}), 400
    c = conn()
    rows = derive.aggregate(db.trades(c, date, instrument, "SEC"))
    c.close()
    body = exports.trades_xlsx(rows, f"{instrument} Secondary")
    filename = f"{instrument}_Secondary_Summary_{date}.xlsx"
    return Response(
        body,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------ ingest

@app.post("/api/ingest")
def run_ingest():
    body = request.get_json(silent=True) or {}
    date = body.get("date") or db.today()
    days = int(body.get("days") or 1)
    if not _ingest_lock.acquire(blocking=False):
        return jsonify({"error": "an ingest is already running"}), 409
    try:
        count = ingest.run(date, days, verbose=False)
    except Exception as e:                                       # noqa: BLE001
        return jsonify({"error": f"{e.__class__.__name__}: {e}"}), 502
    finally:
        _ingest_lock.release()
    c = conn()
    payload = {"date": date, "days": days, "trade_rows": count,
               "coverage": report_mod.coverage(c, date),
               "log": db.ingest_history(c, 10)}
    c.close()
    return jsonify(payload)


# ------------------------------------------------- pasted-block import

_NUM = r"[-+]?\d+(?:\.\d+)?"


def parse_particulars(text):
    """Best-effort read of a pasted daily-particulars block.

    Heuristic by necessity: the block is free text a human assembled, so this
    picks up the shapes that recur (label + one or two rates, tenor + range,
    security + today/prev) and leaves anything it cannot place alone. Anything
    the feeds already cover does not need to come through here.
    """
    out = {"call": {"label": "CALL"}, "trep": {"label": "TREP"},
           "top_traded_gsecs": [], "benchmark_curve": [], "sdl_range": [],
           "aaa_psu_corp": [], "cd_money_market": [], "ois_curve": [],
           "tbill_range": [], "news": []}

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        nums = [float(n) for n in re.findall(_NUM, line)]
        upper = line.upper()

        if upper.startswith("CALL") and nums:
            out["call"]["ltr"] = nums[0]
            out["call"]["weighted_avg"] = nums[1] if len(nums) > 1 else None
            continue
        if upper.startswith(("TREP", "TRIPARTY")) and nums:
            out["trep"]["ltr"] = nums[0]
            out["trep"]["weighted_avg"] = nums[1] if len(nums) > 1 else None
            continue

        # "06.94 GS 2036   6.6958   6.6850"
        gsec = re.match(r"^(\d{1,2}\.\d{2}\s*GS\s*\d{4})\D+(%s)(?:\D+(%s))?" % (_NUM, _NUM),
                        line, re.I)
        if gsec:
            out["top_traded_gsecs"].append({
                "name": gsec.group(1).strip(),
                "yield_today": float(gsec.group(2)),
                "yield_prev": float(gsec.group(3)) if gsec.group(3) else None})
            continue

        # "3 Year  6.85 - 6.95"  ->  a low/high range
        rng = re.match(r"^(\d+\s*(?:YEAR|YR|MONTH|MONTHS|MTH|DAYS?)S?)\D+(%s)\s*[-/to]+\s*(%s)"
                       % (_NUM, _NUM), line, re.I)
        if rng:
            row = {"tenor_label": rng.group(1).strip().title(),
                   "low": float(rng.group(2)), "high": float(rng.group(3))}
            bucket = ("cd_money_market" if re.search(r"\bCD\b", upper) else
                      "aaa_psu_corp" if re.search(r"AAA|PSU|CORP", upper) else
                      "sdl_range")
            out[bucket].append(row)
            continue

        if re.search(r"\bOIS\b", upper) and len(nums) >= 2:
            tenor = re.match(r"^(\d+\s*\w+)", line)
            out["ois_curve"].append({
                "tenor_label": tenor.group(1).title() if tenor else line[:12],
                "rate_today": nums[-2], "rate_prev": nums[-1]})
            continue

        tbill = re.match(r"^(\d{2,3}\s*DAYS?)\D+(%s)(?:\D+(%s))?" % (_NUM, _NUM), line, re.I)
        if tbill:
            out["tbill_range"].append({
                "tenor_label": tbill.group(1).title(),
                "rate_today": float(tbill.group(2)),
                "rate_prev": float(tbill.group(3)) if tbill.group(3) else None})
            continue

        if len(line) > 30 and not nums:
            out["news"].append({"category": "Macro", "headline": line})

    return {k: v for k, v in out.items() if v}


@app.post("/api/parse-particulars")
def post_parse_particulars():
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")
    if not text.strip():
        return jsonify({"error": "no text supplied"}), 400
    parsed = parse_particulars(text)
    date = body.get("report_date")
    if date:
        c = conn()
        base = report_mod.auto_payload(c, date)
        c.close()
        for key, value in parsed.items():
            base[key] = value
        parsed = base
    return jsonify(parsed)


def main():
    db.init()
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
