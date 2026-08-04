# Fixed Income & Debt Market Dashboard

Two dashboards over one automated data layer. A scheduled job pulls CD/CP/CB
Repo trades from CCIL F-TRAC and rates from CCIL's public pages into a local
SQLite database; both dashboards read from that database instead of asking you
to download and drag files.

## Run it

**Double-click `start_dashboard.bat`.** It installs anything missing, starts the
backend, and opens the browser. Leave that window open while you work.

| URL | Page |
|---|---|
| <http://127.0.0.1:5000/> | Fixed Income Closing Report |
| <http://127.0.0.1:5000/cdcp> | CD / CP Secondary Market Dashboard |

> **Do not open `index.html` by double-clicking it.** It loads over `file://`,
> where every `/api/…` call fails and nothing populates. Always go through
> <http://127.0.0.1:5000>. The page now says so explicitly if you do.

Equivalent commands, if you prefer a terminal:

```bash
pip install -r requirements.txt
python -m app.ingest --days 7     # fill the database
python -m app.server              # http://127.0.0.1:5000
```

### Known issue: long scheduled tasks get killed on this machine

Verified 2026-08-04. **Any** scheduled task that runs for more than a few tens of
seconds is terminated with exit code `3221225786` (`0xC000013A`,
`STATUS_CONTROL_C_EXIT`). Reproduced with a bare `python -c "time.sleep(45)"` —
it is not this project's code:

| Test | Result |
|---|---|
| Instant task (`print('hi')`) | exit 0 |
| 45-second sleep | **killed** |
| `run_daily.bat` run by hand from a terminal | exit 0 |
| `run_daily.bat` run by Task Scheduler | **killed** |

The same ingest completed fine under Task Scheduler on 2026-08-03 (85 seconds,
exit 0), so something on the machine changed. Likely candidates, in order:
antivirus/endpoint protection, a power or sleep policy, or a group policy on
scheduled tasks. **This needs checking on the machine — it cannot be fixed from
inside the project.**

Until it is fixed:

- Run `run_daily.bat` by hand (double-click) — that path works.
- The ingest now **commits each instrument as it is fetched**, so a killed run
  keeps the work it had already done and the next one continues. A kill costs
  time, not data.
- An unfinished run is recorded, and the next run reports
  `RUN DIED  the run started … never finished`.

### Knowing when a source breaks

A scraper's worst failure is the quiet one: the site gets redesigned, the parse
returns nothing, and "0 rows" looks exactly like a market holiday. Both sources
are checked structurally, so that cannot happen silently:

- **F-TRAC** — if `txtFrmDealDate`, `txtToDealDate`, `btnExprtExcel` or
  `__VIEWSTATE` are missing from the page, the run reports
  `LAYOUT CHANGED: …` and logs status `error`.
- **CCIL** — if the page loads but the table we scrape (`Type`/`Wtd Avg`,
  `Tenor Bucket`/`YTM (%)`) is not in it, it raises `LayoutError`.

Every run ends with a health summary and prints an `!! ATTENTION` block for
broken sources, trade data more than three business days stale, or missing CCIL
rates. Nothing to report means the line is silent.

### If fetch fails

Almost always the backend is not running. In order:

1. Is the `start_dashboard.bat` window still open? Closing it stops the server.
2. Is the address bar showing `http://127.0.0.1:5000`, not `file:///C:/…`?
3. Check the database directly — this needs no server:
   `python -c "from app import db; c=db.init(); print(db.trade_dates(c))"`
4. Check the last scheduled run: `data\ingest.log`.

## What is automated

Everything below is fetched with plain HTTP — no login, no browser
automation, no paid terminal.

