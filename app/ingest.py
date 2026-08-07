"""One command that fills the database.

    python -m app.ingest                  # today
    python -m app.ingest --date 2026-07-31
    python -m app.ingest --days 7         # last 7 calendar days (F-TRAC's window)
    python -m app.ingest --backfill 30    # after business hours F-TRAC allows 30

Safe to re-run: each source replaces its own slice rather than appending.
"""
import argparse
import datetime as dt
import sys

from . import db, derive
from .sources import ccil, ftrac, market


def _slices_for(records):
    """Only the (instrument, segment, date) slices we actually received rows for.

    This deliberately does NOT clear the whole requested span. F-TRAC returns
    no file both for a genuine market holiday and for a failed or throttled
    export, and the two are indistinguishable from the outside -- so clearing
    by request range meant one bad fetch silently deleted good history. It did:
    a --days 7 run that came back empty wiped 2026-07-29..2026-08-06.

    Replacing only the dates that returned data means a failed fetch is a
    no-op instead of a deletion. The cost is that a day whose rows are later
    withdrawn upstream keeps its old rows; that is far cheaper than losing
    history that cannot be backfilled once F-TRAC's window has passed.
    """
    return {(r["instrument"], r["segment"], r["deal_date"]) for r in records}


def ingest_trades(conn, from_date, to_date, instruments=("CD", "CP", "CB"),
                  progress=None):
    """Pull F-TRAC trade reports for a date range into the trades table.

    Ranges longer than the export window are chunked automatically, so a
    30-day backfill works at any time of day.

    Each instrument/segment is written as soon as it is fetched rather than
    everything at the end. A run that is killed part-way -- which is a real
    failure mode on this machine, see README -- then keeps the work it had
    already done, and the next run only has to pick up the remainder.
    """
    total, notes = 0, {}
    for inst in instruments:
        if inst not in ftrac.INSTRUMENTS:
            continue
        for seg in ftrac.INSTRUMENTS[inst][1]:
            recs, info = ftrac.fetch_range(inst, seg, from_date, to_date, progress)
            derive.enrich(recs)
            db.replace_trades(conn, recs, _slices_for(recs))
            total += len(recs)

            note = f"{len(recs)} rows ({info})"
            notes[f"{inst}/{seg}"] = note
            if "LAYOUT CHANGED" in note:
                status = "error"
            elif not recs:
                status = "empty"
            else:
                status = "ok"
            db.log(conn, f"{from_date}..{to_date}", f"ftrac:{inst}/{seg}", status, note)
    return total, notes


def ingest_ccil(conn):
    data, notes = ccil.fetch_all()
    counts = {
        "mm_rates": db.upsert_mm_rates(conn, data.get("money_market", {})),
        "curve_points": db.upsert_curve(conn, data.get("tenorwise_yields", {})),
    }
    for key, note in notes.items():
        db.log(conn, None, f"ccil:{key}",
               "error" if note.startswith("failed") else "ok", note)
    return counts, notes


def ingest_market(conn, date, from_date=None):
    """FX and commodities.

    Frankfurter serves historical dates, so unlike the CCIL rate pages this
    one *can* be backfilled -- worth doing, since USD/INR is the only daily
    series the report can reconstruct for past dates.
    """
    data, notes = market.fetch_all(date)
    n = db.upsert_fx(conn, date, data.get("usdinr"))
    n += db.upsert_commodity(conn, date, "Brent", data.get("brent"))
    for key, note in notes.items():
        db.log(conn, date, f"market:{key}",
               "empty" if "unavailable" in note else "ok", note)

    if from_date and from_date < date:
        start = dt.date.fromisoformat(str(from_date))
        end = dt.date.fromisoformat(str(date))
        filled = 0
        day = start
        while day < end:
            iso = day.isoformat()
            if day.weekday() < 5 and not db.fx(conn, iso):   # skip weekends
                rec = market.usdinr(iso)
                # Frankfurter answers a non-publishing day with the previous
                # print; only store it against the date actually quoted.
                if rec and rec.get("as_of") == iso:
                    db.upsert_fx(conn, iso, rec)
                    filled += 1
            day += dt.timedelta(days=1)
        if filled:
            notes["usdinr_backfill"] = f"{filled} earlier date(s) filled"
            n += filled
    return n, notes


