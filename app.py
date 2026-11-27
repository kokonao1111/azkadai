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
ERASE_HOUR = 5  # 毎朝5時に黒板を消す

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
SALT = app.secret_key

store = make_store(BASE_DIR)

CHALKS = {
    "white": "ふつう",
    "yellow": "質問",
    "pink": "急ぎ",
    "blue": "部活・行事",
}

PERIODS = [
    ((5, 0), (8, 40), "朝"),
    ((8, 40), (9, 30), "1限"),
    ((9, 30), (9, 40), "休み時間"),
    ((9, 40), (10, 30), "2限"),
    ((10, 30), (10, 40), "休み時間"),
    ((10, 40), (11, 30), "3限"),
    ((11, 30), (11, 40), "休み時間"),
    ((11, 40), (12, 30), "4限"),
    ((12, 30), (13, 20), "昼休み"),
    ((13, 20), (14, 10), "5限"),
    ((14, 10), (14, 20), "休み時間"),
    ((14, 20), (15, 10), "6限"),
    ((15, 10), (19, 0), "放課後"),
]

NAME_MAX = 12
BODY_MAX = 140
REPLY_MAX = 80
HIDE_AFTER_REPORTS = 3

# 個人情報が書き込まれないように、それっぽいものは弾く
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
    """5時を区切りにした「黒板の日付」。深夜2時の書き込みは前日の黒板に乗る。"""
    dt = dt or now_jst()
    if dt.hour < ERASE_HOUR:
        dt = dt - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def next_erase(dt=None):
    dt = dt or now_jst()
    t = dt.replace(hour=ERASE_HOUR, minute=0, second=0, microsecond=0)
    if dt >= t:
        t += timedelta(days=1)
    return t


def period_of(dt):
    hm = (dt.hour, dt.minute)
    for start, end, label in PERIODS:
        if start <= hm < end:
            return label
    return "夜"


def in_class(label):
    return label.endswith("限")


def row_key(dt):
    # 新しい順に並ぶように、時刻を反転させてキーにする
    inv = 10**13 - int(dt.timestamp() * 1000)
    return f"{inv:013d}-{uuid.uuid4().hex[:6]}"


def hash_key(key):
    return hashlib.sha256((SALT + key).encode()).hexdigest()


def check_text(text):
    for pat, what in PERSONAL_PATTERNS:
        if pat.search(text):
            return f"{what}っぽいものが入ってるみたい。黒板はみんなが見るので、個人情報は書かないでね。"
    low = text.lower()
    for w in NG_WORDS:
        if w in low:
            return "その言葉は黒板には書けません。言い方を変えてみて。"
    return None


def client_id():
    fwd = request.headers.get("X-Forwarded-For", "")
    ip = fwd.split(",")[0].split(":")[0].strip() or request.remote_addr or "?"
    return ip


def too_fast(seconds=15):
    cid = client_id()
    now = time.time()
    if now - _last_post[cid] < seconds:
        return True
    _last_post[cid] = now
    return False


def shrink_image(file_storage):
    """画像を小さくして、位置情報などのEXIFを消す。"""
    from PIL import Image, ImageOps

    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
    except Exception:
        return None
    img = img.convert("RGB")
    img.thumbnail((1080, 1080))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)  # exif は渡さない = 消える
    return out.getvalue()


def tilt(row):
    # 投稿ごとにちょっとだけ傾ける。毎回同じ角度になるようにキーから決める
    n = int(hashlib.md5(row.encode()).hexdigest()[:4], 16)
    return round((n % 70) / 10 - 3.5, 1)


def load_board(day):
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
        p["tilt"] = tilt(p["RowKey"])
    return posts


def pick_duty(day):
    """その日いちばん最初に書いた人が日直。"""
    rows = [r for r in store.list_day(day) if not r.get("parent")]
    if not rows:
        return None
    first = max(rows, key=lambda r: r["RowKey"])
    return first.get("name") or "名無し"


def public(r):
    keys = ["RowKey", "name", "body", "chalk", "period", "image", "tally", "created", "parent"]
    return {k: r.get(k) for k in keys}


