import hashlib
import io
import os
import re
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import (
    Flask, abort, g, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)

from storage import Store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
SALT = app.secret_key

db = Store(BASE_DIR)
QPK = "q"

TEXT_MAX, OPT_MAX, NAME_MAX, COMMENT_MAX = 40, 16, 10, 60
HIDE_AFTER_REPORTS = 3
DURATIONS = {"1h": "1時間", "today": "今日中", "3d": "3日間"}
TABS = {"new": "新着", "hot": "白熱", "soon": "締切まぢか", "done": "結果発表"}

PERSONAL = [
    (re.compile(r"0\d{1,4}[-ー－\s]?\d{1,4}[-ー－\s]?\d{3,4}"), "電話番号"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "メールアドレス"),
    (re.compile(r"(line|ライン|ＬＩＮＥ)\s*(id|ＩＤ|アイディー)", re.I), "LINE ID"),
    (re.compile(r"(インスタ|insta|ig)\s*[:：@＠]", re.I), "SNSのアカウント"),
    (re.compile(r"(住所|番地|丁目)\s*[0-9０-９]"), "住所"),
]
NG_WORDS = ["死ね", "しね", "殺す", "ころす", "きもい", "キモい", "うざい", "ブス", "消えろ"]

_last = defaultdict(float)


# ---------- 小物 ----------

def now():
    return datetime.now(JST)


def new_id(t):
    """新しいほど小さい ID。Table Storage は RowKey 昇順で返すので新着順になる。"""
    return f"{10**13 - int(t.timestamp() * 1000):013d}{uuid.uuid4().hex[:5]}"


def seq_id(t):
    return f"{int(t.timestamp() * 1000):013d}{uuid.uuid4().hex[:5]}"


def hash_key(k):
    return hashlib.sha256((SALT + k).encode()).hexdigest()


def bad_text(text):
    for pat, what in PERSONAL:
        if pat.search(text):
            return f"{what}は書けません。"
    low = text.lower()
    if any(w in low for w in NG_WORDS):
        return "その言葉は使えません。"
    return None


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].split(":")[0].strip() or request.remote_addr or "?"


def too_fast(kind, seconds):
    k = (kind, client_ip())
    t = time.time()
    if t - _last[k] < seconds:
        return True
    _last[k] = t
    return False


def closes_at(dur, t):
    if dur == "1h":
        return t + timedelta(hours=1)
    if dur == "3d":
        return t + timedelta(days=3)
    end = t.replace(hour=23, minute=59, second=59, microsecond=0)
    return max(end, t + timedelta(hours=1))


def left_label(closes, t):
    s = (closes - t).total_seconds()
    if s <= 0:
        return "締切"
    if s < 3600:
        return f"あと{max(1, int(s // 60))}分"
    if s < 86400:
        return f"あと{int(s // 3600)}時間"
    return f"あと{int(s // 86400)}日"


def shrink(fs):
    """長い辺1080pxに縮めて、EXIF（位置情報）を消す。"""
    from PIL import Image, ImageOps

    try:
        img = ImageOps.exif_transpose(Image.open(fs.stream)).convert("RGB")
    except Exception:
        return None
    img.thumbnail((1080, 1080))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)
    return out.getvalue()


def wants_json():
    return request.headers.get("Accept", "").startswith("application/json")


# ---------- 投票した人の目印（ログインなし） ----------

@app.before_request
def voter():
    vid = request.cookies.get("vid", "")
    g.new_vid = not re.fullmatch(r"[0-9a-f]{32}", vid)
    g.vid = uuid.uuid4().hex if g.new_vid else vid


@app.after_request
def set_voter(resp):
    if getattr(g, "new_vid", False):
        resp.set_cookie("vid", g.vid, max_age=60 * 60 * 24 * 365, httponly=True,
                        samesite="Lax", secure=request.is_secure)
    return resp


# ---------- データを画面用に整える ----------

