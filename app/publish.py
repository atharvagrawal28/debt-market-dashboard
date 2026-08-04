"""Bake the dashboards into a static site for Vercel.

    python -m app.publish

Writes ./public — plain HTML, one JS data file, no backend. The whole dataset
is ~100 KB gzipped, so a database would add operational cost and buy nothing:
the data changes once a day and the browser can hold all of it.

The dashboards run the *same* UI code as the local server. `data.js` sets
window.__STATIC_DATA__, and the page's apiGet() answers from that instead of
calling Flask. One code path, two deployments.
"""
import datetime as dt
import html as html_mod
import json
import os
import re
import shutil

from . import db, exports, report as report_mod
from .config import BASE_DIR

PUBLIC_DIR = os.path.join(BASE_DIR, "public")
DASHBOARD_SRC = os.path.join(BASE_DIR, "cd_cp_secondary_processorhtml.html")

FINQRATE_URL = "https://www.linkedin.com/company/finqrate"
SWAYAMJEET_URL = "https://www.linkedin.com/in/swayamjitdalai/"

# Shown on every published page. The data is CCIL's; saying so plainly is both
# correct attribution and the honest framing for a public link.
ATTRIBUTION = (
    "Source data: CCIL F-TRAC trade reports and CCIL public market pages, "
    "retrieved from publicly accessible pages. Reproduced for information only, "
    "not for commercial redistribution. Figures are as published by CCIL and "
    "may be revised; no warranty is given as to accuracy or completeness. "
    "Nothing here is investment advice."
)

CREDIT_HTML = (
    f'Made By <a href="{FINQRATE_URL}" target="_blank" rel="noopener noreferrer">'
    f'<b>Finqrate</b></a> for '
    f'<a href="{SWAYAMJEET_URL}" target="_blank" rel="noopener noreferrer">Swayamjeet Dalai</a>'
)


def build_bundle(conn, days=None):
    """Everything the published pages need, as one JSON-serialisable dict."""
    from .server import app

    client = app.test_client()
    dates = db.trade_dates(conn)
    if days:
        dates = dates[:days]

    bundle = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dates": dates,
        "series": {i: client.get(f"/api/series/{i}?days=365").get_json()
                   for i in ("CD", "CP")},
        "cdcp": {d: client.get(f"/api/cdcp/{d}").get_json() for d in dates},
        "reports": {d: client.get(f"/api/report/{d}/full").get_json() for d in dates},
    }
    return bundle


# ---------------------------------------------------------------- pages

def _shell(title, body, active=""):
    """Common chrome: nav, disclaimer, credit."""
    def tab(href, label):
        on = ' style="color:#1D4ED8;border-bottom-color:#1D4ED8"' if active == href else ""
        return f'<a class="navlink" href="{href}"{on}>{label}</a>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_mod.escape(title)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@2.47.0/tabler-icons.min.css">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',system-ui,sans-serif;color:#0F172A;background:#fff}}
