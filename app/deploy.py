"""Trigger a Vercel deployment directly.

    python -m app.deploy

Vercel's GitHub integration on this project does not rebuild on push -- the
production deployment stayed pinned to the commit it was imported at. Rather
than depend on that, this posts to a Vercel **Deploy Hook**, which always
builds the latest commit on the configured branch.

The hook URL is a secret: anyone holding it can trigger deployments. It lives
in data/deploy_hook.txt, which is gitignored, because this repository is
public. It is never printed in full.

To create one: Vercel -> project -> Settings -> Git -> Deploy Hooks ->
name it, pick branch `main`, Create Hook, copy the URL into that file.
"""
import os
import sys

import requests

from .config import DATA_DIR

HOOK_FILE = os.path.join(DATA_DIR, "deploy_hook.txt")


def hook_url():
    """The configured deploy hook, or None if it has not been set up yet."""
    if not os.path.exists(HOOK_FILE):
        return None
    with open(HOOK_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return None


def redacted(url):
    """Enough of the URL to identify it in a log, not enough to use it."""
    if not url:
        return "(none)"
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return f"...{tail[:6]}****" if len(tail) > 6 else "...****"


def trigger(timeout=30):
    """POST to the deploy hook. Returns (ok, message)."""
    url = hook_url()
    if not url:
        return False, (f"no deploy hook configured - put the URL in {HOOK_FILE} "
                       "(Vercel > Settings > Git > Deploy Hooks)")
    try:
        r = requests.post(url, timeout=timeout)
    except requests.RequestException as e:
        return False, f"could not reach Vercel: {e.__class__.__name__}"

    if r.status_code in (200, 201):
        job = ""
        try:
            job = (r.json().get("job") or {}).get("id", "")
        except ValueError:
            pass
        return True, f"deployment triggered {redacted(url)}" + (f" job={job}" if job else "")
    if r.status_code == 404:
        return False, f"hook not found (404) {redacted(url)} - it may have been deleted"
    return False, f"Vercel returned {r.status_code} {redacted(url)}"


def main():
    ok, msg = trigger()
    print(("deploy: " if ok else "deploy FAILED: ") + msg)
    # A missing hook is a setup gap, not a run failure -- the data is already
    # ingested and committed by this point, so do not fail the whole job.
    return 0 if ok or "no deploy hook configured" in msg else 1


if __name__ == "__main__":
    raise SystemExit(main())
