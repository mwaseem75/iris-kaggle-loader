# REST API

Base path: `/api/kaggle2iris`. Every endpoint needs an IRIS login (basic authentication) and answers JSON.

```bash
curl -u _SYSTEM:SYS "http://localhost:52773/api/kaggle2iris/datasets?search=iris"
```

Kaggle is only ever called from the IRIS server. `KAGGLE_API_TOKEN` (or `KAGGLE_USERNAME` and `KAGGLE_KEY`), if you set it, stays in the container and never shows up in a response.

## Errors

Errors look like this:

```json
{"error": "File 'nope.csv' not found in uciml/iris. Files you can import: Iris.csv", "code": "not_found"}
```

| code | HTTP | Meaning |
|---|---|---|
| `bad_request` | 400 | a parameter is missing or not valid, or the file cannot be read |
| `credentials` | 403 | Kaggle rejected the credentials that are set in the container |
| `not_found` | 404 | dataset, version, file, job or table does not exist; Kaggle gives the same answer for a private dataset you have no access to |
| `conflict` | 409 | the target table belongs to someone else, mode `fail` and the table exists, or the job has already finished |
| `preview_unavailable` | 409 | the file cannot be previewed (not a tabular file, or a Parquet, JSON or Excel file over 200 MB) |
| `too_large` | 413 | a download went over `KAGGLE2IRIS_MAX_DOWNLOAD_MB` |
| `rate_limited` | 429 | Kaggle is limiting requests; wait a minute |
| `busy` | 429 | the maximum number of background imports is already running |
| `upstream` | 502 | Kaggle answered with an error |
| `timeout` | 504 | Kaggle did not answer within 20 seconds |

`/load`, `/history` and `/preview/:table` answer `{"error": "..."}` without a `code`.

## Discovery

### `GET /datasets`

Search Kaggle. Each page has up to 20 datasets.

| Parameter | Default | |
|---|---|---|
| `search` | empty | up to 100 characters; empty lists datasets by the sort order |
| `sort` | `hottest` | `hottest`, `votes`, `updated`, `active` or `published` |
| `fileType` | `all` | `all`, `csv`, `json`, `parquet`, `sqlite` or `bigQuery` |
| `license` | `all` | `all`, `cc`, `gpl`, `odb` or `other` |
| `tag` | | a Kaggle tag, for example `finance` |
| `minSize`, `maxSize` | | total dataset size in bytes |
| `page` | 1 | 1 to 500 |
| `refresh` | | `1` skips the cache |

```json
{"items": [{"ref": "uciml/iris", "title": "Iris Species",
            "subtitle": "Classify iris plants into three species in this classic dataset",
            "owner": "UCI Machine Learning", "totalBytes": 3687, "usability": 0.7941176, "votes": 4965,
            "downloads": 950639, "views": 3255005, "lastUpdated": "2016-09-27T07:38:05.44Z",
            "license": "CC0: Public Domain", "tags": ["biology"], "private": false, "currentVersion": 2,
            "url": "https://www.kaggle.com/datasets/uciml/iris"}],
 "count": 20, "page": 1, "hasMore": true, "fetchedAt": "2026-10-14T11:31:05Z", "cached": false}
```

Private datasets are left out unless the container runs with `KAGGLE2IRIS_SHOW_PRIVATE=1`.

### `GET /dataset?id=owner/dataset`

Details, versions and files. Optional `version` (a number; empty means the latest) and `refresh`.

```json
{"ref": "uciml/iris", "title": "Iris Species", "...": "the fields of a search result",
 "description": "The Iris dataset was used in R.A. Fisher's classic 1936 paper ...",
 "versions": [{"number": 2, "date": "2016-09-27T07:38:05.44Z", "notes": "Republishing files ...", "status": "Ready"},
              {"number": 1, "date": "2016-01-12T00:33:31Z", "notes": "Initial release", "status": "Ready"}],
 "version": 2, "pinned": false,
 "files": [{"name": "Iris.csv", "size": 5107, "format": "csv", "importable": true, "created": "2019-09-19T03:05:19.853Z"},
           {"name": "database.sqlite", "size": 10240, "format": null, "importable": false, "created": "2019-09-19T03:05:19.901Z"}],
 "filesTruncated": false, "importableFiles": 1, "fetchedAt": "2026-10-14T11:31:06Z", "cached": false}
```

`format` is `csv`, `tsv`, `parquet`, `json`, `jsonl`, `excel` or `null` for files that cannot be imported. Only the first 1000 files of a dataset are listed; `filesTruncated` says when there are more.

