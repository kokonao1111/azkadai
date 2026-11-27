"""データの保存先。

AZURE_STORAGE_CONNECTION_STRING があれば Table Storage / Blob Storage、
なければ data/ フォルダの JSON に保存する（手元で動かす用）。
"""
import json
import os
import threading
import uuid

CONN = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
PREFIX = os.environ.get("TABLE_PREFIX", "dotchi")
CONTAINER = os.environ.get("IMAGE_CONTAINER", "images")


class AzureTable:
    def __init__(self, service, name):
        self.t = service.create_table_if_not_exists(name)

    def query(self, pk):
        return [dict(e) for e in self.t.query_entities("PartitionKey eq @pk", parameters={"pk": pk})]

    def get(self, pk, rk):
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return dict(self.t.get_entity(pk, rk))
        except ResourceNotFoundError:
            return None

    def insert(self, e):
        self.t.create_entity(e)

    def upsert(self, e):
        from azure.data.tables import UpdateMode

        self.t.upsert_entity(e, mode=UpdateMode.MERGE)

    def bump(self, pk, rk, deltas):
        """数値をまとめて増減する。ETag で同時更新をはじいてやり直す。"""
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
        from azure.data.tables import UpdateMode

        for _ in range(6):
            try:
                e = self.t.get_entity(pk, rk)
            except ResourceNotFoundError:
                return None
            for k, v in deltas.items():
                e[k] = max(0, int(e.get(k, 0)) + v)
            try:
                self.t.update_entity(
                    e, mode=UpdateMode.MERGE, etag=e.metadata["etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
                return dict(e)
            except ResourceModifiedError:
                continue
        return None

    def delete(self, pk, rk):
        self.t.delete_entity(pk, rk)


class AzureImages:
    def __init__(self, conn):
        from azure.storage.blob import BlobServiceClient

        self.c = BlobServiceClient.from_connection_string(conn).get_container_client(CONTAINER)
        if not self.c.exists():
            self.c.create_container(public_access="blob")

    def put(self, data):
        from azure.storage.blob import ContentSettings

        name = f"dotchi/{uuid.uuid4().hex}.jpg"
        self.c.upload_blob(name, data, overwrite=True, content_settings=ContentSettings(
            content_type="image/jpeg", cache_control="public, max-age=604800"))
        return self.c.get_blob_client(name).url

    def delete(self, url):
        prefix = self.c.url.rstrip("/") + "/"
        if url and url.startswith(prefix):
            try:
                self.c.delete_blob(url[len(prefix):])
            except Exception:
                pass

    def asset(self, name):
        return self.c.get_blob_client(name).url


class LocalTable:
    def __init__(self, root, name):
        self.file = os.path.join(root, f"{name}.json")
        self.lock = threading.Lock()
        if not os.path.exists(self.file):
            self._save([])

    def _load(self):
        with open(self.file, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, rows):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)

    def query(self, pk):
        with self.lock:
            return [r for r in self._load() if r["PartitionKey"] == pk]

    def get(self, pk, rk):
        for r in self.query(pk):
            if r["RowKey"] == rk:
                return r
        return None

    def insert(self, e):
        with self.lock:
            rows = self._load()
            rows.append(dict(e))
            self._save(rows)

    def upsert(self, e):
        with self.lock:
            rows = self._load()
            for r in rows:
                if r["PartitionKey"] == e["PartitionKey"] and r["RowKey"] == e["RowKey"]:
                    r.update(e)
                    break
            else:
                rows.append(dict(e))
            self._save(rows)

    def bump(self, pk, rk, deltas):
        with self.lock:
            rows = self._load()
            for r in rows:
                if r["PartitionKey"] == pk and r["RowKey"] == rk:
                    for k, v in deltas.items():
                        r[k] = max(0, int(r.get(k, 0)) + v)
                    self._save(rows)
                    return r
        return None

    def delete(self, pk, rk):
        with self.lock:
            self._save([r for r in self._load() if not (r["PartitionKey"] == pk and r["RowKey"] == rk)])


class LocalImages:
    def __init__(self, root):
        self.dir = os.path.join(root, "images")
        os.makedirs(self.dir, exist_ok=True)

    def put(self, data):
        name = f"{uuid.uuid4().hex}.jpg"
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(data)
        return f"/local-images/{name}"

    def delete(self, url):
        if url and url.startswith("/local-images/"):
            p = os.path.join(self.dir, os.path.basename(url))
            if os.path.exists(p):
                os.remove(p)

    def asset(self, name):
        return "/static/favicon.svg" if name.endswith("mark.svg") else None


class Store:
    def __init__(self, base_dir):
        if CONN:
            from azure.data.tables import TableServiceClient

            svc = TableServiceClient.from_connection_string(CONN)
            make = lambda n: AzureTable(svc, PREFIX + n)
            self.images = AzureImages(CONN)
        else:
            root = os.path.join(base_dir, "data")
            os.makedirs(root, exist_ok=True)
            make = lambda n: LocalTable(root, n)
            self.images = LocalImages(root)
        self.questions = make("questions")
        self.votes = make("votes")
        self.comments = make("comments")
        self.users = make("users")
        self.mine = make("mine")  # 自分がつくった二択と削除キー
        self.contact = make("contact")  # お問い合わせ
