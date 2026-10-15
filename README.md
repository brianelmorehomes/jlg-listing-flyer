# JLG Listing Flyer Converter — Web Edition (Render)

Same tool as the desktop version, packaged to run as a hosted web app on
[Render](https://render.com) so anyone on the team can use it from a browser
&mdash; no install, no Claude session.

Nothing about a listing is stored on the server: PDFs are generated in memory
and handed straight back to your browser for download.

## Deploy it (one time, ~15 minutes)

### 1. Push this folder to GitHub

If you don't already have a repo for this:

```bash
cd "path/to/this/folder"
git init
git add .
git commit -m "JLG listing flyer converter"
```

Then create a new empty repository on [github.com/new](https://github.com/new)
(name it something like `jlg-listing-flyer`), and push:

```bash
git remote add origin https://github.com/<your-username>/jlg-listing-flyer.git
git branch -M main
git push -u origin main
```

### 2. Create a Render account

Go to [render.com](https://render.com) and sign up (GitHub sign-in is
fastest). Free tier is fine for this.

### 3. Create the web service

- In the Render dashboard, click **New +** &rarr; **Web Service**.
- Connect the GitHub repo you just created.
- Render should auto-detect the `render.yaml` in this folder (a "Blueprint")
  and set the environment to **Docker** automatically. If it asks you to pick
  manually: Environment = **Docker**, Plan = **Free**.
- Under **Environment Variables**, set:
  - `AGENT_NAME` &mdash; e.g. `Brian Elmore` (whoever the flyer is "prepared by" by default)
  - `AGENT_PHONE` &mdash; your phone number, e.g. `312.989.0512`
  - `AGENT_EMAIL` &mdash; e.g. `brian@justinlucasgroup.com`
  (Anyone using the tool can change all three per-session right in the
  browser before converting &mdash; handy when Camille is generating a flyer
  for Justin's or Eric's listing. This just sets the default shown when the
  page first loads.)
- Click **Create Web Service**.

First build takes 3-5 minutes (it's installing Pango/Cairo and Python
packages inside the Docker image). After that, Render gives you a URL like
`https://jlg-listing-flyer.onrender.com` &mdash; that's the app, share that
link with the team.

### 4. (Optional) Point a friendlier URL at it

If you want something like `flyers.justinlucasgroup.com` instead of the
`.onrender.com` address, add a custom domain in the Render dashboard under
this service's **Settings**, then add the CNAME record it gives you wherever
your domain's DNS is managed.

## Using it day to day

Open the URL, drag in one or more listing sheet PDFs, download the flyer(s)
that come back. That's it.

## The free-tier tradeoff

Render's free plan spins the service down after ~15 minutes with no traffic.
For occasional, once-a-day use, that just means the first load of the day
takes 30-60 seconds to wake back up; after that it's fast for the rest of
your session. If that ever becomes annoying, upgrading to Render's cheapest
paid tier keeps it always warm.

## Making changes later

Edit the files, commit, `git push` &mdash; Render automatically rebuilds and
redeploys on every push to `main`. The parsing logic lives in `parser.py`,
the branded layout in `templates/flyer.html`, and the PDF rendering in
`render.py`. See the desktop app's README for more detail on how the parser
is structured if you want to extend it to another MLS property type.

## One layout for every MLS source

MRED, MichRIC and Home Platform sheets all render through the same card-style
flyer (`templates/flyer_hp.html`). Home Platform sheets are read by the shared
`hp_core.py`; MichRIC and MRED sheets are read by their own parsers
(`parser.py`, `parser_michric.py`) and then converted by `hp_adapter.py` into
the same record, so the output layout never depends on where the sheet came
from. Only the amount of detail differs (a private-network MRED sheet simply
has fewer cards to fill).

- `mls_router.py` calls `hp_adapter.attach_hp()` for MichRIC and MRED listings.
- If the adapter ever fails, `listing.hp` stays `None` and `render.py` falls
  back to the legacy `templates/flyer.html`, so a conversion never errors out.
- To show another field on the card flyer for MichRIC/MRED, add it to
  `build_hp()` in `hp_adapter.py` (MichRIC's labelled extras live in
  `Listing.extras`, filled in `parser_michric.py`).
- Tests: `python3 tests/test_unified_flyer.py` (real sample sheets in `tests/fixtures/`).