### `GET /dataset/preview`

Rows of a file, without importing anything.

| Parameter | Default | |
|---|---|---|
| `id`, `file` | | required; a file in a folder is `folder/name.csv` |
| `offset` | 0 | 0 to 999 |
| `rows` | 20 | 1 to 100 |
| `version` | latest | dataset version |
| `sheet` | first sheet | Excel only |
| `refresh` | | `1` skips the cache |

```json
{"dataset": "uciml/iris", "file": "Iris.csv", "version": 2, "offset": 100,
 "columns": ["Id", "SepalLengthCm", "SepalWidthCm", "PetalLengthCm", "PetalWidthCm", "Species"],
 "types": [{"name": "Id", "type": "integer"}, {"name": "SepalLengthCm", "type": "number"}, "..."],
 "rows": [[101, 6.3, 3.3, 6.0, 2.5, "Iris-virginica"]],
 "total": 150, "truncated": false, "encoding": "utf-8", "sheets": null, "sheet": null, "partial": false,
 "cached": false}
```

`types` are `integer`, `number`, `boolean`, `date`, `timestamp`, `text`, `list` or `object`. For a large CSV, TSV or JSON Lines file only its start is read: then `partial` is `true` and `total` is `null`. Text longer than 500 characters is cut (`truncated`). `encoding` is `utf-8`, `cp1252` or `latin-1` for text files. For Excel, `sheets` lists the sheet names and `sheet` is the one shown.

### `GET /files?id=owner/dataset`

The files an all-files import takes, with the table each one goes to. Optional `version` and `schema` (default `KG`).

```json
{"dataset": "olistbr/brazilian-ecommerce", "version": 2,
 "files": [{"file": "olist_customers_dataset.csv", "table": "KG.olist_customers_dataset", "size": 9033957, "format": "csv"},
           {"file": "olist_geolocation_dataset.csv", "table": "KG.olist_geolocation_dataset", "size": 61273883, "format": "csv"}]}
```

### `GET /status`

```json
{"version": "1.0.0", "credentials": false, "maxRows": 1000000, "maxDownloadMb": 500, "previewMaxMb": 200,
 "exactCollation": true, "cacheTtl": 600, "showPrivate": false, "iris": "2026.1 (Build 234)"}
```

`credentials` only says whether Kaggle credentials are set.

## Importing

### `POST /plan`

What an import would create, without writing anything. Same body as `/load`.

```json
{"dataset": "uciml/iris", "file": "Iris.csv", "table": "Demo.Iris"}
```

```json
{"dataset": "uciml/iris", "title": "Iris Species", "file": "Iris.csv", "format": "csv", "sheet": null, "sheets": null,
 "version": 2, "pinned": false, "limit": 0, "mode": "replace", "table": "Demo.Iris", "tableExists": false,
 "ownedByLoader": false, "action": "create", "reason": null,
 "columns": [{"source": "Id", "name": "Id_", "type": "BIGINT", "note": "renamed from 'Id'"},
             {"source": "SepalLengthCm", "name": "SepalLengthCm", "type": "DOUBLE"}, "..."],
 "ddl": "CREATE TABLE Demo.Iris (\n  Id_ BIGINT,\n  ...)",
 "download": {"fullLoad": true, "bytes": 5107, "withinLimits": true},
 "warnings": [], "sampleRows": 150}
```

`action` is `create`, `replace`, `append` or `refused`. A refused plan has a `reason` (table not created by kaggle2iris, mode `fail` and the table exists, over the size limits, unknown mode); it is still a 200 answer. The columns come from the first 200 rows of the file, or from all rows of a small file; `warnings` says which. A file that cannot be imported at all (an image, a SQLite database) is a `bad_request` error instead.

### `POST /load`

Imports synchronously and answers when the import is done.

```json
{"dataset": "uciml/iris", "file": "Iris.csv", "table": "Demo.Iris", "limit": 0, "mode": "replace",
 "version": "", "sheet": ""}
```

`dataset` and `file` are required. The answer is the import result:

```json
{"table": "Demo.Iris", "rows": 150, "columns": {"Id_": "BIGINT", "SepalLengthCm": "DOUBLE", "...": "..."},
 "seconds": 0.35, "ddl": "CREATE TABLE Demo.Iris (...)", "dataset": "uciml/iris", "title": "Iris Species",
 "file": "Iris.csv", "sheet": null, "version": 2, "pinned": false, "encoding": "utf-8"}
```

