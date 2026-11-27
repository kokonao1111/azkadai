# 放課後の黒板

毎朝5時に全部消える、高校生のための黒板型の掲示板。

https://houkago-kokuban.azurewebsites.net/

- 要件定義：[requirements.md](requirements.md)
- Flask / Azure App Service / Table Storage / Blob Storage
- main に push すると GitHub Actions で App Service にデプロイされる

## 手元で動かす

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

`AZURE_STORAGE_CONNECTION_STRING` を設定していないときは `data/` に保存される。