def decorate(q, mine, t):
    ca, cb = int(q.get("ca", 0)), int(q.get("cb", 0))
    total = ca + cb
    closes = datetime.fromisoformat(q["closes"])
    q["id"] = q["RowKey"]
    q["ca"], q["cb"], q["total"] = ca, cb, total
    q["pa"] = round(ca * 100 / total) if total else 50
    q["pb"] = 100 - q["pa"] if total else 50
    q["closed"] = closes <= t
    q["left"] = left_label(closes, t)
    q["mine"] = mine.get(q["id"], {}).get("side")
    q["switched"] = bool(mine.get(q["id"], {}).get("switched"))
    q["show"] = bool(q["mine"]) or q["closed"]
    q["contest"] = min(ca, cb) * 2
    if q["mine"] and total:
        my, other = (ca, cb) if q["mine"] == "a" else (cb, ca)
        q["stance"] = "少数派" if my < other else ("多数派" if my > other else "まっぷたつ")
    else:
        q["stance"] = None
    if total == 0:
        q["verdict"] = "投票なし"
    elif abs(ca - cb) <= max(1, total * 0.04):
        q["verdict"] = "引き分け" if ca == cb else "大接戦"
    else:
        q["verdict"] = f"{q['a'] if ca > cb else q['b']}派の勝ち"
    return q


def my_votes():
    return {v["RowKey"]: v for v in db.votes.query(g.vid)}


def all_questions(mine, t):
    qs = [q for q in db.questions.query(QPK) if int(q.get("reports", 0)) < HIDE_AFTER_REPORTS]
    return [decorate(q, mine, t) for q in qs]


TYPES = [
    (15, "王道派", "だいたいみんなと同じ"),
    (35, "バランス派", "ときどき少数派"),
    (55, "気まぐれ派", "読めない"),
    (101, "ひねくれ派", "みんなと逆にいきがち"),
]


def my_type(qs):
    counted = [q for q in qs if q["mine"] and q["total"] >= 3 and q["ca"] != q["cb"]]
    if len(counted) < 3:
        return {"ready": False, "need": 3 - len(counted)}
    minority = sum(1 for q in counted if q["stance"] == "少数派")
    rate = round(minority * 100 / len(counted))
    for limit, name, desc in TYPES:
        if rate < limit:
            return {"ready": True, "rate": rate, "n": len(counted), "name": name, "desc": desc}


# ---------- 画面 ----------

@app.context_processor
def inject():
    return {"durations": DURATIONS, "tabs": TABS, "logo": db.images.asset("assets/dotchi-mark.svg")}


@app.get("/")
def index():
    t = now()
    tab = request.args.get("tab", "new")
    if tab not in TABS:
        tab = "new"
    qs = all_questions(my_votes(), t)
    opened = [q for q in qs if not q["closed"]]
    if tab == "new":
        items = sorted(opened, key=lambda q: q["id"])
    elif tab == "hot":
        items = sorted([q for q in opened if q["contest"] > 0], key=lambda q: (-q["contest"], abs(q["ca"] - q["cb"])))
    elif tab == "soon":
        items = sorted(opened, key=lambda q: q["closes"])
    else:
        items = sorted([q for q in qs if q["closed"]], key=lambda q: q["closes"], reverse=True)
    return render_template("index.html", items=items[:60], tab=tab, me=my_type(qs),
                           voted=sum(1 for q in qs if q["mine"]))


@app.get("/q/<qid>")
def detail(qid):
    t = now()
    q = db.questions.get(QPK, qid)
    if not q or int(q.get("reports", 0)) >= HIDE_AFTER_REPORTS:
        abort(404)
    v = db.votes.get(g.vid, qid)
    q = decorate(q, {qid: v} if v else {}, t)
    comments = sorted(db.comments.query(qid), key=lambda c: c["RowKey"])
    comments = [c for c in comments if int(c.get("reports", 0)) < HIDE_AFTER_REPORTS]
    side = {"a": [c for c in comments if c["side"] == "a"], "b": [c for c in comments if c["side"] == "b"]}
    return render_template("detail.html", q=q, side=side)


@app.get("/about")
def about():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "about.html")


@app.get("/local-images/<name>")
def local_image(name):
    return send_from_directory(os.path.join(BASE_DIR, "data", "images"), name)


# ---------- 書き込み ----------

def fail(msg, code=400):
    if wants_json():
        return jsonify(error=msg), code
    return redirect(url_for("index", e=msg))


