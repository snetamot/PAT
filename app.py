"""
Minimal local server for the Test Assembly block editor.

Run with:  python app.py
Then open: http://127.0.0.1:5000

POST /api/run                      {"name": ..., "steps": [...]}  -> {"run_id": "..."}
GET  /api/status/<run_id>                                         -> {"status", "log", ...}
POST /api/mouse-tracker/start      {"interval_seconds": 1.0}      -> {"active": true, "interval": ...}
POST /api/mouse-tracker/stop                                      -> {"active": false}
GET  /api/mouse-tracker/status                                    -> {"active", "interval", "x", "y"}
"""

import threading
import uuid

import pyautogui
from flask import Flask, jsonify, render_template, request

import runner

app = Flask(__name__)

# In-memory job store: run_id -> {"status": "running"|"passed"|"failed"|"error", "log": [...]}
JOBS = {}
JOBS_LOCK = threading.Lock()

# Mouse position tracker: prints the current (x, y) to the server console
# every `interval` seconds while active, and exposes it via /status for the
# browser to poll. Toggled on/off from the UI.
MOUSE_TRACKER = {"active": False, "interval": 1.0, "stop_event": None, "x": None, "y": None}
MOUSE_TRACKER_LOCK = threading.Lock()


def _mouse_tracker_loop(stop_event, interval):
    while not stop_event.is_set():
        x, y = pyautogui.position()
        print(f"[mouse] x={x} y={y}")
        with MOUSE_TRACKER_LOCK:
            MOUSE_TRACKER["x"] = x
            MOUSE_TRACKER["y"] = y
        stop_event.wait(interval)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def api_run():
    script = request.get_json(force=True)
    if not script or "steps" not in script:
        return jsonify({"error": "request body must be a test script with a 'steps' array"}), 400

    run_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[run_id] = {"status": "running", "log": []}

    def on_progress(index, total, line):
        with JOBS_LOCK:
            JOBS[run_id]["log"].append(line)

    def worker():
        result = runner.run_script(script, on_progress=on_progress)
        with JOBS_LOCK:
            JOBS[run_id].update(result)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"run_id": run_id})


@app.route("/api/status/<run_id>")
def api_status(run_id):
    with JOBS_LOCK:
        job = JOBS.get(run_id)
    if job is None:
        return jsonify({"error": "unknown run_id"}), 404
    return jsonify(job)


@app.route("/api/mouse-tracker/start", methods=["POST"])
def api_mouse_tracker_start():
    body = request.get_json(silent=True) or {}
    interval = float(body.get("interval_seconds", 1.0))

    with MOUSE_TRACKER_LOCK:
        if MOUSE_TRACKER["active"]:
            return jsonify({"error": "mouse tracker already running"}), 400
        stop_event = threading.Event()
        MOUSE_TRACKER["stop_event"] = stop_event
        MOUSE_TRACKER["interval"] = interval
        MOUSE_TRACKER["active"] = True
        threading.Thread(target=_mouse_tracker_loop, args=(stop_event, interval), daemon=True).start()

    return jsonify({"active": True, "interval": interval})


@app.route("/api/mouse-tracker/stop", methods=["POST"])
def api_mouse_tracker_stop():
    with MOUSE_TRACKER_LOCK:
        if MOUSE_TRACKER["active"]:
            MOUSE_TRACKER["stop_event"].set()
        MOUSE_TRACKER["active"] = False
    return jsonify({"active": False})


@app.route("/api/mouse-tracker/status")
def api_mouse_tracker_status():
    with MOUSE_TRACKER_LOCK:
        return jsonify({
            "active": MOUSE_TRACKER["active"],
            "interval": MOUSE_TRACKER["interval"],
            "x": MOUSE_TRACKER["x"],
            "y": MOUSE_TRACKER["y"],
        })


if __name__ == "__main__":
    # host 127.0.0.1 only — this executes real mouse/keyboard input on this
    # machine, it should never be exposed beyond localhost.
    app.run(host="127.0.0.1", port=5000, debug=False)