| Field | Source | How |
|---|---|---|
| CD / CP / CB Repo secondary trades | CCIL F-TRAC | `.aspx` export form post |
| CD / CP primary issuance | CCIL F-TRAC | same |
| CALL, TREP, Basket & Special Repo — O/H/L/last/w.avg/volume | CCIL money market page | server-rendered table |
| Benchmark G-Sec curve (1-2Y … 28-30Y) + security names | CCIL indicative yields | server-rendered table |
| T-Bill 91D / 182D / 364D | CCIL indicative yields | same |
| SDL 5Y / 10Y / 15Y | CCIL indicative yields | same |
| **CD / CP yield range by tenor** | **derived** | trades bucketed by residual maturity |
| **CD / CP spread over T-Bill** | **derived** | weighted-avg yield vs comparable bill |
| **Day-on-day change (bps)** | **derived** | previous stored business day |
| **Most active issuers** | **derived** | volume-weighted, per instrument |
| USD/INR close | FBIL via Frankfurter | free JSON API |
| Brent | FRED `DCOILBRENTEU` | best-effort; see limitations |

Still typed by hand, because no free source publishes them:

- OIS curve, AAA PSU corporate spreads, top-traded G-Secs
- USD/INR open and day range
- News headlines and closing commentary

The closing report's section badges show **Auto** or **Manual** based on what
actually arrived, and say `Auto · from <date>` when a block fell back to an
earlier publish.

## The scheduled task

A Windows scheduled task named **`Debt Market Ingest`** is already registered
and runs `run_daily.bat` **daily at 19:00**, after the market closes. It pulls
the last **7 days** — the widest single export window F-TRAC accepts, so it
costs exactly the same as a one-day pull but survives a week of missed runs
(holidays, laptop off, VPN down). `StartWhenAvailable` is set, so if the PC is
off at 19:00 it runs at the next opportunity rather than skipping the day.

The task only needs the machine on and you logged in — it does **not** need the
dashboard running. It writes to the database; the dashboard just reads it.

```powershell
# check when it last ran and what it returned (0 = success)
Get-ScheduledTaskInfo -TaskName "Debt Market Ingest"

# run it now instead of waiting
Start-ScheduledTask -TaskName "Debt Market Ingest"

# change the time
Set-ScheduledTask -TaskName "Debt Market Ingest" -Trigger (New-ScheduledTaskTrigger -Daily -At 6:30PM)

# remove it
Unregister-ScheduledTask -TaskName "Debt Market Ingest" -Confirm:$false
```

Output is appended to `data\ingest.log`. Re-running any date is safe: each
source replaces its own slice rather than appending.

## Layout

```
app/
  config.py          paths, user agent, timeouts
  db.py              SQLite schema + queries; migrates the old reports.db
  derive.py          issuer names, tenor buckets, spreads, day-on-day
  report.py          assembles a date: auto fields + your saved overlay
  exports.py         TXT / HTML / CSV / DOCX / XLSX renderers
  ingest.py          the scheduled entry point
  server.py          Flask app + JSON API
  sources/
    ftrac.py         CCIL F-TRAC export downloader + parser
    ccil.py          CCIL money market and indicative yields
    market.py        USD/INR, Brent
index.html                          closing report UI
cd_cp_secondary_processorhtml.html  CD/CP dashboard UI
data/market.db                      the database
data/raw/                           every downloaded file, kept for audit
```

## How the F-TRAC download works

The historical-data pages are ASP.NET WebForms. Read `__VIEWSTATE`,
`__VIEWSTATEGENERATOR` and `__EVENTVALIDATION` off a GET, then post them back
with `txtFrmDealDate`, `txtToDealDate` and `btnExprtExcel=Export`. The response
is the file itself.

`__VIEWSTATEENCRYPTED` must be present in the post body (empty is fine) or the
app returns its generic error page instead of the file. The file is named
`.xls` but is really an HTML `<table>`, which is why the original dashboard
needed SheetJS's HTML sniffing to read it.

## Publishing a public link (Vercel)

```bash
python -m app.publish        # writes ./public
```

The whole dataset is ~100 KB gzipped, so the published site is **static** — no
database, no serverless functions, no API keys. `data.js` sets
`window.__STATIC_DATA__` and the dashboards' `apiGet()` reads from it instead of
calling Flask, so the published pages run the same UI code as the local server.

`public/` contains:

