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
<title>JLG Listing Flyer Converter</title>
<style>
  body { font-family: -apple-system, 'Work Sans', sans-serif; background:#f2f2f2; margin:0; padding:0; color:#222; }
  .wrap { max-width: 760px; margin: 0 auto; padding: 36px 24px 80px; }
  header { display:flex; align-items:center; gap:14px; margin-bottom: 28px; }
  header h1 { font-size: 19px; margin:0; color:#032b42; }
  header .sub { font-size: 12.5px; color:#666; margin-top:2px; }
  .card { background:#fff; border-radius:8px; padding:24px; margin-bottom:20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  #dropzone {
    border: 2px dashed #032b42; border-radius:8px; padding: 40px 20px; text-align:center;
    color:#032b42; cursor:pointer; transition: background 0.15s;
  }
  #dropzone.drag { background:#eef3f6; }
  #dropzone p { margin: 6px 0; }
  #dropzone .hint { font-size:12.5px; color:#888; }
  input[type=file] { display:none; }
  .settings-row { display:flex; gap:14px; flex-wrap:wrap; }
  .settings-row label { font-size:12.5px; color:#444; display:block; margin-bottom:4px; }
  .settings-row input[type=text] {
    padding:8px 10px; border:1px solid #ccc; border-radius:5px; font-size:13.5px; width:220px;
  }
  button.primary {
    background:#032b42; color:#fff; border:none; padding:10px 18px; border-radius:5px;
    font-size:13.5px; cursor:pointer; margin-top:14px;
  }
  button.primary:hover { background:#04405f; }
  #results { margin-top: 10px; }
  .result-row {
    display:flex; justify-content:space-between; align-items:center;
    padding:10px 14px; border-bottom:1px solid #eee; font-size:13.5px;
  }
  .result-row:last-child { border-bottom:none; }
  .result-row.error { color:#780000; }
  .result-row a { color:#032b42; font-weight:600; text-decoration:none; }
  .result-row a:hover { text-decoration:underline; }
  #status { font-size:13px; color:#666; margin-top:10px; }
  .zip-link { margin-top: 14px; display:inline-block; }
  .spinner { display:none; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Justin Lucas Group &mdash; Listing Flyer Converter</h1>
  </header>
  <div class="sub" style="margin:-20px 0 20px;color:#666;font-size:13px;">
    Drop in one or more raw MLS listing sheet PDFs. Get back a branded, print-ready client flyer for each one.
  </div>

  <div class="card">
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
    <div style="font-size:11.5px;color:#888;margin-top:8px;">
      Converting for someone else on the team? Just change the name above before converting &mdash; e.g. Justin, Eric, or Camille's own listings.
    </div>
    <label style="display:flex;align-items:center;gap:7px;margin-top:14px;font-size:12.5px;color:#444;cursor:pointer;">
      <input type="checkbox" id="printSafeLogo" {{ 'checked' if cfg.print_safe_logo else '' }} style="margin:0;">
      Print-safe logo (black &amp; white)
    </label>
    <div style="font-size:11.5px;color:#888;margin-top:3px;">
      Some printers render our brand red as near-black no matter the print quality setting &mdash; that's a printer issue, not a PDF issue. Check this to use an all-black version of the logo instead (this is @properties' own approved black-and-white fallback, not a workaround).
    </div>
  </div>

  <div class="card">
    <div id="dropzone">
      <p><strong>Drag &amp; drop listing sheet PDF(s) here</strong></p>
      <p class="hint">or click to browse &mdash; you can select multiple files at once</p>
      <input type="file" id="fileInput" accept="application/pdf" multiple>
    </div>
    <div style="font-size:11.5px;color:#888;margin-top:8px;">
      MichRIC (Michigan) listings: export the <strong>NEW MichRIC Full Detail Report</strong> format &mdash; the one with a "Property Features" grid (Exterior / Interior / Construction-Utilities columns) and a "Tax and Legal" section. The older single-column report layout isn't supported and will come back mostly blank.
    </div>
    <div id="status"></div>
    <div id="results"></div>
    <div id="zipWrap"></div>
  </div>
</div>

<script>
const dz = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const results = document.getElementById('results');
const statusEl = document.getElementById('status');
const zipWrap = document.getElementById('zipWrap');

dz.addEventListener('click', () => fileInput.click());
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
dz.addEventListener('drop', e => {
  e.preventDefault();
  dz.classList.remove('drag');
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => handleFiles(fileInput.files));

function handleFiles(fileList) {
  if (!fileList || !fileList.length) return;
  const form = new FormData();
  for (const f of fileList) form.append('files', f);
  form.append('agent_name', document.getElementById('agentName').value);
  form.append('agent_phone', document.getElementById('agentPhone').value);
  form.append('agent_email', document.getElementById('agentEmail').value);
  form.append('print_safe_logo', document.getElementById('printSafeLogo').checked ? '1' : '');

  results.innerHTML = '';
  zipWrap.innerHTML = '';
  statusEl.textContent = 'Converting ' + fileList.length + ' file(s)...';

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
    })
    .catch(err => { statusEl.textContent = 'Error: ' + err; });
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
