"""黒板の背景画像（消しゴムのあと入り）を作って Blob Storage に置く。

使い方: AZURE_STORAGE_CONNECTION_STRING=... python tools/make_board.py
"""
import io
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter

W, H = 1200, 800
random.seed(7)

base = Image.new("RGB", (W, H), (43, 67, 55))
noise = Image.effect_noise((W, H), 22).convert("L")
img = Image.composite(Image.new("RGB", (W, H), (58, 84, 70)), base, noise.point(lambda v: 40 if v > 150 else 0))

# 黒板消しでこすったあと
smear = Image.new("L", (W, H), 0)
d = ImageDraw.Draw(smear)
for _ in range(22):
    cx, cy = random.randint(0, W), random.randint(0, H)
    r = random.randint(120, 320)
    a0 = random.uniform(0, math.pi)
    for i in range(70):
        t = a0 + i * 0.03
        x = cx + math.cos(t) * r
        y = cy + math.sin(t) * r * .35
        d.ellipse([x - 40, y - 12, x + 40, y + 12], fill=random.randint(18, 40))
smear = smear.filter(ImageFilter.GaussianBlur(14))
img = Image.composite(Image.new("RGB", (W, H), (120, 140, 128)), img, smear)

# 消し残りのチョークの粉
dust = Image.new("L", (W, H), 0)
dd = ImageDraw.Draw(dust)
for _ in range(1400):
    x, y = random.randint(0, W), random.randint(0, H)
    dd.point((x, y), fill=random.randint(30, 90))
dust = dust.filter(ImageFilter.GaussianBlur(.6))
img = Image.composite(Image.new("RGB", (W, H), (200, 210, 200)), img, dust)

out = io.BytesIO()
img.save(out, "JPEG", quality=80, optimize=True)
data = out.getvalue()

conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
if conn:
    from azure.storage.blob import BlobServiceClient, ContentSettings

    c = BlobServiceClient.from_connection_string(conn).get_container_client("images")
    if not c.exists():
        c.create_container(public_access="blob")
    c.upload_blob("assets/board.jpg", data, overwrite=True,
                  content_settings=ContentSettings(content_type="image/jpeg", cache_control="public, max-age=2592000"))
    print(c.get_blob_client("assets/board.jpg").url)
else:
    with open("board.jpg", "wb") as f:
        f.write(data)
    print("board.jpg")