.topnav{{border-bottom:1px solid #E2E8F0;background:#fff;position:sticky;top:0;z-index:40}}
.topnav-in{{max-width:1180px;margin:0 auto;padding:0 1rem;display:flex;align-items:center;gap:22px;height:54px}}
.brand{{font-weight:800;font-size:15px;letter-spacing:-.01em;display:flex;align-items:center;gap:8px}}
.brand i{{color:#1D4ED8;font-size:19px}}
.navlink{{font-size:13.5px;font-weight:700;color:#64748B;text-decoration:none;height:54px;
  display:inline-flex;align-items:center;border-bottom:3px solid transparent}}
.navlink:hover{{color:#1D4ED8}}
.disclaimer{{max-width:1180px;margin:1.25rem auto 0;padding:0 1rem}}
.disclaimer-in{{background:#F8FAFC;border:1px solid #E2E8F0;border-left:3px solid #94A3B8;
  border-radius:10px;padding:.7rem .95rem;font-size:11.5px;color:#64748B;line-height:1.6}}
.credit{{text-align:center;font-size:12.5px;color:#94A3B8;font-weight:500;letter-spacing:.04em;padding:26px 1rem 30px}}
.credit b{{color:#1D4ED8;font-weight:800}}
.credit a{{color:inherit;text-decoration:none}}
.credit a:hover{{color:#1D4ED8}}
</style></head><body>
<div class="topnav"><div class="topnav-in">
  <span class="brand"><i class="ti ti-chart-candle"></i>Debt Market</span>
  {tab('index.html', 'Overview')}{tab('cdcp.html', 'CD / CP Secondary')}{tab('report.html', 'Closing Report')}
</div></div>
<div class="disclaimer"><div class="disclaimer-in">{html_mod.escape(ATTRIBUTION)}</div></div>
{body}
<div class="credit">{CREDIT_HTML}</div>
</body></html>"""


def build_dashboard(bundle):
    """The CD/CP dashboard, rewired to read from the baked bundle."""
    src = open(DASHBOARD_SRC, encoding="utf-8").read()
    # Load the data before the page script runs, and add the shared nav.
    src = src.replace("<body>", '<body>\n<script src="data.js"></script>', 1)
    nav = ('<div style="border-bottom:1px solid #E2E8F0;background:#fff">'
           '<div style="max-width:1180px;margin:0 auto;padding:.85rem 1rem;display:flex;'
           'gap:20px;align-items:center;font-family:Inter,system-ui,sans-serif">'
           '<span style="font-weight:800;font-size:15px">Debt Market</span>'
           '<a href="index.html" style="font-size:13.5px;font-weight:700;color:#64748B;'
           'text-decoration:none">Overview</a>'
           '<a href="cdcp.html" style="font-size:13.5px;font-weight:700;color:#1D4ED8;'
           'text-decoration:none">CD / CP Secondary</a>'
           '<a href="report.html" style="font-size:13.5px;font-weight:700;color:#64748B;'
           'text-decoration:none">Closing Report</a></div></div>')
    src = src.replace('<div class="shell">', nav + '\n<div class="shell">', 1)
    note = (f'<div style="background:#F8FAFC;border:1px solid #E2E8F0;border-left:3px solid #94A3B8;'
            f'border-radius:10px;padding:.7rem .95rem;font-size:11.5px;color:#64748B;'
            f'line-height:1.6;margin-bottom:1.25rem">{html_mod.escape(ATTRIBUTION)}</div>')
    src = src.replace('<div class="header">', note + '\n  <div class="header">', 1)
    return src


def _fmt(v, digits=2):
    if v is None or v == "":
        return "–"
    if isinstance(v, (int, float)):
        return f"{v:,.{digits}f}"
    return html_mod.escape(str(v))


def build_report_page(bundle):
    """Read-only closing report with a date switcher.

    The live form saves and exports; a static page cannot, so this renders the
    finished note instead of shipping inputs whose Save button does nothing.
    """
    dates = [d for d in bundle["dates"] if bundle["reports"].get(d)]
    sections = []
    for date in dates:
        rep = bundle["reports"][date]
        blocks = []
        for heading, key, _kind, columns in exports.SECTIONS:
            rows = rep.get(key) or []
            if isinstance(rows, dict):
                rows = [rows]
            rows = [r for r in rows if isinstance(r, dict)]
            if not rows:
                continue
            head = "".join(f"<th>{html_mod.escape(h)}</th>" for h, _, _ in columns)
            body = ""
            for r in rows:
                cells = ""
                for _h, field, spec in columns:
                    digits = 4 if spec and "4f" in spec else 2
                    cells += f"<td>{_fmt(r.get(field), digits)}</td>"
                body += f"<tr>{cells}</tr>"
            blocks.append(f'<h3>{html_mod.escape(heading)}</h3>'
                          f'<div class="tblwrap"><table><tr>{head}</tr>{body}</table></div>')

        fx = rep.get("fx") or {}
        kpis = []
        if fx.get("close"):
            kpis.append(("USD/INR", f"{fx['close']:.4f}"))
        for kind, label in (("CALL", "CALL w.avg"), ("TREP", "TREP w.avg")):
            block = rep.get(kind.lower()) or {}
            if block.get("weighted_avg") is not None:
                kpis.append((label, f"{block['weighted_avg']:.4f}%"))
        kpi_html = "".join(
            f'<div class="kpi"><div class="kpi-l">{html_mod.escape(l)}</div>'
            f'<div class="kpi-v">{html_mod.escape(v)}</div></div>' for l, v in kpis)

        commentary = (rep.get("ai_commentary") or "").strip()
        if commentary:
            blocks.append('<h3>Closing Commentary</h3><p class="comm">'
                          + html_mod.escape(commentary).replace("\n", "<br>") + "</p>")

        sources = list(dict.fromkeys(rep.get("_sources") or []))
        src_html = (f'<div class="srcline">Auto-filled from {html_mod.escape("; ".join(sources))}</div>'
                    if sources else "")

        sections.append(
            f'<section class="day" data-date="{date}" style="display:none">'
            f'<div class="kpirow">{kpi_html}</div>{src_html}{"".join(blocks) or "<p class=comm>No data stored for this date.</p>"}'
            f"</section>")

    options = "".join(f'<option value="{d}">{d}</option>' for d in dates)
    body = f"""
<div class="wrap">
  <h1>Fixed Income Closing Report</h1>
  <p class="sub">End-of-day summary of the Indian debt market. Money market, benchmark
     curve, T-Bill, SDL and CD/CP ranges fill automatically from CCIL and F-TRAC.</p>
  <div class="picker"><label for="d">Report date</label>
    <select id="d">{options}</select></div>
  {"".join(sections) if sections else '<p class="comm">No reports published yet.</p>'}
</div>
<style>
.wrap{{max-width:1180px;margin:0 auto;padding:1.5rem 1rem 1rem}}
h1{{font-size:26px;font-weight:800;letter-spacing:-.02em}}
.sub{{font-size:13.5px;color:#64748B;margin-top:5px;font-weight:500;max-width:760px;line-height:1.6}}
.picker{{margin:1.4rem 0 1.2rem;display:flex;align-items:center;gap:10px}}
.picker label{{font-size:11.5px;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:.06em}}
.picker select{{font-size:13.5px;font-weight:600;padding:7px 12px;border-radius:10px;
  border:1px solid #E2E8F0;background:#fff;color:#0F172A;height:36px}}
.kpirow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:1.1rem}}
.kpi{{background:#fff;border:1px solid #E2E8F0;border-radius:14px;padding:.9rem 1.1rem;
  position:relative;overflow:hidden}}
.kpi::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
  background:linear-gradient(180deg,#1D4ED8,#60A5FA)}}
.kpi-l{{font-size:11.5px;color:#64748B;text-transform:uppercase;letter-spacing:.06em;font-weight:700}}
.kpi-v{{font-size:22px;font-weight:800;margin-top:3px}}
.srcline{{font-size:11.5px;color:#94A3B8;margin-bottom:1rem;font-weight:500}}
h3{{font-size:13px;font-weight:800;color:#1D4ED8;text-transform:uppercase;letter-spacing:.05em;
  margin:1.6rem 0 .6rem}}
.tblwrap{{border:1px solid #E2E8F0;border-radius:12px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;min-width:420px}}
th{{background:#1D4ED8;color:#fff;font-size:10.5px;padding:8px 10px;text-align:left;
  text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}}
td{{padding:7px 10px;border-bottom:1px solid #F1F5F9;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr:nth-child(even) td{{background:#F8FAFC}}
.comm{{font-size:13.5px;line-height:1.7;color:#334155;max-width:800px}}
</style>
<script>
(function(){{
  var sel=document.getElementById('d');
  if(!sel) return;
  function show(){{
    document.querySelectorAll('.day').forEach(function(s){{
      s.style.display = s.dataset.date===sel.value ? '' : 'none';
    }});
  }}
  sel.addEventListener('change',show);
  if(sel.options.length) {{ sel.value=sel.options[0].value; show(); }}
}})();
</script>"""
    return _shell("Fixed Income Closing Report", body, "report.html")


def build_index(bundle, conn):
    """Landing page: headline numbers plus what the site contains."""
    dates = bundle["dates"]
    latest = dates[0] if dates else None
    cd = (bundle["cdcp"].get(latest) or {}).get("cd", {}) if latest else {}
    cp = (bundle["cdcp"].get(latest) or {}).get("cp", {}) if latest else {}

    def tenor_cards(block, label):
        rows = block.get("tenors") or []
        if not rows:
            return ""
        cards = ""
        for r in rows:
            chg = ""
            if r.get("change_bps") is not None:
                cls = "up" if r["change_bps"] > 0.05 else ("down" if r["change_bps"] < -0.05 else "flat")
                arrow = "▲" if r["change_bps"] > 0.05 else ("▼" if r["change_bps"] < -0.05 else "●")
                chg = f'<span class="chg {cls}">{arrow} {abs(r["change_bps"]):.1f} bps</span>'
            cards += (f'<div class="tc"><div class="tc-n">{html_mod.escape(r["tenor_label"])}</div>'
                      f'<div class="tc-r">{r["low"]:.2f} – {r["high"]:.2f}</div>'
                      f'<div class="tc-m">wtd avg {r["weighted_avg"]:.4f} {chg}</div></div>')
        return f'<h3>{label} yield range by tenor</h3><div class="tcgrid">{cards}</div>'

    total = sum(len(v.get("cd", {}).get("rows", [])) + len(v.get("cp", {}).get("rows", []))
                for v in bundle["cdcp"].values())
    issuers = conn.execute("SELECT COUNT(DISTINCT issuer) FROM trades").fetchone()[0]
    trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    body = f"""
<div class="wrap">
  <h1>Indian Debt &amp; Money Market</h1>
  <p class="sub">Certificate of Deposit and Commercial Paper secondary trades, benchmark
     curves and money market rates — collected automatically each evening from CCIL
     F-TRAC and CCIL's public market pages, then aggregated by issuer, tenor and yield.</p>

  <div class="kpirow">
    <div class="kpi"><div class="kpi-l">Trading days</div><div class="kpi-v">{len(dates)}</div></div>
    <div class="kpi"><div class="kpi-l">Trades processed</div><div class="kpi-v">{trades:,}</div></div>
    <div class="kpi"><div class="kpi-l">Issuers tracked</div><div class="kpi-v">{issuers}</div></div>
    <div class="kpi"><div class="kpi-l">Latest deal date</div><div class="kpi-v">{latest or '–'}</div></div>
  </div>

  {tenor_cards(cd, 'CD')}
  {tenor_cards(cp, 'CP')}

  <div class="cards">
    <a class="card" href="cdcp.html">
      <div class="card-i"><i class="ti ti-chart-histogram"></i></div>
      <div class="card-t">CD / CP Secondary Dashboard</div>
      <div class="card-d">Yield trend by tenor, term structure, traded volume, issuer
        league table and the full trade-level detail for any published date.</div>
    </a>
    <a class="card" href="report.html">
      <div class="card-i"><i class="ti ti-file-text"></i></div>
      <div class="card-t">Fixed Income Closing Report</div>
      <div class="card-d">End-of-day summary: money market, benchmark G-Sec curve,
        T-Bill, SDL, CD/CP ranges, spreads and USD/INR.</div>
    </a>
  </div>
  <p class="upd">Data as of {html_mod.escape(bundle['generated'])}. Refreshed automatically each evening.</p>
</div>
<style>
.wrap{{max-width:1180px;margin:0 auto;padding:1.5rem 1rem 1rem}}
h1{{font-size:30px;font-weight:800;letter-spacing:-.02em}}
.sub{{font-size:14.5px;color:#64748B;margin-top:7px;font-weight:500;max-width:790px;line-height:1.65}}
.kpirow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:1.6rem 0 .5rem}}
.kpi{{background:#fff;border:1px solid #E2E8F0;border-radius:14px;padding:.95rem 1.15rem;position:relative;overflow:hidden}}
.kpi::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:linear-gradient(180deg,#1D4ED8,#60A5FA)}}
.kpi:nth-child(2)::before{{background:linear-gradient(180deg,#eb6834,#FBBF24)}}
.kpi:nth-child(3)::before{{background:linear-gradient(180deg,#1baf7a,#34D399)}}
.kpi-l{{font-size:11.5px;color:#64748B;text-transform:uppercase;letter-spacing:.06em;font-weight:700}}
.kpi-v{{font-size:23px;font-weight:800;margin-top:3px;letter-spacing:-.01em}}
h3{{font-size:13px;font-weight:800;color:#1D4ED8;text-transform:uppercase;letter-spacing:.05em;margin:1.8rem 0 .7rem}}
.tcgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:10px}}
.tc{{border:1px solid #E2E8F0;border-radius:11px;padding:.7rem .85rem;background:#F8FAFC}}
.tc-n{{font-size:11.5px;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:.05em}}
.tc-r{{font-size:17px;font-weight:800;margin:3px 0 2px}}
.tc-m{{font-size:11.5px;color:#64748B;font-weight:500}}
.chg{{font-weight:700}}.chg.up{{color:#DC2626}}.chg.down{{color:#059669}}.chg.flat{{color:#94A3B8}}
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:2rem 0 1rem}}
.card{{display:block;text-decoration:none;color:inherit;background:#fff;border:1px solid #E2E8F0;
  border-radius:16px;padding:1.35rem 1.5rem;transition:box-shadow .2s,transform .2s,border-color .2s}}
.card:hover{{box-shadow:0 14px 32px -12px rgba(29,78,216,.22);transform:translateY(-2px);border-color:#BFDBFE}}
.card-i{{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#1D4ED8,#3B82F6);
  color:#fff;display:flex;align-items:center;justify-content:center;font-size:21px;margin-bottom:.75rem}}
.card-t{{font-size:16px;font-weight:800;letter-spacing:-.01em}}
.card-d{{font-size:13px;color:#64748B;margin-top:5px;line-height:1.6;font-weight:500}}
.upd{{font-size:12px;color:#94A3B8;margin-top:1.5rem;font-weight:500}}
@media(max-width:820px){{.cards{{grid-template-columns:1fr}}}}
</style>"""
    return _shell("Indian Debt & Money Market", body, "index.html")


def publish(days=None, verbose=True):
    conn = db.init()

    def say(*a):
        if verbose:
            print(*a)

    # Clear the contents rather than the directory itself. On Windows anything
    # holding the folder open -- a terminal sitting in it, a preview server,
    # Explorer -- makes rmtree fail with WinError 32, and a scheduled publish
    # must not die because a window was left open.
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    for entry in os.listdir(PUBLIC_DIR):
        path = os.path.join(PUBLIC_DIR, entry)
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        except OSError as e:
            say(f"  note: could not remove {entry} ({e.__class__.__name__}) - overwriting")

    say("Building static site…")
    bundle = build_bundle(conn, days)
    say(f"  {len(bundle['dates'])} trading days")

    payload = json.dumps(bundle, separators=(",", ":"))
    with open(os.path.join(PUBLIC_DIR, "data.js"), "w", encoding="utf-8") as fh:
        fh.write("window.__STATIC_DATA__=" + payload + ";")
    say(f"  data.js        {len(payload)/1024:,.0f} KB")

    for name, content in (
        ("index.html", build_index(bundle, conn)),
        ("cdcp.html", build_dashboard(bundle)),
        ("report.html", build_report_page(bundle)),
    ):
        with open(os.path.join(PUBLIC_DIR, name), "w", encoding="utf-8") as fh:
            fh.write(content)
        say(f"  {name:14} {len(content)/1024:,.0f} KB")

    # Long cache on the data file is wrong -- it is replaced every evening.
    with open(os.path.join(BASE_DIR, "vercel.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "outputDirectory": "public",
            "cleanUrls": True,
            "headers": [{
                "source": "/data.js",
                "headers": [{"key": "Cache-Control", "value": "public, max-age=0, must-revalidate"}],
            }],
        }, fh, indent=2)

    say(f"\nWrote {PUBLIC_DIR}")
    conn.close()
    return bundle


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Bake the dashboards into ./public for Vercel.")
    p.add_argument("--days", type=int, help="publish only the most recent N trading days")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)
    publish(a.days, verbose=not a.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
