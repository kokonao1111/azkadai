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
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from storage import make_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))
RESET_HOUR = 5  # 毎朝5時に全部消える

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
SALT = app.secret_key

store = make_store(BASE_DIR)

TAGS = {
    "talk": "雑談",
    "ask": "質問",
    "now": "いま",
    "event": "おしらせ",
}

NAME_MAX = 12
BODY_MAX = 140
REPLY_MAX = 80
HIDE_AFTER_REPORTS = 3

# 個人情報っぽいものは書き込ませない
PERSONAL_PATTERNS = [
    (re.compile(r"0\d{1,4}[-ー－\s]?\d{1,4}[-ー－\s]?\d{3,4}"), "電話番号"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "メールアドレス"),
    (re.compile(r"(line|ライン|ＬＩＮＥ)\s*(id|ＩＤ|アイディー)", re.I), "LINE ID"),
    (re.compile(r"(インスタ|insta|ig)\s*[:：@＠]", re.I), "SNSのアカウント"),
    (re.compile(r"(住所|番地|丁目)\s*[0-9０-９]"), "住所"),
]
NG_WORDS = ["死ね", "しね", "殺す", "ころす", "きもい", "キモい", "うざい", "ブス", "消えろ"]

_last_post = defaultdict(float)


def now_jst():
    return datetime.now(JST)


def board_day(dt=None):
    """5時を区切りにした日付。深夜2時の書き込みは前の日の分になる。"""
    dt = dt or now_jst()
    if dt.hour < RESET_HOUR:
        dt = dt - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def next_reset(dt=None):
    dt = dt or now_jst()
    t = dt.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if dt >= t:
        t += timedelta(days=1)
    return t


def row_key(dt):
    # 新しい順に並ぶように時刻を反転してキーにする
    inv = 10**13 - int(dt.timestamp() * 1000)
    return f"{inv:013d}-{uuid.uuid4().hex[:6]}"


def hash_key(key):
    return hashlib.sha256((SALT + key).encode()).hexdigest()


def check_text(text):
    for pat, what in PERSONAL_PATTERNS:
        if pat.search(text):
            return f"{what}は書き込めません。"
    low = text.lower()
    for w in NG_WORDS:
        if w in low:
            return "その言葉は書き込めません。"
    return None


def client_id():
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].split(":")[0].strip() or request.remote_addr or "?"


def too_fast(seconds=15):
    cid = client_id()
    now = time.time()
    if now - _last_post[cid] < seconds:
        return True
    _last_post[cid] = now
    return False


def shrink_image(file_storage):
    """長い辺を1080pxにして、位置情報などのEXIFを消す。"""
    from PIL import Image, ImageOps

    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
    except Exception:
        return None
    img = img.convert("RGB")
    img.thumbnail((1080, 1080))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)  # exifを渡さないので消える
    return out.getvalue()


def load_posts(day):
    rows = store.list_day(day)
    posts, replies = [], defaultdict(list)
    for r in rows:
        if int(r.get("reports", 0)) >= HIDE_AFTER_REPORTS:
            continue
        if r.get("parent"):
            replies[r["parent"]].append(r)
        else:
            posts.append(r)
    posts.sort(key=lambda r: r["RowKey"])
    for p in posts:
        p["replies"] = sorted(replies.get(p["RowKey"], []), key=lambda r: r["RowKey"], reverse=True)
        if p.get("tag") not in TAGS:
            p["tag"] = "talk"
    return posts


def public(r):
    keys = ["RowKey", "name", "body", "tag", "image", "likes", "created", "parent"]
    return {k: r.get(k) for k in keys}


