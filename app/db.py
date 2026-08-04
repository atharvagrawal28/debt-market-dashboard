"""SQLite store.

The first version kept one JSON blob per date, which made "what did the 3M CD
range do this month" impossible to answer without loading every row. This
keeps trades as a proper fact table and the hand-entered parts of the closing
report as a thin overlay on top.

Ingestion is idempotent: a re-run for a date replaces that date's rows for
that source rather than appending, so the scheduler can safely re-run.
"""
import datetime as dt
import json
import os
import sqlite3

from .config import DB_PATH, LEGACY_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY,
    instrument      TEXT NOT NULL,      -- CD | CP | CB | NC
    segment         TEXT NOT NULL,      -- SEC | PRI
    isin            TEXT NOT NULL,
    description     TEXT,
    issuer          TEXT,               -- normalized issuer name
    deal_date       TEXT NOT NULL,
    maturity_date   TEXT,
    residual_days   INTEGER,
    sett_type       TEXT,
    sett_date       TEXT,
    deal_time       TEXT,
    amount_rs       REAL,
    amount_cr       REAL,
    trade_count     INTEGER DEFAULT 1,
    price           REAL,
    yield_pct       REAL,
    yield_low       REAL,
    yield_high      REAL,
    wap             REAL,
    way             REAL,
    repo_tenor_days REAL
);
CREATE INDEX IF NOT EXISTS ix_trades_date  ON trades(deal_date, instrument, segment);
CREATE INDEX IF NOT EXISTS ix_trades_isin  ON trades(isin, deal_date);
CREATE INDEX IF NOT EXISTS ix_trades_issuer ON trades(issuer, deal_date);

CREATE TABLE IF NOT EXISTS mm_rates (
    date         TEXT NOT NULL,
    kind         TEXT NOT NULL,         -- CALL | TREP | BASKET_REPO | SPECIAL_REPO
    open         REAL, high REAL, low REAL,
    last_trade   REAL, weighted_avg REAL, volume_cr REAL,
    PRIMARY KEY (date, kind)
);

CREATE TABLE IF NOT EXISTS curve_points (
    date         TEXT NOT NULL,
    curve        TEXT NOT NULL,         -- gsec | tbill | sdl
    tenor_label  TEXT NOT NULL,
    bucket       TEXT,
    security     TEXT,
    ytm          REAL,
    PRIMARY KEY (date, curve, tenor_label, security)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    date    TEXT NOT NULL,
    pair    TEXT NOT NULL,
    close   REAL, open REAL, day_low REAL, day_high REAL,
    as_of   TEXT, source TEXT,
    PRIMARY KEY (date, pair)
);

CREATE TABLE IF NOT EXISTS commodities (
    date      TEXT NOT NULL,
    name      TEXT NOT NULL,
    price_usd REAL, as_of TEXT, source TEXT,
    PRIMARY KEY (date, name)
);

