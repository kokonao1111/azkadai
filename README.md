# どっち派

投稿がぜんぶ「AかBか」の二択になっている、高校生向けの掲示板。

https://dotchiha.azurewebsites.net/

- 要件定義：[requirements.md](requirements.md)
- Flask / Azure App Service / Table Storage / Blob Storage
- main に push すると GitHub Actions で App Service にデプロイされる

## 手元で動かす

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

`AZURE_STORAGE_CONNECTION_STRING` がないときは `data/` に保存される。
