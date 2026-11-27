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
    send_from_directory, session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from storage import Store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # Azure の前段から https を正しく受け取る
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("AZURE_STORAGE_CONNECTION_STRING")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=60),
)
SALT = app.secret_key

db = Store(BASE_DIR)
QPK = "q"

TEXT_MAX, OPT_MAX, NAME_MAX, COMMENT_MAX = 40, 16, 10, 20
COMMENTS_PER_PERSON = 5
HIDE_AFTER_REPORTS = 3
DURATIONS = {"1h": "1時間", "today": "今日中", "3d": "3日間"}
TABS = {"new": "新着", "hot": "白熱", "soon": "締切まぢか", "done": "結果発表"}
UPK = "u"
GRADES = ["1年", "2年", "3年", "ひみつ"]
COLORS = ["#f2502f", "#2a62e8", "#1f9d55", "#e89b00", "#8a4fd8", "#1c1b19"]

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


JP = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
SYMBOL = re.compile(r"[^\w\s\u3040-\u30ff\u3400-\u9fff]")


def looks_like_junk(text, strict=False):
    """暗号みたいな文字列・同じ文字の連打・記号だらけを弾く。"""
    t = text.strip()
    if re.search(r"(.)\1{4,}", t):
        return "同じ文字の連続は書けません。"
    if re.search(r"[A-Za-z0-9]{12,}", t):
        return "意味のない文字列は書けません。"
    if len(t) >= 4 and len(SYMBOL.findall(t)) / len(t) > 0.5:
        return "記号だけのコメントは書けません。"
    if strict and len(t) >= 6 and not JP.search(t) and t.count(" ") < 1:
        return "意味のない文字列は書けません。"
    return None


def bad_text(text, strict=False):
    junk = looks_like_junk(text, strict) if text else None
    if junk:
        return junk
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


# ---------- 投票した人の目印とログイン ----------

@app.before_request
def voter():
    vid = request.cookies.get("vid", "")
    g.new_vid = not re.fullmatch(r"[0-9a-f]{32}", vid)
    g.vid = uuid.uuid4().hex if g.new_vid else vid
    g.user = None
    uid = session.get("uid")
    if uid and request.endpoint not in ("static", "local_image"):
        g.user = db.users.get(UPK, uid)
        if g.user and g.user.get("vid") and g.user["vid"] != g.vid:
            # ログインしている人は、どの端末でも同じ投票記録を使う
            g.vid = g.user["vid"]
            g.new_vid = True
        elif not g.user:
            session.pop("uid", None)


def owner_key():
    return f"u:{g.user['RowKey']}" if g.user else f"v:{g.vid}"


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
        return {"ready": False, "need": 3 - len(counted), "have": len(counted)}
    minority = sum(1 for q in counted if q["stance"] == "少数派")
    rate = round(minority * 100 / len(counted))
    for limit, name, desc in TYPES:
        if rate < limit:
            return {"ready": True, "rate": rate, "n": len(counted), "name": name, "desc": desc, "have": 3}


# ---------- 画面 ----------

@app.context_processor
def inject():
    return {"durations": DURATIONS, "tabs": TABS, "types": TYPES, "me_user": g.get("user"),
            "grades": GRADES, "colors": COLORS,
            "logo": db.images.asset("assets/dotchi-mark.svg") or "/static/favicon.svg"}


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
    own = db.mine.get(owner_key(), qid)
    return render_template("detail.html", q=q, side=side, own=own, made=request.args.get("made"))


