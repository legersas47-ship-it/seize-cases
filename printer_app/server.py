"""Serveur de reception des photos + file d'impression + panneau admin local.

- port public (expose via tunnel) : /api/upload, /api/status
- port admin (127.0.0.1 uniquement) : panneau de controle http://127.0.0.1:8766
"""
from __future__ import annotations

import io
import json
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, render_template_string, redirect
from PIL import Image

import effects
import printer

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
for sub in ("queue", "pending", "archive", "rejected"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)
CFG_PATH = ROOT / "config.json"
STATE_PATH = DATA / "state.json"
Image.MAX_IMAGE_PIXELS = 60_000_000

lock = threading.RLock()


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(p: Path, obj):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


CFG = load_json(CFG_PATH, {})
STATE = load_json(STATE_PATH, {"next_cell": 0, "sheet": 1, "paused": False, "printed_total": 0,
                               "message": "", "last": []})


def persist():
    save_json(STATE_PATH, STATE)


def jobs(folder: str) -> list[Path]:
    return sorted((DATA / folder).glob("*.json"), key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------- worker
def process(meta_path: Path):
    meta = load_json(meta_path, {})
    photo_path = meta_path.with_suffix(".jpg")
    geo = printer.geometry(CFG["printer"])
    with lock:
        cell = STATE["next_cell"]
        sheet = STATE["sheet"]
    l, t, r, b = geo.cell_rect(cell)
    # rendu a la resolution choisie puis mise a l'echelle par GDI dans la case
    scale = CFG.get("dpi", 300) / geo.dpi_x
    size = (max(50, int((r - l) * scale)), max(50, int((b - t) * scale)))
    effect = meta.get("effect") if CFG.get("allow_visitor_effect", True) else None
    effect = effect or CFG.get("default_effect", "comics")
    with Image.open(photo_path) as ph:
        out, used = effects.render(ph, size, effect, CFG.get("inks", "CMYK"), CFG.get("eco", True), CFG.get("dpi", 300))
    mono = CFG.get("inks", "CMYK").upper() == "K"
    printer.print_in_cell(CFG["printer"], out, cell, f"photo-comics f{sheet} c{cell + 1}", mono=mono,
                          draft=CFG.get("eco", True))
    stem = f"f{sheet:03d}-c{cell + 1:02d}-{meta_path.stem}"
    out.save(DATA / "archive" / f"{stem}-rendu.png")
    photo_path.replace(DATA / "archive" / f"{stem}-original.jpg")
    meta.update({"sheet": sheet, "cell": cell + 1, "effect_used": used, "printed_at": time.time()})
    save_json(DATA / "archive" / f"{stem}.json", meta)
    meta_path.unlink(missing_ok=True)
    with lock:
        STATE["printed_total"] += 1
        STATE["last"] = ([stem] + STATE.get("last", []))[:16]
        STATE["next_cell"] = cell + 1
        if STATE["next_cell"] >= printer.GRID ** 2:
            STATE["next_cell"] = 0
            STATE["sheet"] += 1
            STATE["paused"] = True
            STATE["message"] = (f"Feuille {sheet} pleine ! Mets une feuille vierge puis clique 'Reprendre'.")
        persist()


def worker():
    while True:
        try:
            with lock:
                paused = STATE["paused"]
            q = jobs("queue")
            if paused or not q:
                time.sleep(1.5)
                continue
            # on n'envoie la photo suivante que quand l'imprimante a fini la precedente
            # (la feuille doit avoir ete rechargee) -> la case est attribuee au dernier moment
            if printer.printer_status(CFG["printer"])["spool_jobs"] > 0:
                time.sleep(2)
                continue
            process(q[0])
        except Exception as e:  # on ne laisse jamais mourir le worker
            with lock:
                STATE["message"] = f"Erreur : {e!r}"
                STATE["paused"] = True
                persist()
            time.sleep(3)


# --------------------------------------------------------------------------- public
public = Flask("public")
public.config["MAX_CONTENT_LENGTH"] = int(CFG.get("max_upload_mb", 15) * 1024 * 1024)
_last_upload: dict[str, float] = {}


@public.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def public_status():
    with lock:
        return {
            "online": True,
            "sheet": STATE["sheet"],
            "next_cell": STATE["next_cell"] + 1,
            "filled": STATE["next_cell"],
            "queue": len(jobs("queue")) + len(jobs("pending")),
            "paused": STATE["paused"],
            "effects": effects.EFFECTS,
            "visitor_effect": CFG.get("allow_visitor_effect", True),
            "inks": CFG.get("inks", "CMYK"),
        }


@public.get("/api/status")
def api_status():
    return jsonify(public_status())


@public.route("/api/upload", methods=["POST", "OPTIONS"])
def api_upload():
    if request.method == "OPTIONS":
        return ("", 204)
    who = request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"
    now = time.time()
    wait = CFG.get("min_seconds_between_uploads_per_visitor", 30)
    if now - _last_upload.get(who, 0) < wait:
        return jsonify(error=f"Doucement : une photo toutes les {wait} s."), 429
    if len(jobs("queue")) + len(jobs("pending")) >= CFG.get("max_queue", 40):
        return jsonify(error="La file d'impression est pleine, reessaie plus tard."), 503
    f = request.files.get("photo")
    if not f:
        return jsonify(error="Aucune photo recue."), 400
    try:
        raw = f.read()
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((2400, 2400))
            jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            folder = "queue" if CFG.get("auto_print", True) else "pending"
            im.save(DATA / folder / f"{jid}.jpg", quality=90)  # re-encodage : supprime les metadonnees
    except Exception:
        return jsonify(error="Fichier illisible : envoie une image (jpg, png, heic converti...)."), 400
    effect = request.form.get("effect", "")
    if effect not in effects.EFFECTS:
        effect = ""
    save_json(DATA / folder / f"{jid}.json", {"id": jid, "effect": effect, "received_at": now})
    _last_upload[who] = now
    st = public_status()
    return jsonify(ok=True, id=jid, position=st["queue"], moderated=(folder == "pending"),
                   estimated_cell=((STATE["next_cell"] + st["queue"] - 1) % 16) + 1)


# --------------------------------------------------------------------------- admin (localhost)
admin = Flask("admin")

ADMIN_HTML = """<!doctype html><html lang=fr><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Imprimante comics - admin</title>
<meta http-equiv=refresh content=6>
<style>body{font:15px system-ui;margin:16px;max-width:900px;background:#fffbea;color:#111}
h1{font:900 26px Impact,system-ui;letter-spacing:1px}fieldset{border:3px solid #111;margin:12px 0;padding:10px}
.grid{display:grid;grid-template-columns:repeat(4,60px);gap:4px}.c{height:80px;border:2px solid #111;display:flex;
align-items:center;justify-content:center;font-weight:800}.f{background:#111;color:#ff0}.n{background:#f0c}
button{font-weight:700;border:2px solid #111;background:#ff0;padding:6px 10px;margin:2px;cursor:pointer}
.msg{background:#f0c;color:#fff;padding:8px;font-weight:700}img{height:90px;border:1px solid #111;margin:2px}
form{display:inline}</style>
<h1>IMPRIMANTE COMICS · panneau</h1>
{% if s.message %}<p class=msg>{{s.message}} <form method=post action=/clearmsg><button>ok</button></form></p>{% endif %}
<fieldset><legend>Feuille {{s.sheet}} — prochaine case : {{s.next_cell+1}}/16 — {{'EN PAUSE' if s.paused else 'EN MARCHE'}}</legend>
<div class=grid>{% for i in range(16) %}<form method=post action=/setcell><input type=hidden name=cell value={{i}}>
<button class="c {{'f' if i < s.next_cell else ('n' if i == s.next_cell else '')}}" title="Definir comme prochaine case">{{i+1}}</button></form>{% endfor %}</div>
<p>Cliquer une case = la definir comme prochaine case.</p>
<form method=post action=/toggle><button>{{'Reprendre' if s.paused else 'Pause'}}</button></form>
<form method=post action=/newsheet><button>Nouvelle feuille (case 1)</button></form>
<form method=post action=/calibrate><button>Imprimer la grille de calibration</button></form>
<p>Imprimante : {{', '.join(ps.status)}} · taches dans le spouleur : {{ps.spool_jobs}} · file : {{q}} · imprimees : {{s.printed_total}}</p>
</fieldset>
<fieldset><legend>Reglages</legend><form method=post action=/settings>
Encres disponibles : {% for ch,nm in [('C','cyan'),('M','magenta'),('Y','jaune'),('K','noir')] %}
<label><input type=checkbox name=ink value={{ch}} {{'checked' if ch in c.inks}}> {{nm}}</label>{% endfor %}<br>
<label><input type=checkbox name=eco {{'checked' if c.eco}}> mode eco (moins d'encre + qualite brouillon)</label><br>
<label><input type=checkbox name=auto_print {{'checked' if c.auto_print}}> impression automatique (sinon : validation ici)</label><br>
<label><input type=checkbox name=allow_visitor_effect {{'checked' if c.allow_visitor_effect}}> les visiteurs choisissent l'effet</label><br>
Effet par defaut : <select name=default_effect>{% for k,v in fx.items() %}<option value={{k}} {{'selected' if k==c.default_effect}}>{{k}} — {{v}}</option>{% endfor %}</select>
<br><button>Enregistrer</button></form></fieldset>
{% if pending %}<fieldset><legend>A valider ({{pending|length}})</legend>{% for p in pending %}
<div><img src="/file/pending/{{p}}.jpg"> <form method=post action=/approve><input type=hidden name=id value={{p}}><button>Imprimer</button></form>
<form method=post action=/reject><input type=hidden name=id value={{p}}><button>Refuser</button></form></div>{% endfor %}</fieldset>{% endif %}
<fieldset><legend>Dernieres impressions</legend>{% for st in s.last %}<img src="/file/archive/{{st}}-rendu.png" title="{{st}}">{% endfor %}</fieldset>
</html>"""


@admin.get("/")
def admin_home():
    try:
        ps = printer.printer_status(CFG["printer"])
    except Exception as e:
        ps = {"status": [f"introuvable ({e})"], "spool_jobs": "?"}
    with lock:
        return render_template_string(ADMIN_HTML, s=STATE, c=CFG, fx=effects.EFFECTS, ps=ps,
                                      q=len(jobs("queue")), pending=[p.stem for p in jobs("pending")])


@admin.get("/file/<folder>/<name>")
def admin_file(folder, name):
    if folder not in ("pending", "archive"):
        return "", 404
    return send_from_directory(DATA / folder, name)


def _act(fn):
    with lock:
        fn()
        persist()
    return redirect("/")


@admin.post("/toggle")
def a_toggle():
    return _act(lambda: STATE.update(paused=not STATE["paused"], message=""))


@admin.post("/clearmsg")
def a_clear():
    return _act(lambda: STATE.update(message=""))


@admin.post("/setcell")
def a_setcell():
    return _act(lambda: STATE.update(next_cell=max(0, min(15, int(request.form["cell"])))))


@admin.post("/newsheet")
def a_newsheet():
    return _act(lambda: STATE.update(next_cell=0, sheet=STATE["sheet"] + (1 if STATE["next_cell"] else 0)))


@admin.post("/calibrate")
def a_calibrate():
    threading.Thread(target=printer.print_calibration, args=(CFG["printer"],), daemon=True).start()
    return redirect("/")


@admin.post("/settings")
def a_settings():
    f = request.form
    with lock:
        CFG["inks"] = "".join(f.getlist("ink")) or "K"
        CFG["eco"] = "eco" in f
        CFG["auto_print"] = "auto_print" in f
        CFG["allow_visitor_effect"] = "allow_visitor_effect" in f
        if f.get("default_effect") in effects.EFFECTS:
            CFG["default_effect"] = f["default_effect"]
        save_json(CFG_PATH, CFG)
    return redirect("/")


def _move(src: str, dst: str):
    jid = Path(request.form["id"]).name
    for ext in (".jpg", ".json"):
        p = DATA / src / f"{jid}{ext}"
        if p.exists():
            p.replace(DATA / dst / p.name)
    return redirect("/")


@admin.post("/approve")
def a_approve():
    return _move("pending", "queue")


@admin.post("/reject")
def a_reject():
    return _move("pending", "rejected")


def start():
    from waitress import serve
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=serve, args=(admin,), kwargs={"host": "127.0.0.1", "port": CFG["admin_port"]},
                     daemon=True).start()
    serve(public, host="127.0.0.1", port=CFG["public_port"], threads=6)


if __name__ == "__main__":
    start()
