import base64
import csv
import hashlib
import hmac
import io
import logging
import os
import secrets
import time
import uuid
from datetime import datetime
from functools import wraps

import cv2
import numpy as np
from flask import (Flask, Response, abort, jsonify, redirect, render_template, request, send_file, session)
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config, db, notify
from .face_engine import FaceEngine, decode_image, encode_jpeg, face_thumbnail

log = logging.getLogger("vms")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.update(SECRET_KEY=config.secret_key(), MAX_CONTENT_LENGTH=16 * 1024 * 1024,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 12)
ticket_signer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="kiosk-ticket")

db.init()
if not config.ADMIN_PASSWORD:
    config.ADMIN_PASSWORD = secrets.token_urlsafe(9)
    log.warning("No VMS_ADMIN_PASSWORD set. Temporary admin login -> user: %s  password: %s",
                config.ADMIN_USER, config.ADMIN_PASSWORD)
engine = FaceEngine()


# ------------------------------------------------------------------ helpers
def j(**kw):
    return jsonify(kw)


def admin_required(fn):
    @wraps(fn)
    def w(*a, **k):
        if not session.get("admin"):
            return jsonify(error="auth"), 401
        return fn(*a, **k)
    return w


def read_frames(payload, key="frames", limit=5):
    out = []
    for item in (payload.get(key) or [])[:limit]:
        try:
            raw = base64.b64decode(item.split(",", 1)[-1])
        except Exception:  # noqa: BLE001
            continue
        img = decode_image(raw)
        if img is not None:
            out.append(img)
    return out


def save_snapshot(jpeg: bytes) -> str:
    name = f"{datetime.now():%Y%m%d}-{uuid.uuid4().hex}.jpg"
    (config.SNAP_DIR / name).write_bytes(jpeg)
    return name


def row(r):
    return dict(r) if r else None


def visit_json(r, hosts=None):
    d = dict(r)
    d.pop("vec", None)
    d["host_name"] = (hosts or {}).get(d.get("host_id"), "")
    d["snapshot_url"] = f"/snap/{d['snapshot']}" if d.get("snapshot") else ""
    return d


def host_map():
    with db.conn() as c:
        return {h["id"]: h["name"] for h in c.execute("SELECT id,name FROM hosts")}


def expire_pending():
    s = db.get_settings()
    cutoff = db.now() - s["approval_timeout_min"] * 60
    with db.conn() as c:
        c.execute("UPDATE visits SET status='expired' WHERE status='pending' AND created_at<?", (cutoff,))


def purge_old():
    days = db.get_settings()["retention_days"]
    if not days:
        return
    cutoff = db.now() - days * 86400
    with db.conn() as c:
        for r in c.execute("SELECT id,snapshot FROM visits WHERE created_at<?", (cutoff,)).fetchall():
            if r["snapshot"]:
                try:
                    (config.SNAP_DIR / r["snapshot"]).unlink()
                except OSError:
                    pass
        c.execute("DELETE FROM visits WHERE created_at<?", (cutoff,))


purge_old()


def new_visit(c, *, person_id, name, phone, email, purpose, host_id, status, method, score, snapshot, vec=None):
    now = db.now()
    approved = status == "approved"
    cur = c.execute(
        "INSERT INTO visits(person_id,name,phone,email,purpose,host_id,status,method,score,snapshot,token,vec,created_at,"
        "decided_at,decided_by,checkin_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (person_id, name, phone, email, purpose, host_id, status, method, score, snapshot, secrets.token_urlsafe(16),
         db.vec_to_blob(vec) if vec is not None else None, now, now if status != "pending" else None,
         "face-match" if method == "face" and status != "pending" else "", now if approved else None))
    return cur.lastrowid


# ================================================================== pages
@app.get("/")
def kiosk():
    return render_template("kiosk.html", org=db.get_settings()["org_name"])


@app.get("/admin")
def admin_page():
    return render_template("admin.html", org=db.get_settings()["org_name"], authed=bool(session.get("admin")))