@app.get("/phone")
def phone():
    u = request.args.get("u", "/")
    if not u.startswith("/") or u.startswith("//"):
        u = "/"
    return render_template("phone.html", u=u)


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
    if g.user and not f.get("anon"):
        name = g.user.get("name", "")
    key = (f.get("key") or "").strip()[:20] or f"{secrets.randbelow(10000):04d}"
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
        "key": hash_key(key),
        "owner": g.user["RowKey"] if g.user and not f.get("anon") else "",
        "created": t.isoformat(), "closes": closes_at(dur, t).isoformat(),
    }
    db.questions.insert(q)
    db.mine.insert({"PartitionKey": owner_key(), "RowKey": q["RowKey"], "key": key,
                    "text": text, "created": q["created"]})
    if wants_json():
        return jsonify(ok=True, id=q["RowKey"], key=key)
    return redirect(url_for("detail", qid=q["RowKey"], made=1))


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
    msg = bad_text(body, strict=True)
    if msg:
        return jsonify(error=msg), 400
    past = db.comments.query(qid)
    if sum(1 for c in past if c.get("by") == g.vid) >= COMMENTS_PER_PERSON:
        return jsonify(error=f"1つの二択に書けるのは{COMMENTS_PER_PERSON}回までです。"), 429
    if any(c["body"] == body for c in past):
        return jsonify(error="同じコメントがもうあります。"), 400
    if too_fast("comment", 8):
        return jsonify(error="ちょっと待ってからもう一回。"), 429
    t = now()
    c = {"PartitionKey": qid, "RowKey": seq_id(t), "side": v["side"], "body": body,
         "by": g.vid, "created": t.isoformat(), "reports": 0}
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
    own = db.mine.get(owner_key(), qid)
    if not own and (not q.get("key") or not key or hash_key(key) != q["key"]):
        return jsonify(error="削除キーがちがいます。"), 403
    if own:
        db.mine.delete(owner_key(), qid)
    for c in db.comments.query(qid):
        db.comments.delete(qid, c["RowKey"])
    db.images.delete(q.get("img_a"))
    db.images.delete(q.get("img_b"))
    db.questions.delete(QPK, qid)
    return jsonify(ok=True)


# ---------- お問い合わせ ----------

CONTACT_KINDS = ["不具合", "ほしい機能", "こまっている", "その他"]


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "GET":
        return render_template("contact.html", kinds=CONTACT_KINDS)
    kind = request.form.get("kind") if request.form.get("kind") in CONTACT_KINDS else "その他"
    body = (request.form.get("body") or "").strip()
    if len(body) < 5 or len(body) > 400:
        return jsonify(error="5〜400字で書いてください。"), 400
    if too_fast("contact", 30):
        return jsonify(error="少し待ってからもう一回送ってください。"), 429
    t = now()
    no = f"{t.strftime('%m%d')}-{secrets.token_hex(2).upper()}"
    db.contact.insert({"PartitionKey": t.strftime("%Y-%m"), "RowKey": seq_id(t), "no": no, "kind": kind,
                       "body": body, "page": (request.form.get("page") or "")[:200],
                       "uid": g.user["RowKey"] if g.user else "", "created": t.isoformat()})
    return jsonify(ok=True, no=no)


# ---------- アカウント ----------

UID_RE = re.compile(r"[a-z0-9_]{3,15}")


def login_as(user):
    session.clear()
    session.permanent = True
    session["uid"] = user["RowKey"]
    g.user = user
    g.vid = user["vid"]
    g.new_vid = True


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("me"))
    err, f = None, request.form
    if request.method == "POST":
        uid = (f.get("uid") or "").strip().lower()
        name = (f.get("name") or "").strip()[:NAME_MAX]
        pw = f.get("pw") or ""
        grade = f.get("grade") if f.get("grade") in GRADES else "ひみつ"
        color = f.get("color") if f.get("color") in COLORS else COLORS[0]
        if not UID_RE.fullmatch(uid):
            err = "IDは半角の英小文字・数字・_ で3〜15文字にしてください。"
        elif not name:
            err = "表示名を入れてください。"
        elif len(pw) < 6:
            err = "パスワードは6文字以上にしてください。"
        elif bad_text(name) or bad_text(uid):
            err = bad_text(name) or bad_text(uid)
        elif db.users.get(UPK, uid):
            err = "そのIDはもう使われています。"
        elif too_fast("signup", 10):
            err = "少し待ってからもう一回。"
        else:
            user = {"PartitionKey": UPK, "RowKey": uid, "name": name, "pw": generate_password_hash(pw, method="pbkdf2:sha256:600000"),
                    "grade": grade, "color": color, "bio": "", "vid": g.vid, "created": now().isoformat()}
            db.users.insert(user)
            # 登録前にこの端末でつくった二択も引きつぐ
            for m in db.mine.query(f"v:{g.vid}"):
                m2 = dict(m, PartitionKey=f"u:{uid}")
                db.mine.upsert(m2)
                db.mine.delete(m["PartitionKey"], m["RowKey"])
            login_as(user)
            return redirect(url_for("me", welcome=1))
    return render_template("auth.html", mode="signup", err=err, f=f)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("me"))
    err, f = None, request.form
    if request.method == "POST":
        uid = (f.get("uid") or "").strip().lower()
        user = db.users.get(UPK, uid) if UID_RE.fullmatch(uid) else None
        if too_fast("login", 2):
            err = "少し待ってからもう一回。"
        elif not user or not check_password_hash(user["pw"], f.get("pw") or ""):
            err = "IDかパスワードがちがいます。"
        else:
            login_as(user)
            return redirect(url_for("me"))
    return render_template("auth.html", mode="login", err=err, f=f)


