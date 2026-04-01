import socket
import threading
import time
from flask import Flask, Response, render_template_string, request, jsonify
from flask_httpauth import HTTPBasicAuth
import struct

# --- Config ---
USERS = {"web": "stream"}
AUDIO_TCP_PORT = 6002
WEB_PORT = 6003
SAMPLE_RATE = 16000

app = Flask(__name__)
auth = HTTPBasicAuth()

# Globals
audio_buffer = bytearray()
last_photo = None
buffer_lock = threading.Lock()

# Stats for Debug
stats = {
    "audio_received_bytes": 0,
    "photos_received_count": 0,
    "last_rpi_ping": 0,
    "start_time": time.time()
}

@auth.verify_password
def verify(username, password):
    if USERS.get(username) == password:
        return username

# --- 1. Getting data from RASPBERRY PI ---

def receiver_thread():
    global audio_buffer
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('0.0.0.0', AUDIO_TCP_PORT))
            s.listen(5)
            print(f"[*] TCP SERVER: Waiting for RPi on port {AUDIO_TCP_PORT}...")
        except Exception as e:
            print(f"[!] START ERROR: {e}")
            return

        while True:
            conn, addr = s.accept()
            print(f"[*] RPi Connected: {addr}")
            try:
                while True:
                    data = conn.recv(4096)
                    if not data: break

                    stats["audio_received_bytes"] += len(data)
                    stats["last_rpi_ping"] = time.time()

                    with buffer_lock:
                        audio_buffer += data
                        # Buffer max 5 seconds (prevent infinity lag)
                        if len(audio_buffer) > 160000:
                            audio_buffer = audio_buffer[-160000:]
            except Exception as e:
                print(f"[!] Connection interrupted: {e}")
            finally:
                conn.close()

@app.route('/upload_photo', methods=['POST'])
@auth.login_required
def upload_photo():
    global last_photo
    file = request.files.get('photo')
    if file:
        last_photo = file.read()
        stats["photos_received_count"] += 1
        stats["last_rpi_ping"] = time.time()
        return "OK", 200
    return "No file", 400

# --- 2. STATS FOR DASHBOARD ---

@app.route('/stats')
@auth.login_required
def get_stats():
    is_online = (time.time() - stats["last_rpi_ping"]) < 15
    return jsonify({
        "rpi_status": "ONLINE" if is_online else "OFFLINE",
        "audio_mb": round(stats["audio_received_bytes"] / (1024 * 1024), 2),
        "photos": stats["photos_received_count"],
        "buffer_kb": len(audio_buffer) // 1024,
        "uptime_sec": int(time.time() - stats["start_time"])
    })

@app.route('/last_photo.jpg')
@auth.login_required
def get_last_photo():
    if last_photo:
        return Response(last_photo, mimetype='image/jpeg')
    return Response(b'', mimetype='image/jpeg')

# --- 3. DASHBOARD with DEBUG PANEL ---