@app.get("/approve/<token>")
def approve_page(token):
    with db.conn() as c:
        v = c.execute("SELECT * FROM visits WHERE token=?", (token,)).fetchone()
    if not v:
        abort(404)
    return render_template("approve.html", token=token, org=db.get_settings()["org_name"])


@app.get("/snap/<name>")
def snap(name):
    if "/" in name or ".." in name:
        abort(400)
    ok = bool(session.get("admin"))
    if not ok and request.args.get("t"):
        with db.conn() as c:
            ok = c.execute("SELECT 1 FROM visits WHERE token=? AND snapshot=?", (request.args["t"], name)).fetchone() is not None
    if not ok:
        abort(403)
    p = config.SNAP_DIR / name
    if not p.exists():
        abort(404)
    resp = send_file(p, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


# ================================================================== kiosk API
@app.get("/api/kiosk/config")
def kiosk_config():
    with db.conn() as c:
        hosts = [dict(id=h["id"], name=h["name"], unit=h["unit"]) for h in
                 c.execute("SELECT id,name,unit FROM hosts WHERE active=1 ORDER BY name")]
    s = db.get_settings()
    return j(hosts=hosts, org=s["org_name"], frames=int(s["frames_required"]))


@app.post("/api/kiosk/detect")
def kiosk_detect():
    """Cheap per-frame check that drives the live guide on the kiosk."""
    frames = read_frames(request.get_json(silent=True) or {}, limit=1)
    if not frames:
        return j(face=False, ok=False, hint="")
    fi = engine.analyse(frames[0], db.get_settings(), want_embedding=False)
    return j(face=fi.bbox is not None, ok=fi.ok, bbox=fi.bbox, hint=fi.hint(), reasons=fi.reasons)


def burst_embedding(frames, s):
    """Analyse a burst of frames -> (fused_embedding, best_frame, info) or (None, None, error_dict)."""
    infos = []
    for img in frames:
        fi = engine.analyse(img, s)
        if fi.embedding is not None:
            infos.append((img, fi))
    if not infos:
        return None, None, {"state": "no_face", "hint": "No face detected"}
    good = [(i, f) for i, f in infos if f.ok]
    need = int(s["frames_required"])
    if len(good) < min(need, len(frames)) or not good:
        worst = infos[0][1]
        return None, None, {"state": "poor_quality", "hint": worst.hint() or "Face not clear enough",
                            "reasons": worst.reasons}
    fused, n = engine.fuse([f.embedding for _, f in good])
    if fused is None or n < len(good):  # every good frame must agree: no person-swapping mid-capture
        return None, None, {"state": "unstable", "hint": "Please hold still and face the camera"}
    img, fi = max(good, key=lambda t: t[1].quality)
    return fused, (img, fi), None


@app.post("/api/kiosk/recognize")
def kiosk_recognize():
    s = db.get_settings()
    frames = read_frames(request.get_json(silent=True) or {}, limit=5)
    if not frames:
        return j(state="no_face", hint="No image received")
    fused, best, err = burst_embedding(frames, s)
    if err:
        return j(**err)
    img, fi = best
    m = engine.match(fused, s)
    log.info("recognize: %s score=%.3f second=%.3f", m["decision"], m["score"], m["second"])

    if m["decision"] == "uncertain":
        # close call: don't guess. Treat as a new visitor so a human decides.
        return j(state="unknown", score=round(m["score"], 3), uncertain=True)
    if m["decision"] == "unknown":
        return j(state="unknown", score=round(m["score"], 3))

    pid = m["person_id"]
    with db.conn() as c:
        p = c.execute("SELECT * FROM people WHERE id=?", (pid,)).fetchone()
        if p is None:
            return j(state="unknown")
        host = c.execute("SELECT name FROM hosts WHERE id=?", (p["host_id"],)).fetchone()
        snap_name = save_snapshot(face_thumbnail(img, fi.bbox))

        if p["status"] == "blocked":
            vid = new_visit(c, person_id=pid, name=p["name"], phone=p["phone"], email=p["email"], purpose="",
                            host_id=p["host_id"], status="blocked", method="face", score=m["score"], snapshot=snap_name)
            notify.blocked_attempt(dict(p), m["score"])
            return j(state="blocked", visit_id=vid)

        # same person seen again moments ago -> reuse, don't spam the log
        recent = c.execute(
            "SELECT * FROM visits WHERE person_id=? AND status='approved' AND checkout_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1", (pid,)).fetchone()
        person = {"id": pid, "name": p["name"], "kind": p["kind"], "host": host["name"] if host else "",
                  "photo": f"/api/kiosk/photo/{pid}"}
        ticket = ticket_signer.dumps({"pid": pid, "score": round(m["score"], 3), "snap": snap_name})
        if recent is not None:
            if db.now() - (recent["checkin_at"] or recent["created_at"]) < s["cooldown_seconds"]:
                return j(state="welcome", person=person, visit_id=recent["id"], repeat=True, score=round(m["score"], 3))
            return j(state="choose", person=person, ticket=ticket, score=round(m["score"], 3))

        vid = new_visit(c, person_id=pid, name=p["name"], phone=p["phone"], email=p["email"], purpose="",
                        host_id=p["host_id"], status="approved", method="face", score=m["score"], snapshot=snap_name)
    return j(state="welcome", person=person, visit_id=vid, score=round(m["score"], 3))


@app.post("/api/kiosk/action")
def kiosk_action():
    """After a 'choose' response: the recognised person checks out, or starts a fresh visit."""
    d = request.get_json(silent=True) or {}
    try:
        t = ticket_signer.loads(d.get("ticket", ""), max_age=90)
    except BadSignature:
        return jsonify(error="expired"), 400
    pid, now = t["pid"], db.now()
    with db.conn() as c:
        p = c.execute("SELECT * FROM people WHERE id=? AND status='active'", (pid,)).fetchone()
        if not p:
            return jsonify(error="not found"), 404
        if d.get("action") == "checkout":
            c.execute("UPDATE visits SET checkout_at=? WHERE person_id=? AND status='approved' AND checkout_at IS NULL",
                      (now, pid))
            return j(state="goodbye", person={"name": p["name"]})
        new_visit(c, person_id=pid, name=p["name"], phone=p["phone"], email=p["email"], purpose="",
                  host_id=p["host_id"], status="approved", method="face", score=t["score"], snapshot=t["snap"])
    return j(state="welcome", person={"name": p["name"]})


@app.get("/api/kiosk/photo/<int:pid>")
def kiosk_photo(pid):
    with db.conn() as c:
        p = c.execute("SELECT photo FROM people WHERE id=?", (pid,)).fetchone()
    if not p or not p["photo"]:
        abort(404)
    return send_file(config.SNAP_DIR / p["photo"], mimetype="image/jpeg")


@app.post("/api/kiosk/register")
def kiosk_register():
    """Unknown visitor submits details -> a pending approval request is created for the host."""
    d = request.get_json(silent=True) or {}
    s = db.get_settings()
    name = (d.get("name") or "").strip()[:80]
    if len(name) < 2:
        return jsonify(error="Please enter your name"), 400
    phone, email = (d.get("phone") or "").strip()[:30], (d.get("email") or "").strip()[:120]
    purpose = (d.get("purpose") or "").strip()[:200]
    host_id = int(d["host_id"]) if str(d.get("host_id") or "").isdigit() else None
    frames = read_frames(d, limit=5)
    if not frames:
        return jsonify(error="No photo captured"), 400
    # keep the sharpest, best-framed picture; fall back to the largest face if nothing is 'perfect'
    scored = [(engine.analyse(f, s, strict=False), f) for f in frames]
    scored = [(fi, f) for fi, f in scored if fi.bbox is not None]
    if not scored:
        return jsonify(error="We could not see your face - please try again"), 400
    fi, img = max(scored, key=lambda t: t[0].quality)
    snap_name = save_snapshot(face_thumbnail(img, fi.bbox))
    vec = engine.fuse([f.embedding for f, _ in scored if f.embedding is not None])[0]
    if vec is None:
        vec = fi.embedding
    with db.conn() as c:
        host = c.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone() if host_id else None
        vid = new_visit(c, person_id=None, name=name, phone=phone, email=email, purpose=purpose, host_id=host_id,
                        status="pending", method="manual", score=0, snapshot=snap_name, vec=vec)
        v = dict(c.execute("SELECT * FROM visits WHERE id=?", (vid,)).fetchone())
    notify.visitor_waiting(v, dict(host) if host else {}, (config.SNAP_DIR / snap_name).read_bytes())
    return j(visit_id=vid, token=v["token"], timeout_min=s["approval_timeout_min"])


@app.get("/api/kiosk/status/<int:vid>")
def kiosk_status(vid):
    expire_pending()
    with db.conn() as c:
        v = c.execute("SELECT status,token FROM visits WHERE id=?", (vid,)).fetchone()
    if not v or not hmac.compare_digest(v["token"], request.args.get("t", "")):
        abort(404)
    return j(status=v["status"])


# ================================================================== approvals (host link + admin)
def decide(c, visit_id: int, decision: str, by: str, remember: bool = False):
    v = c.execute("SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
    if not v or v["status"] != "pending":
        return None
    now = db.now()
    if decision == "approve":
        c.execute("UPDATE visits SET status='approved', decided_at=?, decided_by=?, checkin_at=? WHERE id=?",
                  (now, by, now, visit_id))
        if remember and v["vec"]:
            cur = c.execute("INSERT INTO people(name,phone,email,kind,host_id,photo,created_at) VALUES(?,?,?,?,?,?,?)",
                            (v["name"], v["phone"], v["email"], "visitor", v["host_id"], v["snapshot"], now))
            pid = cur.lastrowid
            c.execute("INSERT INTO embeddings(person_id,vec,quality,created_at) VALUES(?,?,?,?)",
                      (pid, v["vec"], 0.5, now))
            c.execute("UPDATE visits SET person_id=? WHERE id=?", (pid, visit_id))
    else:
        c.execute("UPDATE visits SET status='denied', decided_at=?, decided_by=? WHERE id=?", (now, by, visit_id))
    return v


@app.get("/api/approve/<token>")
def approve_info(token):
    expire_pending()
    with db.conn() as c:
        v = c.execute("SELECT * FROM visits WHERE token=?", (token,)).fetchone()
    if not v:
        abort(404)
    d = visit_json(v, host_map())
    d["snapshot_url"] += f"?t={token}" if d["snapshot_url"] else ""
    return jsonify({k: d[k] for k in ("id", "name", "purpose", "status", "host_name", "snapshot_url", "created_at")})


@app.post("/api/approve/<token>")
def approve_act(token):
    d = request.get_json(silent=True) or {}
    expire_pending()
    with db.conn() as c:
        v = c.execute("SELECT id FROM visits WHERE token=?", (token,)).fetchone()
        if not v:
            abort(404)
        r = decide(c, v["id"], "approve" if d.get("decision") == "approve" else "deny", "host-link",
                   bool(d.get("remember")))
    return j(ok=r is not None)


# ================================================================== admin API
@app.post("/api/admin/login")
def admin_login():
    d = request.get_json(silent=True) or {}
    ok = hmac.compare_digest(str(d.get("user", "")), config.ADMIN_USER) and \
        hmac.compare_digest(str(d.get("password", "")), config.ADMIN_PASSWORD)
    if not ok:
        time.sleep(0.8)
        return jsonify(error="Wrong username or password"), 401
    session.permanent = True
    session["admin"] = True
    return j(ok=True)


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return j(ok=True)


@app.get("/api/admin/overview")
@admin_required
def overview():
    expire_pending()
    hosts = host_map()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with db.conn() as c:
        pending = [visit_json(r, hosts) for r in
                   c.execute("SELECT * FROM visits WHERE status='pending' ORDER BY created_at")]
        recent = [visit_json(r, hosts) for r in c.execute("SELECT * FROM visits ORDER BY created_at DESC LIMIT 12")]
        q = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        stats = {
            "today": q("SELECT COUNT(*) FROM visits WHERE created_at>=? AND status IN ('approved','pending')", today),
            "inside": q("SELECT COUNT(*) FROM visits WHERE status='approved' AND checkout_at IS NULL AND checkin_at>=?",
                        db.now() - 16 * 3600),
            "auto": q("SELECT COUNT(*) FROM visits WHERE created_at>=? AND method='face' AND status='approved'", today),
            "denied": q("SELECT COUNT(*) FROM visits WHERE created_at>=? AND status IN ('denied','blocked')", today),
            "people": q("SELECT COUNT(*) FROM people"),
        }
    return j(pending=pending, recent=recent, stats=stats)


@app.post("/api/admin/visits/<int:vid>/decide")
@admin_required
def admin_decide(vid):
    d = request.get_json(silent=True) or {}
    with db.conn() as c:
        r = decide(c, vid, "approve" if d.get("decision") == "approve" else "deny", "admin", bool(d.get("remember")))
    if r is not None and d.get("remember"):
        engine.reload_gallery()
    return j(ok=r is not None)


@app.post("/api/admin/visits/<int:vid>/checkout")
@admin_required
def admin_checkout(vid):
    with db.conn() as c:
        c.execute("UPDATE visits SET checkout_at=? WHERE id=? AND checkout_at IS NULL", (db.now(), vid))
    return j(ok=True)


def _visit_query(args):
    sql, p = "SELECT * FROM visits WHERE 1=1", []
    if args.get("q"):
        sql += " AND (name LIKE ? OR purpose LIKE ? OR phone LIKE ?)"
        p += [f"%{args['q']}%"] * 3
    if args.get("status"):
        sql += " AND status=?"
        p.append(args["status"])
    if args.get("from"):
        sql += " AND created_at>=?"
        p.append(datetime.fromisoformat(args["from"]).timestamp())
    if args.get("to"):
        sql += " AND created_at<?"
        p.append(datetime.fromisoformat(args["to"]).timestamp() + 86400)
    return sql + " ORDER BY created_at DESC", p


@app.get("/api/admin/visits")
@admin_required
def admin_visits():
    sql, p = _visit_query(request.args)
    page = max(0, int(request.args.get("page", 0)))
    with db.conn() as c:
        rows = c.execute(sql + " LIMIT 50 OFFSET ?", p + [page * 50]).fetchall()
    hosts = host_map()
    return j(visits=[visit_json(r, hosts) for r in rows], page=page)


@app.get("/api/admin/visits.csv")
@admin_required
def admin_csv():
    sql, p = _visit_query(request.args)
    with db.conn() as c:
        rows = c.execute(sql, p).fetchall()
    hosts = host_map()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "name", "phone", "email", "host", "purpose", "status", "method", "match_score",
                "arrived", "checked_out", "decided_by"])
    ts = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else ""  # noqa: E731
    for r in rows:
        w.writerow([r["id"], r["name"], r["phone"], r["email"], hosts.get(r["host_id"], ""), r["purpose"],
                    r["status"], r["method"], round(r["score"] or 0, 3), ts(r["created_at"]), ts(r["checkout_at"]),
                    r["decided_by"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=visits.csv"})


# ---- people / enrolment
def person_json(c, p, hosts):
    d = dict(p)
    d["faces"] = c.execute("SELECT COUNT(*) FROM embeddings WHERE person_id=?", (p["id"],)).fetchone()[0]
    d["host_name"] = hosts.get(p["host_id"], "")
    d["photo_url"] = f"/snap/{p['photo']}" if p["photo"] else ""
    last = c.execute("SELECT MAX(created_at) FROM visits WHERE person_id=?", (p["id"],)).fetchone()[0]
    d["last_seen"] = last
    return d


@app.get("/api/admin/people")
@admin_required
def people_list():
    hosts = host_map()
    with db.conn() as c:
        rows = c.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()
        return j(people=[person_json(c, p, hosts) for p in rows])


def _enrol(frames, s):
    """Quality-gate every sample, drop outliers, return list of (embedding, quality, img, info) or an error."""
    good, problems = [], []
    for i, img in enumerate(frames):
        fi = engine.analyse(img, s, strict=False)
        if fi.embedding is None:
            problems.append(f"Photo {i + 1}: no face found")
        elif fi.reasons and set(fi.reasons) - {"turned", "tilted"}:
            problems.append(f"Photo {i + 1}: {fi.hint().lower()}")
        else:
            good.append((fi.embedding, fi.quality, img, fi))
    if len(good) >= 3:  # discard a sample that does not look like the rest (wrong person walked into frame)
        E = np.stack([g[0] for g in good])
        centre = E.mean(0)
        centre /= np.linalg.norm(centre)
        keep = [g for g, sim in zip(good, E @ centre) if sim >= 0.5]
        if len(keep) < len(good):
            problems.append("Some photos looked like a different person and were skipped")
        good = keep
    return good, problems


@app.post("/api/admin/people")
@admin_required
def people_create():
    d = request.get_json(silent=True) or {}
    s = db.get_settings()
    name = (d.get("name") or "").strip()
    if len(name) < 2:
        return jsonify(error="Name is required"), 400
    frames = read_frames(d, limit=8)
    good, problems = _enrol(frames, s)
    if len(good) < 2:
        return jsonify(error="Need at least 2 clear photos. " + "; ".join(problems)), 400
    fused = FaceEngine.fuse([g[0] for g in good])[0] if len(good) > 1 else good[0][0]
    dup = engine.match(fused if fused is not None else good[0][0], s)
    if dup["decision"] in ("match", "uncertain") and not d.get("force") and dup.get("candidate"):
        with db.conn() as c:
            o = c.execute("SELECT name FROM people WHERE id=?", (dup["candidate"],)).fetchone()
        return jsonify(error=f"This face already looks like '{o['name']}' (similarity {dup['score']:.2f}).",
                       duplicate=True), 409
    best = max(good, key=lambda g: g[1])
    photo = save_snapshot(face_thumbnail(best[2], best[3].bbox))
    host_id = int(d["host_id"]) if str(d.get("host_id") or "").isdigit() else None
    with db.conn() as c:
        cur = c.execute("INSERT INTO people(name,phone,email,kind,host_id,notes,photo,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (name, d.get("phone", ""), d.get("email", ""), d.get("kind", "visitor"), host_id,
                         d.get("notes", ""), photo, db.now()))
        pid = cur.lastrowid
        for emb, q, _, _ in good:
            c.execute("INSERT INTO embeddings(person_id,vec,quality,created_at) VALUES(?,?,?,?)",
                      (pid, db.vec_to_blob(emb), q, db.now()))
    engine.reload_gallery()
    return j(id=pid, samples=len(good), warnings=problems)


@app.post("/api/admin/people/<int:pid>/faces")
@admin_required
def people_add_faces(pid):
    d = request.get_json(silent=True) or {}
    good, problems = _enrol(read_frames(d, limit=8), db.get_settings())
    if not good:
        return jsonify(error="No usable photos. " + "; ".join(problems)), 400
    with db.conn() as c:
        if not c.execute("SELECT 1 FROM people WHERE id=?", (pid,)).fetchone():
            abort(404)
        for emb, q, _, _ in good:
            c.execute("INSERT INTO embeddings(person_id,vec,quality,created_at) VALUES(?,?,?,?)",
                      (pid, db.vec_to_blob(emb), q, db.now()))
        # cap gallery size: keep the 12 best-quality samples
        c.execute("DELETE FROM embeddings WHERE person_id=? AND id NOT IN "
                  "(SELECT id FROM embeddings WHERE person_id=? ORDER BY quality DESC LIMIT 12)", (pid, pid))
    engine.reload_gallery()
    return j(added=len(good), warnings=problems)


@app.patch("/api/admin/people/<int:pid>")
@admin_required
def people_update(pid):
    d = request.get_json(silent=True) or {}
    fields = {k: d[k] for k in ("name", "phone", "email", "kind", "status", "notes", "host_id") if k in d}
    if fields.get("status") not in (None, "active", "blocked"):
        abort(400)
    if fields:
        with db.conn() as c:
            c.execute(f"UPDATE people SET {','.join(k + '=?' for k in fields)} WHERE id=?", [*fields.values(), pid])
    return j(ok=True)


@app.delete("/api/admin/people/<int:pid>")
@admin_required
def people_delete(pid):
    with db.conn() as c:
        p = c.execute("SELECT photo FROM people WHERE id=?", (pid,)).fetchone()
        c.execute("UPDATE visits SET person_id=NULL WHERE person_id=?", (pid,))
        c.execute("DELETE FROM people WHERE id=?", (pid,))
    engine.reload_gallery()
    return j(ok=True)


# ---- hosts & settings
@app.get("/api/admin/hosts")
@admin_required
def hosts_list():
    with db.conn() as c:
        return j(hosts=[dict(r) for r in c.execute("SELECT * FROM hosts ORDER BY name COLLATE NOCASE")])


@app.post("/api/admin/hosts")
@admin_required
def hosts_save():
    d = request.get_json(silent=True) or {}
    if len((d.get("name") or "").strip()) < 2:
        return jsonify(error="Name required"), 400
    with db.conn() as c:
        if d.get("id"):
            c.execute("UPDATE hosts SET name=?,email=?,phone=?,unit=?,active=? WHERE id=?",
                      (d["name"], d.get("email", ""), d.get("phone", ""), d.get("unit", ""),
                       1 if d.get("active", True) else 0, d["id"]))
        else:
            c.execute("INSERT INTO hosts(name,email,phone,unit) VALUES(?,?,?,?)",
                      (d["name"], d.get("email", ""), d.get("phone", ""), d.get("unit", "")))
    return j(ok=True)


@app.delete("/api/admin/hosts/<int:hid>")
@admin_required
def hosts_delete(hid):
    with db.conn() as c:
        c.execute("UPDATE hosts SET active=0 WHERE id=?", (hid,))
    return j(ok=True)


@app.get("/api/admin/settings")
@admin_required
def settings_get():
    return j(settings=db.get_settings(), notify={"email": bool(config.SMTP_HOST), "webhook": bool(config.WEBHOOK_URL)},
             model=config.MODEL_PACK)


@app.post("/api/admin/settings")
@admin_required
def settings_set():
    d = request.get_json(silent=True) or {}
    clean = {}
    ranges = {"match_threshold": (0.25, 0.8), "match_margin": (0, 0.3), "min_face_px": (40, 400), "blur_min": (0, 500),
              "frames_required": (1, 4), "cooldown_seconds": (0, 3600), "approval_timeout_min": (1, 240),
              "retention_days": (0, 3650)}
    for k, (lo, hi) in ranges.items():
        if k in d:
            clean[k] = min(hi, max(lo, float(d[k]))) if isinstance(config.DEFAULT_SETTINGS[k], float) \
                else int(min(hi, max(lo, float(d[k]))))
    if "org_name" in d:
        clean["org_name"] = str(d["org_name"])[:60] or "Front Desk"
    db.set_settings(clean)
    return j(ok=True)


@app.errorhandler(413)
def too_big(_):
    return jsonify(error="Upload too large"), 413


def main():
    from waitress import serve
    log.info("Visitor Management System running on %s  (kiosk: /   admin: /admin)", config.BASE_URL)
    serve(app, host=config.HOST, port=config.PORT, threads=6)


if __name__ == "__main__":
    main()
