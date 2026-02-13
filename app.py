from flask import Flask, request, jsonify, make_response, render_template, render_template_string
import sqlite3
import json
import uuid
from datetime import datetime, timedelta
import hashlib

app = Flask(__name__)

DB_PATH = "tracker.db"
SESSION_TIMEOUT_MINUTES = 30


# ---------------- DATABASE ---------------- #

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS visitors(
        visitor_id TEXT PRIMARY KEY,
        first_seen TEXT,
        last_seen TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS devices(
        device_id TEXT PRIMARY KEY,
        visitor_id TEXT,
        fingerprint_hash TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        session_id TEXT PRIMARY KEY,
        visitor_id TEXT,
        device_id TEXT,
        track_id TEXT,
        start_time TEXT,
        last_event TEXT,
        ip TEXT,
        country TEXT,
        city TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events(
        event_id TEXT PRIMARY KEY,
        session_id TEXT,
        event_type TEXT,
        timestamp TEXT,
        data TEXT
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_track ON sessions(track_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")

    conn.commit()
    conn.close()


init_db()


# ---------------- HELPERS ---------------- #

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def now_dt():
    return datetime.utcnow()

def fingerprint_hash(data):
    base = (
        str(data.get("platform", "")) +
        str(data.get("screen", "")) +
        str(data.get("timezone", "")) +
        str(data.get("dpr", ""))
    )
    return hashlib.sha256(base.encode()).hexdigest()

def get_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0]
    return request.remote_addr

# Dummy geo resolver (replace with real API later)
def geo_lookup(ip):
    return ip, "Unknown city"


# ---------------- VISITOR ---------------- #

def ensure_visitor(visitor_id):
    conn = get_db()
    cur = conn.cursor()

    row = cur.execute("SELECT visitor_id FROM visitors WHERE visitor_id=?", (visitor_id,)).fetchone()

    if not row:
        cur.execute("INSERT INTO visitors VALUES (?, ?, ?)",
                    (visitor_id, now(), now()))
    else:
        cur.execute("UPDATE visitors SET last_seen=? WHERE visitor_id=?",
                    (now(), visitor_id))

    conn.commit()
    conn.close()


def get_or_create_device(visitor_id, fp_hash):
    conn = get_db()
    cur = conn.cursor()

    row = cur.execute("SELECT device_id FROM devices WHERE fingerprint_hash=?", (fp_hash,)).fetchone()

    if row:
        device_id = row["device_id"]
    else:
        device_id = uuid.uuid4().hex
        cur.execute("INSERT INTO devices VALUES (?, ?, ?)",
                    (device_id, visitor_id, fp_hash))

    conn.commit()
    conn.close()
    return device_id


# ---------------- SESSION ---------------- #

def get_or_create_session(visitor_id, device_id, track_id):
    conn = get_db()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT * FROM sessions
        WHERE visitor_id=? AND device_id=? AND track_id=?
        ORDER BY last_event DESC LIMIT 1
    """, (visitor_id, device_id, track_id)).fetchone()

    if row:
        last_event = datetime.strptime(row["last_event"], "%Y-%m-%d %H:%M:%S")
        if now_dt() - last_event < timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            conn.close()
            return row["session_id"]

    session_id = uuid.uuid4().hex
    ip = get_ip()
    country, city = geo_lookup(ip)

    cur.execute("""
        INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (session_id, visitor_id, device_id, track_id,
          now(), now(), ip, country, city))

    conn.commit()
    conn.close()
    return session_id



# ---------------- EVENTS ---------------- #

def save_event(event_id, session_id, event_type, data):
    conn = get_db()
    cur = conn.cursor()

    exists = cur.execute("SELECT event_id FROM events WHERE event_id=?", (event_id,)).fetchone()
    if exists:
        conn.close()
        return

    cur.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (event_id, session_id, event_type, now(), json.dumps(data)))

    cur.execute("UPDATE sessions SET last_event=? WHERE session_id=?",
                (now(), session_id))

    conn.commit()
    conn.close()
# ---------------- track id ---------------- #
@app.route("/r/<track_id>")
def track(track_id):
    visitor_id = request.cookies.get("visitor_id")
    if not visitor_id:
        visitor_id = uuid.uuid4().hex

    ensure_visitor(visitor_id)

    html = """
    <html>
    <body>
    <h2>Tracking...</h2>
    <script>
        const TRACK_ID = "{{ track_id }}";
        const queueKey = "event_queue";

        function queueEvent(ev){
            let q = JSON.parse(localStorage.getItem(queueKey) || "[]");
            q.push(ev);
            localStorage.setItem(queueKey, JSON.stringify(q));
        }

        function send(ev){
            const ok = navigator.sendBeacon(
                "/collect",
                new Blob([JSON.stringify(ev)], {type:"application/json"})
            );
            if(!ok){
                queueEvent(ev);
            }
        }

        function flushQueue(){
            let q = JSON.parse(localStorage.getItem(queueKey) || "[]");
            localStorage.removeItem(queueKey);
            q.forEach(send);
        }

        function emit(type, data){
            const ev = {
                event_id: crypto.randomUUID(),
                event_type: type,
                track_id: TRACK_ID,
                data: data
            };
            send(ev);
        }

        window.onload = function(){
            emit("basic", {
                screen: screen.width+"x"+screen.height,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                platform: navigator.platform,
                dpr: window.devicePixelRatio
            });

            flushQueue();
            document.body.innerHTML = "<h2>Tracked successfully</h2>";
        }
    </script>
    </body>
    </html>
    """

    resp = make_response(render_template_string(html, track_id=track_id))
    resp.set_cookie("visitor_id", visitor_id, max_age=31536000)
    return resp
# ---------------- COLLECTION ---------------- #

@app.route("/collect", methods=["POST"])
def collect():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "invalid"}), 400

    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    data = payload.get("data")
    track_id = payload.get("track_id")

    if not all([event_id, event_type, data, track_id]):
        return jsonify({"status": "invalid"}), 400

    visitor_id = request.cookies.get("visitor_id")
    if not visitor_id:
        return jsonify({"status": "no visitor"}), 400

    ensure_visitor(visitor_id)

    fp_hash = fingerprint_hash(data)
    device_id = get_or_create_device(visitor_id, fp_hash)
    session_id = get_or_create_session(visitor_id, device_id, track_id)

    save_event(event_id, session_id, event_type, data)

    return jsonify({"status": "ok"})