@app.template_filter("hm")
def hm_filter(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(JST).strftime("%H:%M")
    except Exception:
        return ""


@app.template_filter("tally")
def tally_filter(n):
    """数を「正」の字で書く。5本で1文字。"""
    n = int(n or 0)
    strokes = ["", "一", "丅", "下", "止"]
    return "正" * (n // 5) + strokes[n % 5]


@app.context_processor
def inject():
    return {"chalks": CHALKS}


@app.route("/")
def index():
    day = board_day()
    now = now_jst()
    posts = load_board(day)
    chalk = request.args.get("chalk")
    if chalk in CHALKS:
        posts = [p for p in posts if p.get("chalk") == chalk]
    yesterday = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        erased = store.count_day(yesterday)
    except Exception:
        erased = 0
    weekday = "月火水木金土日"[datetime.strptime(day, "%Y-%m-%d").weekday()]
    d = datetime.strptime(day, "%Y-%m-%d")
    return render_template(
        "index.html",
        posts=posts,
        day=day,
        month=d.month,
        mday=d.day,
        weekday=weekday,
        duty=pick_duty(day),
        period=period_of(now),
        erase_at=next_erase(now).isoformat(),
        erased=erased,
        current_chalk=chalk if chalk in CHALKS else None,
        board_bg=store.asset_url("assets/board.jpg"),
        error=request.args.get("e"),
    )


@app.post("/post")
def create_post():
    name = (request.form.get("name") or "").strip()[:NAME_MAX]
    body = (request.form.get("body") or "").strip()
    chalk = request.form.get("chalk") if request.form.get("chalk") in CHALKS else "white"
    key = (request.form.get("key") or "").strip()[:20]
    wants_json = request.headers.get("Accept", "").startswith("application/json")

    def fail(msg, code=400):
        if wants_json:
            return jsonify(error=msg), code
        return redirect(url_for("index", e=msg) + "#tray")

    if not body:
        return fail("なにか書いてね。")
    if len(body) > BODY_MAX:
        return fail(f"黒板が足りません。{BODY_MAX}文字までにしてね。")
    msg = check_text(body) or check_text(name)
    if msg:
        return fail(msg)
    if too_fast():
        return fail("チョークが折れそう…少し待ってからもう一回。", 429)

    image_url = ""
    f = request.files.get("image")
    if f and f.filename:
        data = shrink_image(f)
        if data is None:
            return fail("その画像は貼れませんでした。jpg か png でお願い。")
        image_url = store.put_image(data)

    now = now_jst()
    period = period_of(now)
    entity = {
        "PartitionKey": board_day(now),
        "RowKey": row_key(now),
        "name": name or "名無し",
        "body": body,
        "chalk": chalk,
        "period": period,
        "image": image_url,
        "tally": 0,
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
        return jsonify(error=f"返事は{REPLY_MAX}文字までです。"), 400
    msg = check_text(body)
    if msg:
        return jsonify(error=msg), 400
    if too_fast(8):
        return jsonify(error="ちょっと待ってからもう一回。"), 429
    now = now_jst()
    entity = {
        "PartitionKey": day,
        "RowKey": row_key(now),
        "name": "",
        "body": body,
        "chalk": parent.get("chalk", "white"),
        "period": period_of(now),
        "image": "",
        "tally": 0,
        "reports": 0,
        "key": "",
        "parent": row,
        "created": now.isoformat(),
    }
    store.insert(entity)
    return jsonify(ok=True, reply=public(entity))


@app.post("/post/<row>/tally")
def add_tally(row):
    r = store.bump(board_day(), row, "tally")
    if not r:
        abort(404)
    return jsonify(tally=int(r["tally"]), mark=tally_filter(r["tally"]))


@app.post("/post/<row>/report")
def report(row):
    r = store.bump(board_day(), row, "reports")
    if not r:
        abort(404)
    return jsonify(ok=True, hidden=int(r["reports"]) >= HIDE_AFTER_REPORTS)


@app.post("/post/<row>/erase")
def erase(row):
    day = board_day()
    r = store.get(day, row)
    if not r:
        abort(404)
    key = (request.form.get("key") or "").strip()
    if not r.get("key") or not key or hash_key(key) != r["key"]:
        return jsonify(error="消しゴム番号がちがいます。"), 403
    for child in store.list_day(day):
        if child.get("parent") == row:
            store.delete(day, child["RowKey"])
    store.delete_image(r.get("image"))
    store.delete(day, row)
    return jsonify(ok=True)


@app.get("/api/board")
def api_board():
    """新しい書き込みが来たか確かめる用。"""
    day = board_day()
    posts = load_board(day)
    return jsonify(
        day=day,
        count=len(posts),
        latest=posts[0]["RowKey"] if posts else None,
    )


@app.get("/local-images/<name>")
def local_image(name):
    return send_from_directory(os.path.join(BASE_DIR, "data", "images"), name)


@app.get("/about")
def about():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "about.html")


@app.errorhandler(413)
def too_large(_):
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(error="画像が大きすぎます（8MBまで）。"), 413
    return redirect(url_for("index", e="画像が大きすぎます（8MBまで）。"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