| File | |
|---|---|
| `index.html` | Overview — headline stats, current tenor ranges, links |
| `cdcp.html` | Full CD/CP dashboard: charts, tenor cards, trade-level table, Excel export |
| `report.html` | Read-only closing report with a date switcher |
| `data.js` | The baked dataset |

### One-time setup

1. Create an empty **GitHub** repo.
2. In this folder:
   ```bash
   git init && git add . && git commit -m "Debt market dashboard"
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
3. Go to **vercel.com**, sign in with GitHub, *Add New → Project*, pick the repo,
   deploy. `vercel.json` already points it at `public/`. No environment variables,
   no build command, no database.

After that the daily task rebuilds `public/`, commits and pushes automatically,
and Vercel redeploys. The link updates itself every evening.

`.gitignore` keeps `market.db`, `data/raw/` and the logs off GitHub — only the
generated site is published.

### Why not Postgres / Neon / Supabase

The data changes once a day and the entire history is ~100 KB gzipped; a year
would be under 2 MB. A database would add a second service to keep alive,
connection handling in serverless, cold starts, credentials to rotate, and a
free tier that can pause — for no gain over a file the browser downloads once.
Revisit only if the published site ever needs to accept writes.

Note that the published pages carry a CCIL source attribution and a
non-commercial notice. That is deliberate: a public link is redistribution, and
CCIL's terms restrict commercial reuse of this data.

## Charts

The CD/CP dashboard renders three charts per instrument off `/api/series/<CD|CP>`:

- **Yield trend by tenor** — volume-weighted average for 1M / 3M / 6M / 12M
- **Term structure** — the latest curve against the one a week earlier
- **Traded volume** — crore per trading day

One control row above the tabs scopes every chart (10D / 30D / All), and a
**Table** toggle gives the same numbers without hovering. Charts are
dependency-free inline SVG, so they work offline.

Series colours are categorical slots 1–4 in fixed order, one per tenor, so
changing the window never repaints a series. The palette was checked with the
data-viz validator against the white chart surface — worst adjacent pair ΔE 9.1
(protan) / 22.9 (normal vision). Two slots sit under 3:1 contrast, which is why
every line carries a direct end label and the table view exists.

## Backfilling

```bash
python -m app.ingest --backfill 30      # or any number of days
```

The export form rejects a range wider than **7 days** outright — it returns the
page instead of a file, with no warning. But that cap is on the **span**, not on
how far back you can reach: a 7-day window from six weeks ago downloads fine, at
any time of day. So `ingest` splits long ranges into 7-day windows
automatically and a 30-day backfill works whenever you run it, not just after
business hours.

USD/INR is backfilled too, since Frankfurter serves historical dates. Rates
quoted for a non-publishing day are skipped rather than stored against the
wrong date.

## Limitations worth knowing

- **CCIL rate pages publish only the last two days**, so the money market and
  benchmark/T-Bill/SDL curves *cannot* be backfilled — that history builds
  forward from the day you start running the scheduler. F-TRAC trade data and
  USD/INR have no such limit.
- F-TRAC's own stated retention is 7 days during business hours and 30 after.
  Reaching further back than 30 days has not been tested.
- **NCD trade data** is not published publicly. The endpoints exist and are
  wired up, but return no rows.
- **CB Repo has no primary market** page; only secondary is available.
- **Brent (FRED) is unreachable from this network** — every request times out.
  The field degrades to manual. If it works from another connection it will
  populate on its own.
- **Intraday**: F-TRAC serves trades as they print, so a mid-session pull gives
  a partial day. The evening scheduled run is the authoritative one.
- **CCIL terms restrict commercial redistribution** of this data. This reads
  public pages for desk use; check with CCIL before republishing.

## Notes

- `reports.db` (the original single-blob store) is migrated into
  `data/market.db` automatically on first run and is no longer written to.
- The two `.xls` files in the project root are the original manual downloads.
  Nothing reads them any more — `data/raw/` holds fetched copies instead.
- The CD/CP dashboard keeps drag-and-drop upload as a fallback for dates
  outside the F-TRAC window.