@app.template_filter("hm")
def hm_filter(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(JST).strftime("%H:%M")
    except Exception:
        return ""


@app.context_processor
def inject():
    return {"tags": TAGS}


@app.route("/")
def index():
    now = now_jst()
    day = board_day(now)
    posts = load_posts(day)
    tag = request.args.get("tag")
    if tag in TAGS:
        posts = [p for p in posts if p.get("tag") == tag]
    yesterday = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        gone = store.count_day(yesterday)
    except Exception:
        gone = 0
    reset = next_reset(now)
    left = (reset - now).total_seconds()
    return render_template(
        "index.html",
        posts=posts,
        reset_at=reset.isoformat(),
        left_ratio=round(left / 86400, 4),
        gone=gone,
        current_tag=tag if tag in TAGS else None,
        logo=store.asset_url("assets/logo.svg"),
        error=request.args.get("e"),
    )


@app.post("/post")
def create_post():
    name = (request.form.get("name") or "").strip()[:NAME_MAX]
    body = (request.form.get("body") or "").strip()
    tag = request.form.get("tag") if request.form.get("tag") in TAGS else "talk"
    key = (request.form.get("key") or "").strip()[:20]
    wants_json = request.headers.get("Accept", "").startswith("application/json")

    def fail(msg, code=400):
        if wants_json:
            return jsonify(error=msg), code
        return redirect(url_for("index", e=msg))

    if not body:
        return fail("本文が空です。")
    if len(body) > BODY_MAX:
        return fail(f"{BODY_MAX}文字までです。")
    msg = check_text(body) or check_text(name)
    if msg:
        return fail(msg)
    if too_fast():
        return fail("連続では書き込めません。少し待ってください。", 429)

    image_url = ""
    f = request.files.get("image")
    if f and f.filename:
        data = shrink_image(f)
        if data is None:
            return fail("この画像は使えません。jpg・png・webpにしてください。")
        image_url = store.put_image(data)

    now = now_jst()
    entity = {
        "PartitionKey": board_day(now),
        "RowKey": row_key(now),
        "name": name,
        "body": body,
        "tag": tag,
        "image": image_url,
        "likes": 0,
        "reports": 0,
        "key": hash_key(key) if key else "",
        "parent": "",
        "created": now.isoformat(),
    }
    store.insert(entity)
    if wants_json:
        return jsonify(ok=True, post=public(entity))
    return redirect(url_for("index") + f"#p-{entity['RowKey']}")


@app.post("/post/<row>/reply")
def reply(row):
    day = board_day()
    parent = store.get(day, row)
    if not parent or parent.get("parent"):
        abort(404)
    body = (request.form.get("body") or "").strip()
    if not body or len(body) > REPLY_MAX:
        return jsonify(error=f"返信は{REPLY_MAX}文字までです。"), 400
    msg = check_text(body)
    if msg:
        return jsonify(error=msg), 400
    if too_fast(8):
        return jsonify(error="少し待ってください。"), 429
    now = now_jst()
    entity = {
        "PartitionKey": day,
        "RowKey": row_key(now),
        "name": "",
        "body": body,
        "tag": parent.get("tag", "talk"),
        "image": "",
        "likes": 0,
        "reports": 0,
        "key": "",
        "parent": row,
        "created": now.isoformat(),
    }
    store.insert(entity)
    return jsonify(ok=True, reply=public(entity))


@app.post("/post/<row>/like")
def like(row):
    r = store.bump(board_day(), row, "likes")
    if not r:
        abort(404)
    return jsonify(likes=int(r["likes"]))


@app.post("/post/<row>/report")
def report(row):
    r = store.bump(board_day(), row, "reports")
    if not r:
        abort(404)
    return jsonify(ok=True, hidden=int(r["reports"]) >= HIDE_AFTER_REPORTS)


@app.post("/post/<row>/delete")
def delete(row):
    day = board_day()
    r = store.get(day, row)
    if not r:
        abort(404)
    key = (request.form.get("key") or "").strip()
    if not r.get("key") or not key or hash_key(key) != r["key"]:
        return jsonify(error="削除キーがちがいます。"), 403
    for child in store.list_day(day):
        if child.get("parent") == row:
            store.delete(day, child["RowKey"])
    store.delete_image(r.get("image"))
    store.delete(day, row)
    return jsonify(ok=True)


@app.get("/api/latest")
def api_latest():
    posts = load_posts(board_day())
    return jsonify(count=len(posts), latest=posts[0]["RowKey"] if posts else None)


@app.get("/local-images/<name>")
def local_image(name):
    return send_from_directory(os.path.join(BASE_DIR, "data", "images"), name)


@app.get("/about")
def about():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "about.html")


@app.errorhandler(413)
def too_large(_):
    msg = "画像が大きすぎます（8MBまで）。"
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(error=msg), 413
    return redirect(url_for("index", e=msg))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
