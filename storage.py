"""投稿と画像の保存先。

AZURE_STORAGE_CONNECTION_STRING が設定されていれば Table Storage / Blob Storage を使う。
設定されていなければ手元の data/ フォルダに保存する（ローカル開発用）。
"""
import json
import os
import threading
import uuid

CONN = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
TABLE_NAME = os.environ.get("POSTS_TABLE", "posts")
CONTAINER = os.environ.get("IMAGE_CONTAINER", "images")


class AzureStore:
    def __init__(self, conn):
        from azure.data.tables import TableServiceClient
        from azure.storage.blob import BlobServiceClient

        self._tables = TableServiceClient.from_connection_string(conn)
        self.table = self._tables.create_table_if_not_exists(TABLE_NAME)
        self._blobs = BlobServiceClient.from_connection_string(conn)
        self.container = self._blobs.get_container_client(CONTAINER)
        if not self.container.exists():
            self.container.create_container(public_access="blob")

    def list_day(self, day):
        rows = self.table.query_entities(
            "PartitionKey eq @pk", parameters={"pk": day}
        )
        return [dict(r) for r in rows]

    def count_day(self, day):
        rows = self.table.query_entities(
            "PartitionKey eq @pk", parameters={"pk": day}, select=["RowKey"]
        )
        return sum(1 for _ in rows)

    def get(self, day, row):
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return dict(self.table.get_entity(day, row))
        except ResourceNotFoundError:
            return None

    def insert(self, entity):
        self.table.create_entity(entity)

    def bump(self, day, row, field, amount=1):
        """楽観ロックで数値を増やす。同時に押されても数え漏れしにくくする。"""
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
        from azure.data.tables import UpdateMode

        for _ in range(5):
            try:
                ent = self.table.get_entity(day, row)
            except ResourceNotFoundError:
                return None
            ent[field] = int(ent.get(field, 0)) + amount
            try:
                self.table.update_entity(
                    ent,
                    mode=UpdateMode.MERGE,
                    etag=ent.metadata["etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
                return dict(ent)
            except ResourceModifiedError:
                continue
        return None

    def delete(self, day, row):
        self.table.delete_entity(day, row)

    def put_image(self, data, content_type="image/jpeg"):
        from azure.storage.blob import ContentSettings

        name = f"posts/{uuid.uuid4().hex}.jpg"
        self.container.upload_blob(
            name,
            data,
            overwrite=True,
            content_settings=ContentSettings(
                content_type=content_type, cache_control="public, max-age=604800"
            ),
        )
        return self.container.get_blob_client(name).url

    def delete_image(self, url):
        prefix = self.container.url.rstrip("/") + "/"
        if url and url.startswith(prefix):
            try:
                self.container.delete_blob(url[len(prefix):])
            except Exception:
                pass

    def asset_url(self, name):
        return self.container.get_blob_client(name).url


class LocalStore:
    """Azure なしで動かすときの代わり。JSON ファイル 1 個に全部入れる。"""

    def __init__(self, root):
        self.root = root
        self.file = os.path.join(root, "posts.json")
        self.img_dir = os.path.join(root, "images")
        os.makedirs(self.img_dir, exist_ok=True)
        self.lock = threading.Lock()
        if not os.path.exists(self.file):
            self._save([])

    def _load(self):
        with open(self.file, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, rows):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)

    def list_day(self, day):
        with self.lock:
            return [r for r in self._load() if r["PartitionKey"] == day]

    def count_day(self, day):
        return len(self.list_day(day))

    def get(self, day, row):
        for r in self.list_day(day):
            if r["RowKey"] == row:
                return r
        return None

    def insert(self, entity):
        with self.lock:
            rows = self._load()
            rows.append(entity)
            self._save(rows)

    def bump(self, day, row, field, amount=1):
        with self.lock:
            rows = self._load()
            for r in rows:
                if r["PartitionKey"] == day and r["RowKey"] == row:
                    r[field] = int(r.get(field, 0)) + amount
                    self._save(rows)
                    return r
        return None

    def delete(self, day, row):
        with self.lock:
            rows = [
                r
                for r in self._load()
                if not (r["PartitionKey"] == day and r["RowKey"] == row)
            ]
            self._save(rows)

    def put_image(self, data, content_type="image/jpeg"):
        name = f"{uuid.uuid4().hex}.jpg"
        with open(os.path.join(self.img_dir, name), "wb") as f:
            f.write(data)
        return f"/local-images/{name}"

    def delete_image(self, url):
        if url and url.startswith("/local-images/"):
            path = os.path.join(self.img_dir, os.path.basename(url))
            if os.path.exists(path):
                os.remove(path)

    def asset_url(self, name):
        return None


def make_store(base_dir):
    if CONN:
        return AzureStore(CONN)
    return LocalStore(os.path.join(base_dir, "data"))