@app.post("/logout")
def logout():
    session.clear()
    resp = redirect(url_for("index"))
    resp.delete_cookie("vid")
    g.new_vid = False
    return resp


def profile_data(vid, mine_pk, t, with_votes=True):
    qs = {q["RowKey"]: q for q in db.questions.query(QPK)}
    votes = {v["RowKey"]: v for v in db.votes.query(vid)} if vid else {}
    deco = [decorate(dict(q), votes, t) for q in qs.values()]
    by_id = {q["id"]: q for q in deco}
    made = []
    for m in sorted(db.mine.query(mine_pk), key=lambda m: m["RowKey"]):
        q = by_id.get(m["RowKey"])
        if q:
            made.append(dict(q, mykey=m.get("key")))
    voted = sorted([q for q in deco if q["mine"]], key=lambda q: q["id"]) if with_votes else []
    stats = {
        "votes": len([v for v in votes if v in by_id]),
        "made": len(made),
        "switched": sum(1 for v in votes.values() if v.get("switched")),
        "minor": sum(1 for q in deco if q["stance"] == "少数派"),
    }
    return made, voted, stats, my_type(deco)


@app.get("/me")
def me():
    made, voted, stats, typ = profile_data(g.vid, owner_key(), now())
    return render_template("me.html", u=g.user, own=True, made=made, voted=voted, stats=stats, typ=typ,
                           tab=request.args.get("tab", "made"), welcome=request.args.get("welcome"))


@app.post("/me/edit")
def me_edit():
    if not g.user:
        return redirect(url_for("login"))
    f = request.form
    name = (f.get("name") or "").strip()[:NAME_MAX] or g.user["name"]
    bio = (f.get("bio") or "").strip()[:60]
    msg = bad_text(name) or bad_text(bio)
    if msg:
        return redirect(url_for("me", e=msg))
    g.user.update(
        name=name, bio=bio,
        grade=f.get("grade") if f.get("grade") in GRADES else g.user.get("grade"),
        color=f.get("color") if f.get("color") in COLORS else g.user.get("color"),
    )
    db.users.upsert(g.user)
    return redirect(url_for("me"))


@app.get("/u/<uid>")
def user_page(uid):
    u = db.users.get(UPK, uid)
    if not u:
        abort(404)
    if g.user and g.user["RowKey"] == uid:
        return redirect(url_for("me"))
    t = now()
    made, _, stats, typ = profile_data(u.get("vid"), f"u:{uid}", t, with_votes=False)
    mine = my_votes()
    made = [decorate({k: v for k, v in q.items() if k != "mykey"}, mine, t) for q in made]
    return render_template("me.html", u=u, own=False, made=made, voted=[], stats=stats, typ=typ, tab="made")


@app.errorhandler(413)
def too_large(_):
    return fail("画像が大きすぎます（合わせて12MBまで）。", 413)


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True, port=5050)
