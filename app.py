"""
JLG Listing Flyer Converter
---------------------------
A small local web app: drag in one or more raw MLS listing sheet PDFs
(MRED or MichRIC -- auto-detected per upload, see mls_router.py), get back
a branded, print-ready, 2-page 8.5x11 client flyer for each one.

Run with:  python3 app.py
Then open: http://localhost:5000
"""
import io
import json
import os
import traceback
import uuid
import zipfile
from datetime import datetime

from flask import Flask, request, jsonify, send_file, render_template_string

from mls_router import parse_listing_pdfs
from render import render_flyer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB total upload cap


def load_config():
    default = {
        "agent_name": "Brian Elmore",
        "agent_phone": "",
        "agent_email": "brian@justinlucasgroup.com",
        "print_safe_logo": False,
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                default.update(json.load(f))
        except Exception:
            pass
    return default


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JLG Listing Flyer Converter</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --blue: #032b42;
    --blue-dk: #021e30;
    --blue-md: #04395a;
    --slate: #f2f2f2;
    --red: #780000;
    --red-hv: #8f0000;
    --white: #ffffff;
    --text: #1a1a1a;
    --muted: #6b6b6b;
    --border: rgba(0,0,0,.08);
    --border-b: rgba(3,43,66,.12);
    --r: 4px;
    --rl: 8px;
    --d: .28s;
    --ease: cubic-bezier(.4,0,.2,1);
    --sh: 0 4px 20px rgba(0,0,0,.08);
    --sh-l: 0 12px 40px rgba(0,0,0,.14);
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: 'Plus Jakarta Sans', sans-serif; background: var(--slate); color: var(--text); }
  h1, h2 { font-family: 'DM Serif Display', serif; font-weight: 400; margin: 0; }

  header.top { background: var(--blue); padding: 22px 0; }
  .top-in { max-width: 760px; margin: 0 auto; padding: 0 24px; display: flex; align-items: center; gap: 18px; }
  .top-in img { height: 44px; width: auto; display: block; }
  .top-title { color: rgba(255,255,255,.55); font-size: .82rem; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; border-left: 1px solid rgba(255,255,255,.25); padding-left: 18px; }

  .wrap { max-width: 760px; margin: 0 auto; padding: 40px 24px 100px; }
  .hero { margin-bottom: 32px; }
  .hero h1 { font-size: 1.7rem; color: var(--blue); }
  .hero p { color: var(--muted); margin-top: 8px; font-size: .95rem; max-width: 560px; }

  .card { background: #fff; border-radius: var(--rl); box-shadow: var(--sh); padding: 28px; margin-bottom: 24px; }
  .step-num { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; background: var(--red); color: #fff; font-size: .78rem; font-weight: 700; font-family: 'Plus Jakarta Sans'; margin-right: 10px; flex-shrink: 0; }
  .card h2 { font-size: 1.15rem; color: var(--blue); font-family: 'Plus Jakarta Sans'; font-weight: 700; display: flex; align-items: center; margin-bottom: 16px; }

  #dropzone {
    border: 2px dashed var(--border-b); border-radius: var(--rl); padding: 32px 20px; text-align:center;
    color: var(--blue); cursor:pointer; transition: border-color var(--d) var(--ease), background var(--d) var(--ease);
  }
  #dropzone:hover, #dropzone.drag { border-color: var(--blue); background: var(--slate); }
  #dropzone p { margin: 6px 0; font-size: .92rem; }
  #dropzone .hint { font-size:.8rem; color:var(--muted); }
  input[type=file] { display:none; }
  .settings-row { display:flex; gap:16px; flex-wrap:wrap; }
  .settings-row label { font-size:.78rem; font-weight: 700; color: var(--blue); text-transform: uppercase; letter-spacing: .03em; display:block; margin-bottom:6px; }
  .settings-row input[type=text] {
    padding:11px 13px; border:1.5px solid var(--border-b); border-radius: var(--r); font-size:.92rem; font-family: inherit; width:220px;
  }
  .settings-row input[type=text]:focus { outline: none; border-color: var(--blue); }
  input[type=checkbox] { accent-color: var(--red); }
  button.primary {
    display: inline-flex; align-items: center; gap: 10px; background: var(--red); color: #fff; border: none;
    padding: 13px 24px; border-radius: var(--r); font-family: inherit; font-size:.88rem; font-weight: 700;
    letter-spacing: .01em; cursor:pointer; margin-top:14px; transition: background var(--d) var(--ease);
  }
  button.primary:hover { background: var(--red-hv); }
  button.primary:disabled { background:#c9c9c9; cursor:not-allowed; }
  #results { margin-top: 10px; }
  .result-row {
    display:flex; justify-content:space-between; align-items:center;
    padding:10px 14px; border-bottom:1px solid var(--border); font-size:.88rem;
  }
  .result-row:last-child { border-bottom:none; }
  .result-row.error { color: var(--red); }
  .result-row a { color: var(--blue); font-weight:700; text-decoration:none; }
  .result-row a:hover { text-decoration:underline; }
  #status { font-size:.85rem; color:var(--muted); margin-top:10px; }
  .zip-link { margin-top: 14px; display:inline-block; color: var(--blue); font-weight: 700; font-size: .85rem; }
  #stagedList { margin-top:14px; }
  .staged-row {
    display:flex; justify-content:space-between; align-items:center;
    padding:8px 0; border-bottom:1px solid var(--border); font-size:.85rem; color: var(--text);
  }
  .staged-row:last-child { border-bottom:none; }
  .staged-row .remove { color: var(--red); cursor:pointer; font-size:.78rem; margin-left:10px; }
  .staged-row .remove:hover { text-decoration:underline; }

  .build-credit { text-align: center; margin-top: 32px; padding-top: 20px; border-top: 1px solid var(--border); font-size: .74rem; color: var(--muted); }

  @media (max-width: 640px) {
    .top-in { padding: 0 16px; gap: 12px; }
    .top-in img { height: 36px; }
    .top-title { font-size: .7rem; padding-left: 12px; }
    .wrap { padding: 24px 16px 64px; }
    .hero h1 { font-size: 1.4rem; }
    .card { padding: 18px; border-radius: var(--r); }
    .settings-row { flex-direction: column; gap: 14px; }
    .settings-row input[type=text] { width: 100%; font-size: 16px; }
    button.primary { width: 100%; justify-content: center; }
  }
</style>
</head>
<body>

<header class="top">
  <div class="top-in">
    <img src="/static/logo/JLG-COMBO-BLUE.png" alt="Justin Lucas Group">
    <span class="top-title">Internal Tool</span>
  </div>
</header>

<div class="wrap">
  <div class="hero">
    <h1>Listing Flyer Converter</h1>
    <p>Drop in one or more raw MLS listing sheet PDFs. Get back a branded, print-ready client flyer for each one.</p>
  </div>

  <div class="card">
    <h2><span class="step-num">1</span>Agent details</h2>
    <div class="settings-row">
      <div>
        <label>Prepared for / agent name (shown on flyer)</label>
        <input type="text" id="agentName" value="{{ cfg.agent_name }}" placeholder="Brian Elmore">
      </div>
      <div>
        <label>Phone (shown on flyer footer)</label>
        <input type="text" id="agentPhone" value="{{ cfg.agent_phone }}" placeholder="312.555.0100">
      </div>
      <div>
        <label>Email (shown on flyer footer)</label>
        <input type="text" id="agentEmail" value="{{ cfg.agent_email }}">
      </div>
    </div>
    <div style="font-size:.78rem;color:var(--muted);margin-top:10px;">
      Converting for someone else on the team? Just change the name above before converting &mdash; e.g. Justin, Eric, or Camille's own listings.
    </div>
    <label style="display:flex;align-items:center;gap:7px;margin-top:16px;font-size:.82rem;color:var(--text);cursor:pointer;">
      <input type="checkbox" id="printSafeLogo" {{ 'checked' if cfg.print_safe_logo else '' }} style="margin:0;">
      Print-safe logo (black &amp; white)
    </label>
    <div style="font-size:.78rem;color:var(--muted);margin-top:4px;">
      Some printers render our brand red as near-black no matter the print quality setting &mdash; that's a printer issue, not a PDF issue. Check this to use an all-black version of the logo instead (this is @properties' own approved black-and-white fallback, not a workaround).
    </div>
  </div>

  <div class="card">
    <h2><span class="step-num">2</span>Upload listing sheets</h2>
    <div id="dropzone">
      <p><strong>Drag &amp; drop listing sheet PDF(s) here</strong></p>
      <p class="hint">or click to browse &mdash; you can select multiple files at once</p>
      <input type="file" id="fileInput" accept="application/pdf" multiple>
    </div>
    <div style="font-size:.78rem;color:var(--muted);margin-top:10px;">
      MichRIC (Michigan) listings: export the <strong>NEW MichRIC Full Detail Report</strong> format &mdash; the one with a "Property Features" grid (Exterior / Interior / Construction-Utilities columns) and a "Tax and Legal" section. The older single-column report layout isn't supported and will come back mostly blank.
    </div>
    <div id="stagedList"></div>
    <button class="primary" id="createBtn" disabled>Create Flyers</button>
    <div style="font-size:.78rem;color:var(--muted);margin-top:10px;">
      Nothing is generated until you click Create &mdash; double-check the name, phone, and email above first.
    </div>
    <div id="status"></div>
    <div id="results"></div>
    <div id="zipWrap"></div>
  </div>

  <p class="build-credit">&copy; 2026 Brian Elmore. All rights reserved. This tool may not be reproduced or redistributed without permission.</p>
</div>

<script>
const dz = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const results = document.getElementById('results');
const statusEl = document.getElementById('status');
const zipWrap = document.getElementById('zipWrap');
const stagedListEl = document.getElementById('stagedList');
const createBtn = document.getElementById('createBtn');

let stagedFiles = [];

dz.addEventListener('click', () => fileInput.click());
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
dz.addEventListener('drop', e => {
  e.preventDefault();
  dz.classList.remove('drag');
  addFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => { addFiles(fileInput.files); fileInput.value = ''; });
createBtn.addEventListener('click', () => convertStagedFiles());

function addFiles(fileList) {
  if (!fileList || !fileList.length) return;
  for (const f of fileList) {
    if (!stagedFiles.some(sf => sf.name === f.name && sf.size === f.size)) {
      stagedFiles.push(f);
    }
  }
  renderStagedList();
}

function removeFile(idx) {
  stagedFiles.splice(idx, 1);
  renderStagedList();
}

function renderStagedList() {
  stagedListEl.innerHTML = '';
  stagedFiles.forEach((f, idx) => {
    const row = document.createElement('div');
    row.className = 'staged-row';
    row.innerHTML = '<span>' + f.name + '</span><span class="remove">Remove</span>';
    row.querySelector('.remove').addEventListener('click', () => removeFile(idx));
    stagedListEl.appendChild(row);
  });
  createBtn.disabled = stagedFiles.length === 0;
}

function convertStagedFiles() {
  if (!stagedFiles.length) return;
  const form = new FormData();
  for (const f of stagedFiles) form.append('files', f);
  form.append('agent_name', document.getElementById('agentName').value);
  form.append('agent_phone', document.getElementById('agentPhone').value);
  form.append('agent_email', document.getElementById('agentEmail').value);
  form.append('print_safe_logo', document.getElementById('printSafeLogo').checked ? '1' : '');

  results.innerHTML = '';
  zipWrap.innerHTML = '';
  createBtn.disabled = true;
  statusEl.textContent = 'Converting ' + stagedFiles.length + ' file(s)...';

  fetch('/convert', { method: 'POST', body: form })
    .then(r => r.json())
    .then(data => {
      statusEl.textContent = 'Done. ' + data.results.filter(r => r.ok).length + ' of ' + data.results.length + ' converted.';
      data.results.forEach(r => {
        const row = document.createElement('div');
        row.className = 'result-row' + (r.ok ? '' : ' error');
        if (r.ok) {
          row.innerHTML = '<span>' + r.source + ' &rarr; ' + r.address + '</span>' +
            '<a href="/download/' + encodeURIComponent(r.filename) + '">Download PDF</a>';
        } else {
          row.innerHTML = '<span>' + r.source + '</span><span>Could not parse: ' + r.error + '</span>';
        }
        results.appendChild(row);
      });
      if (data.batch_id && data.results.filter(r => r.ok).length > 1) {
        zipWrap.innerHTML = '<a class="zip-link" href="/download-all/' + data.batch_id + '">Download all as ZIP</a>';
      }
      stagedFiles = [];
      renderStagedList();
    })
    .catch(err => {
      statusEl.textContent = 'Error: ' + err;
      createBtn.disabled = stagedFiles.length === 0;
    });
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE, cfg=load_config())


@app.route("/convert", methods=["POST"])
def convert():
    agent_name = request.form.get("agent_name", "").strip() or "Brian Elmore"
    agent_phone = request.form.get("agent_phone", "").strip()
    agent_email = request.form.get("agent_email", "").strip() or "brian@justinlucasgroup.com"
    print_safe_logo = bool(request.form.get("print_safe_logo", "").strip())
    save_config({
        "agent_name": agent_name,
        "agent_phone": agent_phone,
        "agent_email": agent_email,
        "print_safe_logo": print_safe_logo,
    })

    files = request.files.getlist("files")
    batch_id = uuid.uuid4().hex[:10]
    batch_dir = os.path.join(OUTPUT_DIR, batch_id)
    os.makedirs(batch_dir, exist_ok=True)

    results = []
    for f in files:
        source_name = f.filename or "listing.pdf"
        try:
            data = f.read()
            # A single uploaded PDF can be a batch export holding several
            # listings back-to-back (MRED's "Full Report" for a whole search
            # result set) -- parse_listing_pdfs splits that apart and
            # returns one Listing per property, or just the one Listing for
            # an ordinary single-listing file, so this always produces one
            # flyer per property found rather than only ever converting the
            # first listing in a multi-listing file. mls_router picks MRED
            # vs. MichRIC per upload automatically -- see mls_router.py.
            listings = parse_listing_pdfs(data, source_name)
            for listing in listings:
                try:
                    out_name = f"{listing.file_safe_name or 'listing'}.pdf"
                    out_path = os.path.join(batch_dir, out_name)
                    # avoid collisions within the same batch
                    n = 1
                    base_out_name = out_name
                    while os.path.exists(out_path):
                        n += 1
                        out_name = base_out_name.replace(".pdf", f"_{n}.pdf")
                        out_path = os.path.join(batch_dir, out_name)
                    render_flyer(
                        listing,
                        out_path,
                        agent_phone=agent_phone,
                        agent_email=agent_email,
                        agent_name=agent_name,
                        print_safe_logo=print_safe_logo,
                    )
                    results.append({
                        "ok": True,
                        "source": source_name if len(listings) == 1 else f"{source_name} — {listing.full_address or 'listing'}",
                        "address": listing.full_address or "(address not found)",
                        "filename": f"{batch_id}/{out_name}",
                    })
                except Exception as e:
                    traceback.print_exc()
                    results.append({"ok": False, "source": source_name, "error": str(e)})
        except Exception as e:
            traceback.print_exc()
            results.append({"ok": False, "source": source_name, "error": str(e)})

    return jsonify({"results": results, "batch_id": batch_id})


@app.route("/download/<path:filename>")
def download(filename):
    full_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.abspath(full_path).startswith(os.path.abspath(OUTPUT_DIR)):
        return "Invalid path", 400
    if not os.path.exists(full_path):
        return "Not found", 404
    return send_file(full_path, as_attachment=True)


@app.route("/download-all/<batch_id>")
def download_all(batch_id):
    batch_dir = os.path.join(OUTPUT_DIR, batch_id)
    if not os.path.isdir(batch_dir):
        return "Not found", 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(batch_dir):
            zf.write(os.path.join(batch_dir, fname), arcname=fname)
    buf.seek(0)
    zip_name = f"JLG_Listing_Flyers_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    return send_file(buf, as_attachment=True, download_name=zip_name, mimetype="application/zip")


if __name__ == "__main__":
    print("\n  JLG Listing Flyer Converter is running.")
    print("  Open this in your browser:  http://localhost:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