-- Hand-entered fields that no free source publishes (OIS, AAA PSU spreads,
-- news, commentary) plus any analyst override of a fetched value.
CREATE TABLE IF NOT EXISTS reports (
    report_date TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id          INTEGER PRIMARY KEY,
    run_at      TEXT NOT NULL DEFAULT (datetime('now')),
    target_date TEXT,
    source      TEXT,
    status      TEXT,                   -- ok | empty | error
    detail      TEXT
);
"""

TRADE_COLUMNS = [
    "instrument", "segment", "isin", "description", "issuer", "deal_date",
    "maturity_date", "residual_days", "sett_type", "sett_date", "deal_time",
    "amount_rs", "amount_cr", "trade_count", "price", "yield_pct",
    "yield_low", "yield_high", "wap", "way", "repo_tenor_days",
]


def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(path=DB_PATH):
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------- writes

def replace_trades(conn, records, deal_dates=None):
    """Insert trades, first clearing the (instrument, segment, date) slices covered.

    Clearing by slice rather than appending is what makes a re-run safe --
    F-TRAC always returns the complete set of trades for a date.
    """
    if deal_dates is None:
        deal_dates = {(r["instrument"], r["segment"], r["deal_date"]) for r in records}
    for instrument, segment, date in deal_dates:
        conn.execute(
            "DELETE FROM trades WHERE instrument=? AND segment=? AND deal_date=?",
            (instrument, segment, date))

    placeholders = ",".join("?" * len(TRADE_COLUMNS))
    conn.executemany(
        f"INSERT INTO trades ({','.join(TRADE_COLUMNS)}) VALUES ({placeholders})",
        [tuple(r.get(c) for c in TRADE_COLUMNS) for r in records])
    conn.commit()
    return len(records)


def upsert_mm_rates(conn, by_date):
    n = 0
    for date, kinds in by_date.items():
        for kind, v in kinds.items():
            conn.execute(
                """INSERT INTO mm_rates (date,kind,open,high,low,last_trade,weighted_avg,volume_cr)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(date,kind) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     last_trade=excluded.last_trade,
                     weighted_avg=excluded.weighted_avg, volume_cr=excluded.volume_cr""",
                (date, kind, v.get("open"), v.get("high"), v.get("low"),
                 v.get("last_trade"), v.get("weighted_avg"), v.get("volume_cr")))
            n += 1
    conn.commit()
    return n


def upsert_curve(conn, by_date):
    n = 0
    for date, curves in by_date.items():
        for curve, points in curves.items():
            for p in points:
                conn.execute(
                    """INSERT INTO curve_points (date,curve,tenor_label,bucket,security,ytm)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(date,curve,tenor_label,security) DO UPDATE SET
                         bucket=excluded.bucket, ytm=excluded.ytm""",
                    (date, curve, p["tenor_label"], p.get("bucket"),
                     p.get("security"), p.get("ytm")))
                n += 1
    conn.commit()
    return n


def upsert_fx(conn, date, rec):
    if not rec:
        return 0
    conn.execute(
        """INSERT INTO fx_rates (date,pair,close,open,day_low,day_high,as_of,source)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(date,pair) DO UPDATE SET
             close=excluded.close, as_of=excluded.as_of, source=excluded.source""",
        (date, rec.get("pair", "USD/INR"), rec.get("close"), rec.get("open"),
         rec.get("day_low"), rec.get("day_high"), rec.get("as_of"), rec.get("source")))
    conn.commit()
    return 1


def upsert_commodity(conn, date, name, rec):
    if not rec:
        return 0
    conn.execute(
        """INSERT INTO commodities (date,name,price_usd,as_of,source)
           VALUES (?,?,?,?,?)
           ON CONFLICT(date,name) DO UPDATE SET
             price_usd=excluded.price_usd, as_of=excluded.as_of, source=excluded.source""",
        (date, name, rec.get("price_usd"), rec.get("as_of"), rec.get("source")))
    conn.commit()
    return 1


def log(conn, target_date, source, status, detail=""):
    conn.execute(
        "INSERT INTO ingest_log (target_date, source, status, detail) VALUES (?,?,?,?)",
        (target_date, source, status, str(detail)[:500]))
    conn.commit()


def save_report(conn, report_date, data):
    conn.execute(
        """INSERT INTO reports (report_date, data, updated_at) VALUES (?,?,datetime('now'))
           ON CONFLICT(report_date) DO UPDATE SET
             data=excluded.data, updated_at=datetime('now')""",
        (report_date, json.dumps(data)))
    conn.commit()


# ---------------------------------------------------------------- reads

def get_report(conn, report_date):
    row = conn.execute("SELECT data FROM reports WHERE report_date=?",
                       (report_date,)).fetchone()
    return json.loads(row["data"]) if row else None


def report_dates(conn):
    return [r["report_date"] for r in
            conn.execute("SELECT report_date FROM reports ORDER BY report_date DESC")]


def trade_dates(conn):
    return [r["deal_date"] for r in
            conn.execute("SELECT DISTINCT deal_date FROM trades ORDER BY deal_date DESC")]


def previous_trade_date(conn, date):
    row = conn.execute(
        "SELECT MAX(deal_date) d FROM trades WHERE deal_date < ?", (date,)).fetchone()
    return row["d"] if row and row["d"] else None


def trades(conn, date, instrument=None, segment="SEC"):
    sql = "SELECT * FROM trades WHERE deal_date=?"
    args = [date]
    if instrument:
        sql += " AND instrument=?"
        args.append(instrument)
    if segment:
        sql += " AND segment=?"
        args.append(segment)
    sql += " ORDER BY maturity_date, issuer, yield_pct"
    return [dict(r) for r in conn.execute(sql, args)]


def mm_rates(conn, date):
    return {r["kind"]: dict(r) for r in
            conn.execute("SELECT * FROM mm_rates WHERE date=?", (date,))}


def latest_mm_date(conn, on_or_before):
    row = conn.execute("SELECT MAX(date) d FROM mm_rates WHERE date<=?",
                       (on_or_before,)).fetchone()
    return row["d"] if row and row["d"] else None


def curve(conn, date, curve_name=None):
    sql = "SELECT * FROM curve_points WHERE date=?"
    args = [date]
    if curve_name:
        sql += " AND curve=?"
        args.append(curve_name)
    return [dict(r) for r in conn.execute(sql + " ORDER BY curve, ytm", args)]


def latest_curve_date(conn, on_or_before):
    row = conn.execute("SELECT MAX(date) d FROM curve_points WHERE date<=?",
                       (on_or_before,)).fetchone()
    return row["d"] if row and row["d"] else None


def fx(conn, date, pair="USD/INR"):
    row = conn.execute("SELECT * FROM fx_rates WHERE date=? AND pair=?",
                       (date, pair)).fetchone()
    return dict(row) if row else None


def commodity(conn, date, name="Brent"):
    row = conn.execute("SELECT * FROM commodities WHERE date=? AND name=?",
                       (date, name)).fetchone()
    return dict(row) if row else None


def ingest_history(conn, limit=40):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM ingest_log ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- migration

def migrate_legacy(conn, legacy_path=LEGACY_DB_PATH):
    """Copy saved reports out of the original reports.db, once."""
    if not os.path.exists(legacy_path):
        return 0
    try:
        old = sqlite3.connect(legacy_path)
        old.row_factory = sqlite3.Row
        rows = old.execute("SELECT report_date, data FROM reports").fetchall()
    except sqlite3.Error:
        return 0
    moved = 0
    for r in rows:
        exists = conn.execute("SELECT 1 FROM reports WHERE report_date=?",
                              (r["report_date"],)).fetchone()
        if not exists:
            conn.execute("INSERT INTO reports (report_date, data) VALUES (?,?)",
                         (r["report_date"], r["data"]))
            moved += 1
    conn.commit()
    old.close()
    return moved


def today():
    return dt.date.today().isoformat()
