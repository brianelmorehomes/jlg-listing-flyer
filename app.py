"""
JLG Listing Flyer Converter -- web edition (Render-ready)
----------------------------------------------------------
Same conversion pipeline as the desktop app, adapted to run on a host with an
ephemeral filesystem (Render's free tier wipes local disk on every restart):

- Agent phone/email default from environment variables, not a config file.
- Converted PDFs never touch disk on the server -- they're rendered to a
  temp file, immediately read back into memory, and returned to the browser
  as base64. The "download all as ZIP" endpoint is likewise stateless: the
  browser sends back the base64 PDFs it already has, the server zips them
  in memory and streams the result. Nothing about a listing sits on the
  server after the request completes.
"""
import base64
import io
import os
import tempfile
import traceback
import zipfile
from datetime import datetime

from flask import Flask, request, jsonify, render_template_string, send_file

from parser import parse_listing_pdf
from render import render_flyer

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB total upload cap

DEFAULT_AGENT_PHONE = os.environ.get("AGENT_PHONE", "")
DEFAULT_AGENT_EMAIL = os.environ.get("AGENT_EMAIL", "brian@justinlucasgroup.com")


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
  .zip-link { margin-top: 14px; display:inline-block; cursor:pointer; color:#032b42; font-weight:600; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Justin Lucas Group &mdash; Listing Flyer Converter</h1>
  </header>
  <div style="margin:-20px 0 20px;color:#666;font-size:13px;">
    Drop in one or more raw MLS listing sheet PDFs. Get back a branded, print-ready client flyer for each one.
    Nothing is stored on the server &mdash; files are converted and handed back directly to your browser.
  </div>

  <div class="card">
    <div class="settings-row">
      <div>
        <label>Your phone (shown on flyer footer)</label>
        <input type="text" id="agentPhone" placeholder="312.555.0100">
      </div>
      <div>
        <label>Your email (shown on flyer footer)</label>
        <input type="text" id="agentEmail">
      </div>
    </div>
  </div>

  <div class="card">
    <div id="dropzone">
      <p><strong>Drag &amp; drop listing sheet PDF(s) here</strong></p>
      <p class="hint">or click to browse &mdash; you can select multiple files at once</p>
      <input type="file" id="fileInput" accept="application/pdf" multiple>
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
const phoneEl = document.getElementById('agentPhone');
const emailEl = document.getElementById('agentEmail');

// Remember the agent's phone/email in this browser (no server-side storage).
phoneEl.value = localStorage.getItem('jlg_agent_phone') || '{{ default_phone }}';
emailEl.value = localStorage.getItem('jlg_agent_email') || '{{ default_email }}';
phoneEl.addEventListener('change', () => localStorage.setItem('jlg_agent_phone', phoneEl.value));
emailEl.addEventListener('change', () => localStorage.setItem('jlg_agent_email', emailEl.value));

let lastConverted = [];

dz.addEventListener('click', () => fileInput.click());
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
dz.addEventListener('drop', e => {
  e.preventDefault();
  dz.classList.remove('drag');
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => handleFiles(fileInput.files));

function b64ToBlob(b64) {
  const bytes = atob(b64);
  const arr = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
  return new Blob([arr], { type: 'application/pdf' });
}

function handleFiles(fileList) {
  if (!fileList || !fileList.length) return;
  const form = new FormData();
  for (const f of fileList) form.append('files', f);

  results.innerHTML = '';
  zipWrap.innerHTML = '';
  lastConverted = [];
  statusEl.textContent = 'Converting ' + fileList.length + ' file(s)...';

  fetch('/convert', { method: 'POST', body: form })
    .then(r => r.json())
    .then(data => {
      statusEl.textContent = 'Done. ' + data.results.filter(r => r.ok).length + ' of ' + data.results.length + ' converted.';
      data.results.forEach(r => {
        const row = document.createElement('div');
        row.className = 'result-row' + (r.ok ? '' : ' error');
        if (r.ok) {
          const blob = b64ToBlob(r.data_b64);
          const url = URL.createObjectURL(blob);
          lastConverted.push({ filename: r.filename, data_b64: r.data_b64 });
          row.innerHTML = '<span>' + r.source + ' &rarr; ' + r.address + '</span>';
          const a = document.createElement('a');
          a.href = url;
          a.download = r.filename;
          a.textContent = 'Download PDF';
          row.appendChild(a);
        } else {
          row.innerHTML = '<span>' + r.source + '</span><span>Could not parse: ' + r.error + '</span>';
        }
        results.appendChild(row);
      });
      if (lastConverted.length > 1) {
        const link = document.createElement('div');
        link.className = 'zip-link';
        link.textContent = 'Download all as ZIP';
        link.onclick = downloadZip;
        zipWrap.appendChild(link);
      }
    })
    .catch(err => { statusEl.textContent = 'Error: ' + err; });
}

function downloadZip() {
  fetch('/zip-all', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ files: lastConverted }),
  })
    .then(r => r.blob())
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'JLG_Listing_Flyers.zip';
      a.click();
    });
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(
        PAGE, default_phone=DEFAULT_AGENT_PHONE, default_email=DEFAULT_AGENT_EMAIL
    )


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/convert", methods=["POST"])
def convert():
    files = request.files.getlist("files")
    results = []
    used_names = set()

    for f in files:
        source_name = f.filename or "listing.pdf"
        tmp_path = None
        try:
            data = f.read()
            listing = parse_listing_pdf(data, source_name)
            base_name = listing.file_safe_name or "listing"
            out_name = f"{base_name}.pdf"
            n = 1
            while out_name in used_names:
                n += 1
                out_name = f"{base_name}_{n}.pdf"
            used_names.add(out_name)

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            render_flyer(listing, tmp_path)

            with open(tmp_path, "rb") as fh:
                pdf_bytes = fh.read()

            results.append({
                "ok": True,
                "source": source_name,
                "address": listing.full_address or "(address not found)",
                "filename": out_name,
                "data_b64": base64.b64encode(pdf_bytes).decode("ascii"),
            })
        except Exception as e:
            traceback.print_exc()
            results.append({"ok": False, "source": source_name, "error": str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return jsonify({"results": results})


@app.route("/zip-all", methods=["POST"])
def zip_all():
    payload = request.get_json(force=True)
    files = payload.get("files", [])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f["filename"], base64.b64decode(f["data_b64"]))
    buf.seek(0)
    zip_name = f"JLG_Listing_Flyers_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    return send_file(buf, as_attachment=True, download_name=zip_name, mimetype="application/zip")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