@app.route('/')
@auth.login_required
def index():
    return render_template_string('''
        <!DOCTYPE html>
        <html>
            <head>
                <title>SecCam Admin Panel</title>
                <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
                <style>
                    body { font-family: -apple-system, sans-serif; background: #000; color: white; text-align: center; padding: 15px; }
                    .card { background: #1a1a1a; display: inline-block; padding: 20px; border-radius: 20px; width: 100%; max-width: 500px; box-sizing: border-box; }
                    #camera-stream { width: 100%; border-radius: 12px; border: 1px solid #333; margin-bottom: 15px; background: #111; }
                    .btn-stream { background: #00ff99; color: black; border: none; padding: 18px; border-radius: 12px; cursor: pointer; font-weight: bold; width: 100%; font-size: 1.1em; }
                    .active { background: #e86000 !important; color: white; }

                    /* Debug Panel */
                    #debug-panel { margin-top: 20px; padding: 12px; background: #0a0a0a; border-radius: 10px; text-align: left; font-family: monospace; font-size: 11px; border: 1px solid #222; }
                    .stat-line { margin-bottom: 4px; display: flex; justify-content: space-between; }
                    .log-box { color: #888; height: 50px; overflow-y: auto; border-top: 1px solid #222; margin-top: 8px; padding-top: 5px; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h3 style="margin:0 0 15px 0; font-weight:300;">SecCam <span style="color:#00ff99">Live</span></h3>

                    <img id="camera-stream" src="/last_photo.jpg" alt="Waiting for RPi...">

                    <button class="btn-stream" onclick="startAudio()">
                        <span id="btn-txt">Run Audio Stream</span>
                    </button>

                    <div id="debug-panel">
                        <div class="stat-line"><span>RPi Status:</span> <b id="st-rpi">-</b></div>
                        <div class="stat-line"><span>Server Audio In:</span> <b id="st-audio">-</b></div>
                        <div class="stat-line"><span>iPhone Download:</span> <b id="st-down">0 KB</b></div>
                        <div class="stat-line"><span>Audio Context:</span> <b id="st-ctx">OFF</b></div>
                        <div class="log-box" id="log-box">Log: Waiting for interaction...</div>
                    </div>
                </div>

                <script>
                    let audioCtx = null;
                    let nextStartTime = 0;
                    let bytesDown = 0;
                    const BUFFER_DELAY = 0.6; // buffer delay in seconds to prevent under-run

                    function addLog(m) {
                        const lb = document.getElementById('log-box');
                        lb.innerHTML = "> " + m + "<br>" + lb.innerHTML;
                    }

                    // Refresh obrazu
                    setInterval(() => {
                        document.getElementById('camera-stream').src = '/last_photo.jpg?t=' + Date.now();
                    }, 5000);

                    // Refresh štatistík
                    async function refreshStats() {
                        try {
                            const r = await fetch('/stats');
                            const s = await r.json();
                            document.getElementById('st-rpi').innerText = s.rpi_status;
                            document.getElementById('st-rpi').style.color = s.rpi_status === 'ONLINE' ? '#00ff99' : '#ff4444';
                            document.getElementById('st-audio').innerText = s.audio_mb + " MB";
                        } catch(e) {}
                    }
                    setInterval(refreshStats, 2000);

                    async function startAudio() {
                        addLog("Initializing Audio...");
                        audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

                        if (audioCtx.state === 'suspended') {
                            addLog("Resuming iOS Context...");
                            await audioCtx.resume();
                        }

                        document.getElementById('st-ctx').innerText = audioCtx.state;
                        document.getElementById('btn-txt').innerText = "Connecting...";

                        try {
                            const response = await fetch("/audio_feed?t=" + Date.now());
                            const reader = response.body.getReader();
                            addLog("Stream opened.");

                            document.querySelector('.btn-stream').classList.add('active');
                            document.getElementById('btn-txt').innerText = "Audio Stream Active";

                            let isFirst = true;
                            while (true) {
                                const { value, done } = await reader.read();
                                if (done) break;

                                bytesDown += value.length;
                                document.getElementById('st-down').innerText = (bytesDown/1024).toFixed(1) + " KB";

                                let chunk = value;
                                if (isFirst && value.length > 44) {
                                    chunk = value.slice(44);
                                    isFirst = false;
                                }
                                schedulePlay(chunk);
                            }
                        } catch (e) { addLog("Error: " + e); }
                    }

                    function schedulePlay(data) {
                        const int16 = new Int16Array(data.buffer, data.byteOffset, data.byteLength / 2);
                        const f32 = new Float32Array(int16.length);
                        for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768.0;

                        const buffer = audioCtx.createBuffer(1, f32.length, 16000);
                        buffer.getChannelData(0).set(f32);
                        const source = audioCtx.createBufferSource();
                        source.buffer = buffer;
                        source.connect(audioCtx.destination);

                        const now = audioCtx.currentTime;
                        if (nextStartTime < now) nextStartTime = now + BUFFER_DELAY;

                        source.start(nextStartTime);
                        nextStartTime += buffer.duration;
                    }
                </script>
            </body>
        </html>
    ''')

@app.route('/audio_feed')
@auth.login_required
def audio_feed():
    def stream_generator():
        global audio_buffer
        # Header
        header = b'RIFF' + struct.pack('<I', 0x7FFFFFFF) + b'WAVEfmt '
        header += struct.pack('<IHHIIHH', 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
        header += b'data' + struct.pack('<I', 0x7FFFFFFF)
        yield header

        with buffer_lock:
            audio_buffer = bytearray()

        while True:
            # Large chunks (8KB) for stability of slow network
            if len(audio_buffer) >= 8192:
                with buffer_lock:
                    chunk = bytes(audio_buffer[:8192])
                    del audio_buffer[:8192]
                yield chunk
            else:
                time.sleep(0.05)

    return Response(stream_generator(), mimetype='audio/x-wav')

if __name__ == '__main__':
    threading.Thread(target=receiver_thread, daemon=True).start()
    print(f"[*] Web Server is running on port {WEB_PORT}")
    app.run(host='0.0.0.0', port=WEB_PORT, debug=False, threaded=True)