@app.post("/new")
def create():
    f = request.form
    text = (f.get("text") or "").strip()
    a = (f.get("a") or "").strip()
    b = (f.get("b") or "").strip()
    name = (f.get("name") or "").strip()[:NAME_MAX]
    key = (f.get("key") or "").strip()[:20]
    dur = f.get("dur") if f.get("dur") in DURATIONS else "today"

    if not text or not a or not b:
        return fail("お題とA・Bを入れてください。")
    if len(text) > TEXT_MAX or len(a) > OPT_MAX or len(b) > OPT_MAX:
        return fail(f"お題は{TEXT_MAX}字、選択肢は{OPT_MAX}字までです。")
    if a == b:
        return fail("AとBが同じです。")
    for s in (text, a, b, name):
        msg = bad_text(s)
        if msg:
            return fail(msg)
    if too_fast("new", 30):
        return fail("つくるのは30秒に1回までです。", 429)

    imgs = {}
    for k in ("img_a", "img_b"):
        fs = request.files.get(k)
        if fs and fs.filename:
            data = shrink(fs)
            if data is None:
                return fail("その画像は使えません（jpg・png・webp）。")
            imgs[k] = db.images.put(data)

    t = now()
    q = {
        "PartitionKey": QPK, "RowKey": new_id(t),
        "text": text, "a": a, "b": b, "name": name,
        "img_a": imgs.get("img_a", ""), "img_b": imgs.get("img_b", ""),
        "ca": 0, "cb": 0, "switches": 0, "ncom": 0, "reports": 0,
        "key": hash_key(key) if key else "",
        "created": t.isoformat(), "closes": closes_at(dur, t).isoformat(),
    }
    db.questions.insert(q)
    if wants_json():
        return jsonify(ok=True, id=q["RowKey"])
    return redirect(url_for("detail", qid=q["RowKey"]))


def result(q, v):
    q = decorate(q, {q["RowKey"]: v} if v else {}, now())
    keys = ["id", "ca", "cb", "total", "pa", "pb", "mine", "switched", "stance", "switches"]
    return jsonify({k: q.get(k) for k in keys})


@app.post("/q/<qid>/vote")
def vote(qid):
    side = request.form.get("side")
    if side not in ("a", "b"):
        abort(400)
    q = db.questions.get(QPK, qid)
    if not q:
        abort(404)
    if datetime.fromisoformat(q["closes"]) <= now():
        return jsonify(error="もう締め切られています。"), 400
    v = db.votes.get(g.vid, qid)
    col = "ca" if side == "a" else "cb"
    if not v:
        v = {"PartitionKey": g.vid, "RowKey": qid, "side": side, "switched": False,
             "at": now().isoformat()}
        db.votes.insert(v)
        q = db.questions.bump(QPK, qid, {col: 1}) or q
    elif v["side"] != side:
        if v.get("switched"):
            return jsonify(error="寝返りは1回までです。"), 400
        old = "ca" if v["side"] == "a" else "cb"
        v.update(side=side, switched=True)
        db.votes.upsert(v)
        q = db.questions.bump(QPK, qid, {old: -1, col: 1, "switches": 1}) or q
    return result(q, v)


@app.post("/q/<qid>/comment")
def comment(qid):
    q = db.questions.get(QPK, qid)
    if not q:
        abort(404)
    v = db.votes.get(g.vid, qid)
    if not v:
        return jsonify(error="投票するとコメントできます。"), 403
    body = (request.form.get("body") or "").strip()
    if not body or len(body) > COMMENT_MAX:
        return jsonify(error=f"{COMMENT_MAX}字までです。"), 400
    msg = bad_text(body)
    if msg:
        return jsonify(error=msg), 400
    if too_fast("comment", 8):
        return jsonify(error="ちょっと待ってからもう一回。"), 429
    t = now()
    c = {"PartitionKey": qid, "RowKey": seq_id(t), "side": v["side"], "body": body,
         "created": t.isoformat(), "reports": 0}
    db.comments.insert(c)
    db.questions.bump(QPK, qid, {"ncom": 1})
    return jsonify(ok=True, side=c["side"], body=body)


@app.post("/q/<qid>/report")
def report(qid):
    q = db.questions.bump(QPK, qid, {"reports": 1})
    if not q:
        abort(404)
    return jsonify(ok=True)


@app.post("/q/<qid>/delete")
def delete(qid):
    q = db.questions.get(QPK, qid)
    if not q:
        abort(404)
    key = (request.form.get("key") or "").strip()
    if not q.get("key") or not key or hash_key(key) != q["key"]:
        return jsonify(error="削除キーがちがいます。"), 403
    for c in db.comments.query(qid):
        db.comments.delete(qid, c["RowKey"])
    db.images.delete(q.get("img_a"))
    db.images.delete(q.get("img_b"))
    db.questions.delete(QPK, qid)
    return jsonify(ok=True)


@app.errorhandler(413)
def too_large(_):
    return fail("画像が大きすぎます（合わせて12MBまで）。", 413)


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True, port=5050)