Long imports can run into the web gateway's 60 second timeout; use `/jobs` for those.

### `POST /jobs`

Starts the same import in the background and answers `202` right away with the job (see below). The body is the one of `/load`, with one addition: `"file": "*"` imports every tabular file of the dataset, one table per file, and `table` is then the schema (default `KG`). At most two background imports run at the same time (`KAGGLE2IRIS_MAX_JOBS`).

### `GET /jobs`, `GET /jobs/:id`

`GET /jobs?limit=50` lists the newest jobs as `{"items": [...], "count": n}`.

```json
{"id": "17", "state": "running", "phase": "inserting", "rowsDone": 3000, "rowsTotal": 10000,
 "dataset": "olistbr/brazilian-ecommerce", "file": "olist_orders_dataset.csv", "sheet": null, "table": "Demo.Orders",
 "mode": "replace", "version": null, "limit": 10000, "filesDone": 0, "filesTotal": 1,
 "currentFile": "olist_orders_dataset.csv", "user": "_SYSTEM", "createdAt": "2026-10-14 10:00:00",
 "startedAt": "2026-10-14 10:00:01", "finishedAt": null, "elapsed": 12, "cancelRequested": false,
 "result": null, "error": null, "importLogId": null}
```

`state` is `queued`, `running`, `ok`, `error`, `cancelled` or `interrupted` (the process ended, for example because IRIS restarted; its transaction was rolled back). `phase` is `resolving`, `fetching`, `inserting` or `finishing`. While `phase` is `fetching`, `rowsDone` and `rowsTotal` count downloaded bytes, not rows.

For an all-files job, `filesDone`, `filesTotal` and `currentFile` show how far it is, and `rowsDone` and `rowsTotal` are about the current file. When the job is done, `result` holds what `/load` would have answered, or for an all-files job `{"dataset": ..., "version": ..., "tables": [one result per file], "rows": total}`. If an all-files job fails, `result` lists the tables imported before the failing file.

### `POST /jobs/:id/cancel`

The import stops before its next batch of 1000 rows (or before its next file) and is rolled back: a new table is removed, rows appended to an existing table are taken out again. A download that is already running is not interrupted; the cancel takes effect when it ends.

## History and tables

### `GET /history`

The last 50 imports from any path (web page, REST, SQL, ObjectScript), newest first.

```json
[{"id": "145", "dataset": "mkechinov/ecommerce-behavior-data-from-multi-category-store", "file": "2019-Nov.csv",
  "sheet": "", "table": "Demo.Events", "rows": 10000, "seconds": 0.72, "status": "OK", "message": "",
  "createdAt": "2026-10-14 11:30:34", "version": 8, "encoding": "utf-8"}]
```

### `GET /tables`

Tables created by kaggle2iris that still exist.

```json
{"items": [{"table": "Demo.Events", "rows": 10000, "columnCount": 9,
            "dataset": "mkechinov/ecommerce-behavior-data-from-multi-category-store", "file": "2019-Nov.csv",
            "sheet": null, "version": 8, "lastImport": "2026-10-14 11:30:34"}],
 "count": 1}
```

### `GET /tables/:table`

The same fields plus `columns` (`[{"name": "event_time", "type": "TIMESTAMP"}, ...]`) and the last 10 `imports` into the table. Other tables answer `not_found`.

### `GET /preview/:table`

Rows of a table created by kaggle2iris: `?rows=20` (up to 200) and `?offset=` for paging. Dates and timestamps come back as text (`2019-11-01 00:00:00`).

```json
{"table": "Demo.Orders", "total": 3000, "columns": ["order_id", "customer_id", "order_status", "order_purchase_timestamp", "..."],
 "rows": [["e481f51cbdc54678b7cc49136f2d6af7", "9ef432eb6251297304e76186b10a928d", "delivered", "2017-10-02 10:56:33", "..."]],
 "offset": 50}
```

## Caching

Search results are cached for 5 minutes, details and previews for 10 (`KAGGLE2IRIS_CACHE_TTL`, `0` turns the cache off). The cache lives in IRISTEMP, so it is shared by all IRIS processes and gone after a restart. Results seen with a token are never handed to requests without one, errors are not cached, and `refresh=1` asks Kaggle again.

Downloaded files are kept in `KAGGLE2IRIS_CACHE_DIR` (in the Docker image `/home/irisowner/.cache/kaggle2iris`), one folder per dataset and version, so a file is downloaded once.
