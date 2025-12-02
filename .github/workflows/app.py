#!/usr/bin/env python3
# app.py
from flask import Flask, render_template_string, request, jsonify, send_file
import subprocess, threading, os, time
from urllib.parse import urlparse
import shlex

app = Flask(__name__)
FFMPEG_LOG = "/tmp/flask_restream_ffmpeg.log"
ffmpeg_proc = None
proc_lock = threading.Lock()

# Simple HTML template — تصميم أنيق ومبسط
TEMPLATE = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Restreamer — m3u8 → RTMPS (Facebook)</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body{background: linear-gradient(120deg,#0f172a,#0b1220); color:#e6eef8; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px;}
    .card{width:100%; max-width:900px; border-radius:18px; box-shadow: 0 10px 30px rgba(2,6,23,0.7);}
    .brand{font-weight:700; letter-spacing:0.6px;}
    label {font-weight:600}
    .log-box{background:#020617; color:#bcd1ff; font-family:monospace; white-space:pre-wrap; padding:10px; height:260px; overflow:auto; border-radius:8px;}
    .small-muted{color:#9fb0d9}
  </style>
</head>
<body>
  <div class="card p-4">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <div>
        <div class="h4 brand">🎛️ Restream — m3u8 → RTMPS</div>
        <div class="small-muted">شغّل بث مباشر إلى فيسبوك عبر رابط RTMPS</div>
      </div>
      <div>
        <span id="statusBadge" class="badge bg-success">متوقف</span>
      </div>
    </div>

    <form id="streamForm" class="row g-3 mb-3">
      <div class="col-md-12">
        <label class="form-label">رابط m3u8 (مصدر)</label>
        <input required name="source_url" id="source_url" class="form-control" placeholder="https://example.com/stream.m3u8">
      </div>

      <div class="col-md-12">
        <label class="form-label">رابط RTMPS (الوجهة — مثال فيسبوك)</label>
        <input required name="rtmps_url" id="rtmps_url" class="form-control" placeholder="rtmps://live-api-s.facebook.com:443/rtmp/STREAM_KEY">
      </div>

      <div class="col-12 d-flex gap-2">
        <button id="startBtn" type="button" class="btn btn-primary">بدء البث ▶</button>
        <button id="stopBtn" type="button" class="btn btn-danger">إيقاف ■</button>
        <button id="downloadLog" type="button" class="btn btn-outline-light ms-auto">تنزيل السجل</button>
      </div>
    </form>

    <div>
      <label class="form-label">سجل ffmpeg</label>
      <div id="log" class="log-box">جاري الاستعداد...</div>
    </div>

    <div class="mt-3 small text-muted">
      تعليمات: تأكد من أن مفتاح البث صحيح وأن مصدر الـ m3u8 يعمل. التطبيق يقوم بتشغيل عملية ffmpeg في الخلفية ويعرض السجل.
    </div>
  </div>

<script>
async function api(path, method='GET', body=null){
  const opts = {method, headers:{'Accept':'application/json'}};
  if(body){ opts.method='POST'; opts.body = JSON.stringify(body); opts.headers['Content-Type']='application/json' }
  const res = await fetch(path, opts);
  return res.json ? res.json() : res.text();
}

document.getElementById('startBtn').addEventListener('click', async ()=>{
  const source = document.getElementById('source_url').value.trim();
  const rtmps = document.getElementById('rtmps_url').value.trim();
  if(!source || !rtmps){ alert('رجاءً عبّئ الحقول'); return; }
  const resp = await api('/start', 'POST', {source_url: source, rtmps_url: rtmps});
  alert(resp.message || 'done');
  refreshStatus();
});

document.getElementById('stopBtn').addEventListener('click', async ()=>{
  const resp = await api('/stop', 'POST');
  alert(resp.message || 'stopped');
  refreshStatus();
});

document.getElementById('downloadLog').addEventListener('click', ()=>{
  window.location = '/download_log';
});

async function refreshStatus(){
  const s = await api('/status');
  const badge = document.getElementById('statusBadge');
  if(s.running){
    badge.textContent = 'قيد التشغيل';
    badge.className = 'badge bg-success';
  } else {
    badge.textContent = 'متوقف';
    badge.className = 'badge bg-secondary';
  }
}

async function tailLog(){
  const resp = await fetch('/log');
  const txt = await resp.text();
  const el = document.getElementById('log');
  el.textContent = txt || 'لا توجد سجلات بعد';
  el.scrollTop = el.scrollHeight;
}

refreshStatus();
setInterval(tailLog, 2000);
setInterval(refreshStatus, 3000);
</script>
</body>
</html>
"""

def is_valid_m3u8(url):
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and (url.lower().endswith(".m3u8") or ".m3u8?" in url.lower())
    except:
        return False

def is_valid_rtmps(url):
    try:
        p = urlparse(url)
        return p.scheme == "rtmps"
    except:
        return False

def start_ffmpeg(source_url, rtmps_url):
    # Build ffmpeg command: transcode video to H.264/AAC for FB compatibility
    # tuned for live: preset veryfast; adapt bitrate if needed
    cmd = [
        "ffmpeg",
        "-y",
        "-re",
        "-i", source_url,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-b:v", "2500k",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-f", "flv",
        rtmps_url
    ]
    # write to log file
    with open(FFMPEG_LOG, "ab") as flog:
        flog.write(("\n\n=== FFmpeg started: %s -> %s ===\n" % (source_url, rtmps_url)).encode('utf-8'))
        flog.flush()
        # start process
        proc = subprocess.Popen(cmd, stdout=flog, stderr=flog)
    return proc

@app.route("/")
def index():
    return render_template_string(TEMPLATE)

@app.route("/start", methods=["POST"])
def start():
    global ffmpeg_proc
    data = request.get_json(force=True)
    source = data.get("source_url","").strip()
    rtmps = data.get("rtmps_url","").strip()
    if not is_valid_m3u8(source):
        return jsonify({"ok": False, "message": "رابط m3u8 غير صالح (يجب أن يبدأ بـ http/https وينتهي بـ .m3u8)."}), 400
    if not is_valid_rtmps(rtmps):
        return jsonify({"ok": False, "message": "رابط RTMPS غير صالح (يجب أن يبدأ بـ rtmps://)."}), 400

    with proc_lock:
        if ffmpeg_proc and ffmpeg_proc.poll() is None:
            return jsonify({"ok": False, "message": "عملية ffmpeg تعمل بالفعل."})
        try:
            ffmpeg_proc = start_ffmpeg(source, rtmps)
            return jsonify({"ok": True, "message": "تم بدء البث. تحقق من السجل."})
        except Exception as e:
            return jsonify({"ok": False, "message": f"فشل بدء ffmpeg: {e}"}), 500

@app.route("/stop", methods=["POST"])
def stop():
    global ffmpeg_proc
    with proc_lock:
        if not ffmpeg_proc or ffmpeg_proc.poll() is not None:
            ffmpeg_proc = None
            return jsonify({"ok": True, "message": "لا توجد عملية تشغيل."})
        try:
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
            ffmpeg_proc = None
            with open(FFMPEG_LOG, "ab") as flog:
                flog.write(b"\n=== FFmpeg stopped by user ===\n")
            return jsonify({"ok": True, "message": "تم إيقاف البث."})
        except Exception as e:
            return jsonify({"ok": False, "message": f"خطأ أثناء الإيقاف: {e}"}), 500

@app.route("/status")
def status():
    global ffmpeg_proc
    running = False
    pid = None
    with proc_lock:
        if ffmpeg_proc and ffmpeg_proc.poll() is None:
            running = True
            pid = ffmpeg_proc.pid
    return jsonify({"running": running, "pid": pid})

@app.route("/log")
def log():
    if not os.path.exists(FFMPEG_LOG):
        return "لا توجد سجلات بعد."
    try:
        with open(FFMPEG_LOG, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read()
        return data
    except Exception as e:
        return f"خطأ قراءة السجل: {e}"

@app.route("/download_log")
def download_log():
    if not os.path.exists(FFMPEG_LOG):
        open(FFMPEG_LOG, "w").close()
    return send_file(FFMPEG_LOG, as_attachment=True, download_name="ffmpeg_restream.log")

if __name__ == "__main__":
    # Ensure log exists
    if not os.path.exists(FFMPEG_LOG):
        open(FFMPEG_LOG, "w").close()
    # Run flask on 0.0.0.0:6080
    app.run(host="0.0.0.0", port=6080, debug=False)