def run(date=None, days=1, instruments=("CD", "CP", "CB"), verbose=True):
    target = date or db.today()
    to_date = dt.date.fromisoformat(target)
    from_date = to_date - dt.timedelta(days=max(days, 1) - 1)

    conn = db.init()
    moved = db.migrate_legacy(conn)

    def say(*a):
        if verbose:
            print(*a)

    # Bookend every run. A run that is killed part-way -- machine sleeps, user
    # logs off, console closed -- writes no other rows at all, and silence used
    # to read as health. An unmatched "started" is now the evidence it died.
    db.log(conn, target, "run", "started", f"{from_date}..{to_date}")

    windows = ftrac.date_chunks(from_date.isoformat(), to_date.isoformat())
    say(f"Ingest for {from_date} .. {to_date}"
        + (f"  ({len(windows)} export windows)" if len(windows) > 1 else ""))
    if moved:
        say(f"  migrated {moved} saved report(s) from the legacy reports.db")

    say("\n[1/3] F-TRAC trade reports")

    def progress(inst, seg, start, stop, n):
        if verbose and len(windows) > 1:
            print(f"      {inst}/{seg:3} {start}..{stop}  {n:>4} rows")

    count, notes = ingest_trades(conn, from_date.isoformat(), to_date.isoformat(),
                                 instruments, progress)
    if len(windows) > 1:
        say("      ---")
    for key, note in sorted(notes.items()):
        say(f"      {key:8} {note.split(' (')[0]}")
    say(f"      -> {count} trade rows stored")

    say("\n[2/3] CCIL money market + indicative yields")
    counts, ccil_notes = ingest_ccil(conn)
    for key, note in sorted(ccil_notes.items()):
        say(f"      {key:18} {note}")
    say(f"      -> {counts['mm_rates']} rate rows, {counts['curve_points']} curve points")

    say("\n[3/3] FX and commodities")
    _, market_notes = ingest_market(conn, target, from_date.isoformat())
    for key, note in sorted(market_notes.items()):
        say(f"      {key:16} {note}")

    db.log(conn, target, "run", "completed", f"{count} trade rows")

    problems = health(conn, target)
    if problems:
        say("\n!! ATTENTION")
        for p in problems:
            say(f"      {p}")

    say(f"\nDone. Database: {db.DB_PATH}")
    conn.close()
    return count


def health(conn, target=None):
    """Things a human should look at. Empty list means all good.

    Separates 'a source broke' from 'the market was shut', which the raw
    row counts cannot tell apart on their own.
    """
    target = target or db.today()
    out = []

    recent = db.ingest_history(conn, 60)
    broken = [r for r in recent if r["status"] == "error"]
    for r in broken:
        out.append(f"SOURCE BROKEN  {r['source']}: {r['detail'][:120]}")

    # A run that started and never reported completion was killed part-way.
    # run() logs 'completed' *before* calling this, so when the newest run row
    # is still 'started' it belongs to an earlier, dead run.
    runs = [r for r in recent if r["source"] == "run"]
    if runs and runs[0]["status"] == "started":
        out.append(f"RUN DIED       the run started {runs[0]['run_at']} never finished "
                   "- machine slept, logged off, or the window was closed")

    dates = db.trade_dates(conn)
    if not dates:
        out.append("NO TRADE DATA at all - run: python -m app.ingest --backfill 30")
        return out

    latest = dt.date.fromisoformat(dates[0])
    today = dt.date.fromisoformat(target)
    weekdays_since = sum(1 for i in range(1, (today - latest).days + 1)
                         if (latest + dt.timedelta(days=i)).weekday() < 5)
    if weekdays_since >= 3:
        out.append(f"STALE TRADES   newest deal date is {dates[0]}, "
                   f"{weekdays_since} business days ago - is the scheduled task running?")

    mm = db.latest_mm_date(conn, target)
    if mm:
        gap = sum(1 for i in range(1, (today - dt.date.fromisoformat(mm)).days + 1)
                  if (dt.date.fromisoformat(mm) + dt.timedelta(days=i)).weekday() < 5)
        if gap >= 3:
            out.append(f"STALE RATES    newest CCIL money market date is {mm}")
    else:
        out.append("NO CCIL RATES  money market table is empty")

    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Fetch debt market data into the local database.")
    p.add_argument("--date", help="target date, YYYY-MM-DD (default: today)")
    p.add_argument("--days", type=int, default=1,
                   help="number of days ending at --date (default 1)")
    p.add_argument("--backfill", type=int,
                   help="shorthand for --days N; F-TRAC allows 7 during "
                        "business hours, 30 after")
    p.add_argument("--instruments", default="CD,CP,CB",
                   help="comma-separated subset of CD,CP,CB,NC")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    days = args.backfill or args.days
    instruments = tuple(x.strip().upper() for x in args.instruments.split(",") if x.strip())
    try:
        run(args.date, days, instruments, verbose=not args.quiet)
    except Exception as e:                                       # noqa: BLE001
        print(f"Ingest failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