# ---------------- ANALYTICS LAYER ---------------- #

def collect_stats(track_id):
    conn = get_db()
    cur = conn.cursor()

    visitors = cur.execute("""
        SELECT COUNT(DISTINCT visitor_id) c FROM sessions WHERE track_id=?
    """, (track_id,)).fetchone()["c"]

    sessions = cur.execute("""
        SELECT COUNT(*) c FROM sessions WHERE track_id=?
    """, (track_id,)).fetchone()["c"]

    events = cur.execute("""
        SELECT COUNT(*) c FROM events
        WHERE session_id IN (
            SELECT session_id FROM sessions WHERE track_id=?
        )
    """, (track_id,)).fetchone()["c"]

    avg_duration = cur.execute("""
        SELECT AVG((julianday(last_event)-julianday(start_time))*86400) d
        FROM sessions WHERE track_id=?
    """, (track_id,)).fetchone()["d"] or 0

    bounce = cur.execute("""
        SELECT COUNT(*) c FROM sessions
        WHERE track_id=?
        AND (julianday(last_event)-julianday(start_time))*86400 < 10
    """, (track_id,)).fetchone()["c"] or 0

    active = cur.execute("""
        SELECT COUNT(*) c FROM sessions
        WHERE track_id=? AND last_event > datetime('now','-5 minutes')
    """, (track_id,)).fetchone()["c"]

    ips = cur.execute("""
        SELECT ip, COUNT(*) c FROM sessions
        WHERE track_id=?
        GROUP BY ip ORDER BY c DESC
    """, (track_id,)).fetchall()


    hours = cur.execute("""
        SELECT strftime('%H', start_time) h, COUNT(*) c
        FROM sessions WHERE track_id=?
        GROUP BY h ORDER BY h
    """, (track_id,)).fetchall()

    devices = cur.execute("""
        SELECT COUNT(DISTINCT device_id) c
        FROM sessions WHERE track_id=?
    """, (track_id,)).fetchone()["c"]

    conn.close()

    return {
        "visitors": visitors,
        "sessions": sessions,
        "events": events,
        "avg_duration": round(avg_duration, 1),
        "bounce_rate": round((bounce / sessions) * 100, 2) if sessions else 0,
        "active": active,
        "ips": ips,
        "hours": hours,
        "devices": devices
    }


# ---------------- DASHBOARD ---------------- #

@app.route("/dashboard/<track_id>")
def dashboard(track_id):

    stats = collect_stats(track_id)

    return render_template("dashboard.html", stats=stats, track_id=track_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
