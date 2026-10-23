# iris-kaggle-loader

**Dataset Explorer and Import Studio for InterSystems IRIS.** Find a dataset on [Kaggle](https://www.kaggle.com/datasets), look inside its files, and load them into IRIS tables: from a web page, with one line of ObjectScript, or with one SQL statement. Public datasets work without a Kaggle account.

![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![InterSystems IRIS 2023.1+](https://img.shields.io/badge/InterSystems%20IRIS-2023.1%2B-1f6feb)
![Version 1.0.0](https://img.shields.io/badge/version-1.0.0-informational)

> <img width="941" alt="image" src="https://github.com/user-attachments/assets/695fc68a-4439-478d-97ba-e438ac56a267" />

> The Explore page with search results for "iris".

## Contents
- [Features](#features)
- [Quick start](#quick-start)
- [Tutorial](#tutorial)
  1. [Start IRIS](#1-start-iris)
  2. [Find a dataset](#2-find-a-dataset)
  3. [Look inside a dataset](#3-look-inside-a-dataset)
  4. [Plan and run an import](#4-plan-and-run-an-import)
  5. [Import every file of a dataset](#5-import-every-file-of-a-dataset)
  6. [Follow your imports](#6-follow-your-imports)
  7. [Work with the imported tables](#7-work-with-the-imported-tables)
  8. [Query the tables with SQL](#8-query-the-tables-with-sql)
  9. [Import from an IRIS terminal](#9-import-from-an-iris-terminal)
  10. [Import from SQL](#10-import-from-sql)
  11. [File types, Excel sheets and versions](#11-file-types-excel-sheets-and-versions)
  12. [Big files](#12-big-files)
  13. [Private datasets](#13-private-datasets)
  14. [The REST API](#14-the-rest-api)
  15. [Python](#15-python)
  16. [Themes and phones](#16-themes-and-phones)
- [Installing with IPM](#installing-with-ipm)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Safety and limits](#safety-and-limits)
- [Running the tests](#running-the-tests)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [License](#license)

## Features

**Discover**
- Search Kaggle by name or keyword, sort by hottest, most votes, recently updated or newest, and filter by file type, license, size and tag.
- See each dataset's title, description, license, tags, size, votes, downloads and usability rating.
- List every file of a dataset with its type and size, and see which ones can be imported.
- Page through the real rows of a file before importing anything, with the column types the import would use. For a large CSV only its first megabytes are read, so even an 8 GB file opens in a few seconds.
- Pick an older version of a dataset and look at its files as they were.

**Import**
- CSV, TSV, Parquet, JSON, JSON Lines and Excel (`.xlsx`).
- One file into one table, or every tabular file of a dataset in one go, one table per file.
- Plan an import before running it: the target table, every IRIS column and type, the download size, and a clear reason if the import would not be allowed.
- Background imports with live progress (bytes while downloading, rows while writing), so large files do not hit the web gateway's 60 second limit. Cancel at any time; nothing of a cancelled import is kept.
- Row limits that only download the start of a CSV or JSON Lines file, instead of the whole file.
- Replace, append or fail when the table already exists.
- Pin an import to a dataset version so the same rows come back every time. Every import records the version it read.
- The same import from the web page, ObjectScript, SQL, REST or Python.

**Data quality**
- CSV has no types, so every column is typed from all of its values: integers, decimals, booleans, dates and timestamps are recognised, and a column only becomes numeric if every value is a number.
- Values with leading zeros, like zip codes and ids (`01003`), stay text.
- `NA`, `N/A`, `null` and similar markers in a numeric column become NULL.
- Files that are not UTF-8 are read as Windows-1252 or Latin-1, and the import says so.
- Parquet and Excel keep their own types. Excel columns where numbers were saved as text are typed like CSV.
- Text goes into normal `VARCHAR` columns, so `=`, `LIKE`, `GROUP BY` and `DISTINCT` work as usual, and text compares exactly.
- Column names are cleaned for SQL, including reserved words and accents.

**Manage**
- An Imports page with every background import and the full history of all imports, whatever started them.
- A Tables page with the tables the loader created: rows, columns, the imports into each table, and one-click re-import.
- Existing tables that the loader did not create are never changed.
- Every import is all or nothing.

**Operate**
- No Kaggle account needed for public datasets. An optional Kaggle API token adds your private datasets, and it stays on the IRIS server; the browser never sees it.
- Dark and light theme, keyboard navigation and screen reader support, and a layout that works on a phone.
- Short-lived caching of Kaggle answers, shared by all IRIS processes, and a download cache so a file is fetched once.
- A Docker image with pinned versions, and a test suite that covers the Python code, the IRIS classes, the REST API and the web app in a real browser.

## Quick start

### 1. Start IRIS

```bash
git clone https://github.com/mwaseem75/iris-kaggle-loader.git
cd iris-kaggle-loader
docker compose up -d --build --wait
```

The first build takes a few minutes. It downloads the IRIS image and installs the Python libraries for embedded Python (`pandas`, `pyarrow`, `openpyxl`). `--wait` returns when IRIS is up and the package is installed in the `USER` namespace.

The container listens on your own machine only:

| | Address |
|---|---|
| Web app | http://localhost:52773/kaggle2iris/index.html |
| Management Portal | http://localhost:52773/csp/sys/UtilHome.csp |
| SQL (DBeaver, JDBC, ODBC) | localhost:1972, namespace `USER` |

### 2. Find a dataset

Open http://localhost:52773/kaggle2iris/index.html and sign in. The address needs the `index.html` at the end.

The **Explore** page starts with the hottest datasets on Kaggle. Type `iris` in the search box. You can sort the results by **Hottest**, **Most votes**, **Recently updated** or **Newest**, and narrow them down by **File type**, **License**, **Size** and **Tag**. The search and filters are part of the page address, so you can bookmark or share a search. **Show more** loads the next 20 datasets.

Each card shows the dataset title and id (`owner/dataset`), the subtitle, its first tags and license, the total size, votes, downloads and when it was last updated.

> **[Screenshot: `docs/screenshots/explore.png`]**
> Search results for "iris" on the Explore page.

### 3. Look inside a dataset

Click **Iris Species** (`uciml/iris`). The dataset page shows the title, the subtitle, tags, license, size, votes, downloads, Kaggle's usability rating and a link to the dataset on Kaggle.

Above the tabs, **File** lists the files that can be imported, with their type and size, and **Version** lets you pick an older version of the dataset. Then use the four tabs:

- **Overview** shows the description and the dataset id, owner, version, license, number of files and size.
- **Files** lists every file of the dataset with its type and size. Files that cannot be imported (images, SQLite databases, PDFs) are shown too, greyed out. **Preview** next to a file opens it in the Preview tab.
- **Preview** shows the actual rows of the selected file, 20 per page, with the type of each column. Long texts are shortened (hover over a cell to read more). Nothing is imported into IRIS for this. The preview covers the first 1,000 rows of a file.
- **Schema** lists every column of the file next to the IRIS column name and type that an import would create.

> **[Screenshot: `docs/screenshots/dataset-preview.png`]**
> The Preview tab of `uciml/iris` with the import panel on the right.

> **[Screenshot: `docs/screenshots/dataset-schema.png`]**
> The Schema tab: the file's columns next to the planned IRIS columns.

The tabs work with the keyboard too: the arrow keys, Home and End move between them.

### 4. Plan and run an import

The **Import into IRIS** panel sits next to the tabs. Let's import a real business dataset: open `olistbr/brazilian-ecommerce` (search for "olist"), a Brazilian online shop with orders, customers, products and reviews in nine CSV files.

1. Choose **File** `olist_orders_dataset.csv`.
2. Set **Target table** to `Demo.Orders`. The default is `KG.<file name>`.
3. Set **Row limit** to `3000`. `0` imports all rows.
4. Leave **If the table exists** on **Replace**. **Append** adds rows to a table the loader created; **Fail** stops if the table exists.

The version comes from the **Version** picker above the tabs; leave it on **Latest**.

Click **Review plan**. Nothing is written yet. The plan says whether the table will be created, replaced or appended to, how many columns it gets, which version will be read and how much has to be downloaded. If the import would not be allowed, for example because the target table exists and was not created by this tool, the plan says why and **Start import** stays disabled.

Click **Start import**. The import runs in the background inside IRIS, and the panel follows it: "Looking up the dataset", "Downloading from Kaggle" with the megabytes so far, then "Writing rows to IRIS" with a progress bar. **Cancel import** stops it before the next batch of rows; a cancelled import leaves nothing behind. When it is done, the panel links to the new table.

> **[Screenshot: `docs/screenshots/import-progress.png`]**
> An import running in the background, with its progress and the Cancel button.

Under the panel, **Same import in ObjectScript or SQL** shows the one-line command for exactly this import, with copy buttons.

### 5. Import every file of a dataset

Many Kaggle datasets are a set of related tables. In the **File** list of the import panel, the last entry is **All tabular files (9)**. Choose it and the panel changes a little:

- **Target table** becomes **Target schema** (default `KG`). Each file goes into its own table, named after the file: `KG.olist_orders_dataset`, `KG.olist_customers_dataset` and so on. When two files in different folders have the same name, the second table gets `_2`.
- **Review plan** lists every file with the table it goes to and its size.
- The row limit applies to each file. Keep the default of 1000 for a first try: one Olist file, `olist_geolocation_dataset.csv`, has just over a million rows, which is more than a load without a row limit may import. For a full import of all nine files, start the container with `KAGGLE2IRIS_MAX_ROWS=2000000` (see [Configuration](#configuration)).

While it runs, the panel shows which file is being imported ("File 3 of 9, olist_order_items_dataset.csv"). The import stops at the first file that fails; the tables imported before it stay, and the result lists them.

### 6. Follow your imports

Open **Imports** in the side menu.

- **Background imports** lists the recent background imports with their state (running, ok, error, cancelled or interrupted), the file, who started them and when. Running imports show their progress and a **Cancel** button, and the page updates itself while something runs. Open a finished import to see its columns, version and encoding, or the tables of an all-files import, or the full error message.
- **Import history** lists the last 50 imports from every source: this page, the REST API, SQL and ObjectScript. Filter it by status, or by dataset, file and table name. Dataset and table names link to their pages.

> **[Screenshot: `docs/screenshots/imports.png`]**
> The Imports page with finished background imports and the import history.

### 7. Work with the imported tables

Open **Tables** in the side menu. It lists every table the loader created, with its rows, columns, source dataset and file, version and last import. Filter the list by name.

Click a table to open it:

- **Rows**: the table's rows, 50 per page.
- **Columns**: each column with its IRIS type.
- **Imports**: the imports into this table, with their status and messages.

**Copy SELECT** copies a ready-made query. **Re-import** opens the dataset page with the same file and table filled in, which is the quickest way to refresh a table.

> **[Screenshot: `docs/screenshots/table-detail.png`]**
> The `Demo.Orders` table with its rows, columns and imports.

### 8. Query the tables with SQL

The imported tables are ordinary IRIS tables, so every SQL tool can use them.

1. Open the Management Portal at http://localhost:52773/csp/sys/UtilHome.csp and log in.
2. Go to **System Explorer > SQL** and switch the namespace to **USER**.
3. Under **Tables**, filter the schema by `Demo` and click `Demo.Orders` to see its columns.
4. In **Execute Query**, run:

```sql
SELECT order_status, COUNT(*) AS orders
FROM Demo.Orders
GROUP BY order_status
ORDER BY orders DESC
```

The purchase and delivery columns arrived as text in the CSV file, but they are real `TIMESTAMP` columns in IRIS, so date functions work:

```sql
SELECT TOP 10 order_id,
       DATEDIFF('day', order_purchase_timestamp, order_delivered_customer_date) AS days_to_deliver
FROM Demo.Orders
WHERE order_delivered_customer_date IS NOT NULL
ORDER BY days_to_deliver DESC
```

After an all-files import you can join the tables. With a row limit each file is cut on its own, so a join finds only a few matches; import without a limit to get the real numbers:

```sql
SELECT TOP 10 c.customer_state, COUNT(*) AS orders
FROM KG.olist_orders_dataset o
JOIN KG.olist_customers_dataset c ON c.customer_id = o.customer_id
GROUP BY c.customer_state
ORDER BY orders DESC
```

The loader cleans column names for SQL: names that are reserved words get a `_` at the end (in `uciml/iris`, `Id` becomes `Id_`), spaces and symbols become `_`, and accents are removed (`Café` becomes `Cafe`).

Text columns compare exactly, so `'delivered'` and `'Delivered'` are different values. For a case-insensitive search use `%SQLUPPER`:

```sql
SELECT COUNT(*) FROM Demo.Orders WHERE %SQLUPPER(order_status) = %SQLUPPER('DELIVERED')
```

### 9. Import from an IRIS terminal

```bash
docker compose exec iris iris session iris -U USER
```

```objectscript
USER>do ##class(dc.kaggle2iris.Loader).Load("uciml/iris","Iris.csv","Demo.Iris")

Fetching uciml/iris / Iris.csv (version 2)
Created Demo.Iris (6 columns)
  150/150 rows

Loaded 150 rows into Demo.Iris in .35s (version 2)
```

With only a dataset and a file, the table is `KG.<file name>`:

```objectscript
do ##class(dc.kaggle2iris.Loader).Load("uciml/iris","Iris.csv")
```

The arguments of `Load`:

| Argument | Default | Meaning |
|---|---|---|
| dataset | | Kaggle id, for example `uciml/iris` |
| file | | file in the dataset, for example `Iris.csv` or `data/train.csv` |
| table | `KG.<file name>` | target table, `Schema.Table` |
| limit | `0` | maximum number of rows; `0` means all |
| mode | `replace` | `replace`, `append`, or `fail` when the table exists |
| version | `""` | dataset version number; empty means the latest |
| sheet | `""` | Excel sheet; empty means the first one |
| result | | output: the result as a `%DynamicObject` |

`Load` returns a `%Status`. To import every tabular file of a dataset, one table per file, use `LoadAll(dataset, schema, limit, mode, version, .result)`:

```objectscript
do ##class(dc.kaggle2iris.Loader).LoadAll("olistbr/brazilian-ecommerce","Shop",1000)
```

To see the title, versions and files of a dataset before loading anything:

```objectscript
write ##class(dc.kaggle2iris.Loader).Info("uciml/iris").%ToJSON()
```

### 10. Import from SQL

The loader is also available as two SQL functions, so any SQL client can import: the Management Portal, DBeaver, JDBC, ODBC or Python DB-API. The client needs neither Python nor a Kaggle library.

```sql
SELECT dc_kaggle2iris.load_dataset('uciml/iris', 'Iris.csv', 'Demo.Iris')

SELECT dc_kaggle2iris.dataset_info('uciml/iris')
```

The arguments of `load_dataset` are the same as in the table above, in the same order; leave out trailing ones or pass `NULL`. It returns the result as JSON with `"status":"OK"`. A failed import does not raise an SQL error; it returns `{"status":"ERROR","error":"..."}`, which is easier to handle in scripts. If you mistype a file name, the error lists the files you can import.

### 11. File types, Excel sheets and versions

| Type | Extensions | How it is read |
|---|---|---|
| CSV | `.csv` | Every column is typed from all its values (see [How it works](#how-it-works)) |
| TSV | `.tsv`, `.tab` | Like CSV, with tabs |
| Parquet | `.parquet`, `.pq` | Keeps the file's own types |
| JSON | `.json` | A list of records, or an object holding one list of records |
| JSON Lines | `.jsonl`, `.ndjson` | One record per line |
| Excel | `.xlsx`, `.xlsm` | One sheet per import, the first one by default |

Other files, like images, `.sqlite` databases, `.arff` or `.feather`, are listed but cannot be imported.

An Excel file with several sheets shows a **Sheet** picker after its first preview, and the import panel has a **Sheet** field. In ObjectScript and SQL the sheet is the last argument:

```sql
SELECT dc_kaggle2iris.load_dataset('muratkokludataset/raisin-dataset',
       'Raisin_Dataset/Raisin_Dataset.xlsx', 'Demo.Raisin', 0, NULL, NULL, 'Raisin_Dataset')
```

Datasets change over time. When you need the same rows every time, for a demo, a test or a report, pass a version number:

```sql
SELECT dc_kaggle2iris.load_dataset('uciml/iris', 'Iris.csv', 'Demo.IrisV1', 0, NULL, '1')
```

The result then says `"pinned": true`. Every import, pinned or not, records the version it read; you see it in the result, on the Imports and Tables pages and in `dc_kaggle2iris.ImportLog`. Kaggle only offers single files of the latest version, so an older version downloads the version's whole archive once and takes the file from it. The plan tells you when that happens.

### 12. Big files

Kaggle has files of many gigabytes. Two things keep them manageable:

- **Previews and row limits read only the start of a file.** For CSV, TSV and JSON Lines the loader downloads just enough of the file for the rows it needs and stops. Kaggle sends large files zipped; they are unzipped while they stream in. Try it with `mkechinov/ecommerce-behavior-data-from-multi-category-store` and its 8 GB file `2019-Nov.csv`: the preview opens in a few seconds, and an import with a row limit of 10,000 takes about as long.
- **Full loads have limits.** Without a row limit the loader refuses files over 500 MB or over 1,000,000 rows (see [Configuration](#configuration)), and the plan says so before anything is downloaded.

Parquet, JSON and Excel files have to be read completely. They are previewed up to 200 MB.

Downloaded files are kept in the container under `/home/irisowner/.cache/kaggle2iris`, so importing the same file again does not download it again. Delete that folder to free the space.

### 13. Private datasets

Public datasets need no account. For your own private datasets, create an API token in the settings of your Kaggle account and start the container with it:

```bash
KAGGLE_API_TOKEN=your-token docker compose up -d --wait
```

In PowerShell:

```powershell
$env:KAGGLE_API_TOKEN = "your-token"
docker compose up -d --wait
```

The older `kaggle.json` style works too: set `KAGGLE_USERNAME` and `KAGGLE_KEY` instead.

The status badge in the top bar then says **Kaggle token set**. Kaggle answers the same way for a dataset that does not exist and for a private one you cannot see, so without a token the error for both says "Dataset not found, or private".

The token is only passed into the container when it is set. All Kaggle requests go out from IRIS, so the token never reaches the browser. Downloads are redirected by Kaggle to a Google Cloud Storage address; the token is not sent there. It is replaced by `***` if it ever appears in an error message or the import log.

### 14. The REST API

The web app is built on a REST API that you can use directly. It uses basic authentication. [API.md](API.md) describes every endpoint with its parameters, responses and error codes.

```bash
# search and look inside datasets
curl -u _SYSTEM:SYS "http://localhost:52773/api/kaggle2iris/datasets?search=olist&sort=votes"
curl -u _SYSTEM:SYS "http://localhost:52773/api/kaggle2iris/dataset?id=olistbr/brazilian-ecommerce"
curl -u _SYSTEM:SYS "http://localhost:52773/api/kaggle2iris/dataset/preview?id=uciml/iris&file=Iris.csv&rows=5"

# plan and start a background import, then follow it
curl -u _SYSTEM:SYS -X POST -H "Content-Type: application/json" \
     -d '{"dataset":"uciml/iris","file":"Iris.csv","table":"Demo.Iris"}' \
     http://localhost:52773/api/kaggle2iris/plan
curl -u _SYSTEM:SYS -X POST -H "Content-Type: application/json" \
     -d '{"dataset":"uciml/iris","file":"Iris.csv","table":"Demo.Iris"}' \
     http://localhost:52773/api/kaggle2iris/jobs
curl -u _SYSTEM:SYS http://localhost:52773/api/kaggle2iris/jobs/1

# every file of a dataset, one table each in schema Shop
curl -u _SYSTEM:SYS -X POST -H "Content-Type: application/json" \
     -d '{"dataset":"olistbr/brazilian-ecommerce","file":"*","table":"Shop","limit":1000}' \
     http://localhost:52773/api/kaggle2iris/jobs

# history and tables
curl -u _SYSTEM:SYS http://localhost:52773/api/kaggle2iris/history
curl -u _SYSTEM:SYS http://localhost:52773/api/kaggle2iris/tables
curl -u _SYSTEM:SYS "http://localhost:52773/api/kaggle2iris/preview/Demo.Iris?rows=5&offset=0"
```

`POST /load` also imports, but it answers only when the import is finished. Use `/jobs` for anything that takes longer than a few seconds: the web gateway in front of IRIS gives up on a request after 60 seconds, while a background job keeps running.

### 15. Python

Inside IRIS embedded Python (a `Language = python` method, or `irispython`) the package can be used directly:

```python
import kaggle2iris
r = kaggle2iris.load("uciml/iris", "Iris.csv", "Demo.Iris")
print(r["rows"], r["columns"])
```

The readers work on their own too, for a file you already have:

```python
from kaggle2iris import readers
table, info = readers.read("/path/to/sales.csv", "csv")
```

To load a pandas DataFrame or a pyarrow Table you already have:

```python
from kaggle2iris.loader import IrisSQL, load_dataframe
load_dataframe(df, "Demo.Mine", IrisSQL())
```

### 16. Themes and phones

The button in the top bar switches between the dark and the light theme; the choice is remembered. Both themes meet the WCAG contrast guidelines.

> **[Screenshot: `docs/screenshots/light-theme.png`]**
> A dataset page in the light theme.

On a phone the side menu becomes a bar under the top bar, results show in one column, and the import panel moves below the dataset with a **Jump to import** button.

> **[Screenshot: `docs/screenshots/phone-explore.png`]**
> The Explore page on a phone. This image is tall; show it at about 260 pixels wide.

## Installing with IPM

On your own IRIS (2023.1 or newer, with embedded Python), once the package is published on Open Exchange:

```objectscript
zpm "install iris-kaggle-loader"
```

Until then, or to install your own changes, load it from a clone of this repository:

```objectscript
zpm "load /path/to/iris-kaggle-loader"
```

IPM installs the Python libraries from `requirements.txt`, the classes, the REST application `/api/kaggle2iris` and the web app `/kaggle2iris/index.html`. The official `kaggle` Python package is not needed; the loader talks to Kaggle's REST API itself.

## Configuration

Set these in your shell before `docker compose up`; they are passed into the container when set.

| Variable | Default | Effect |
|---|---|---|
| `KAGGLE_API_TOKEN` | | Kaggle API token, for private datasets |
| `KAGGLE_USERNAME`, `KAGGLE_KEY` | | the older way to give Kaggle credentials |
| `KAGGLE2IRIS_MAX_ROWS` | 1000000 | the most rows a load without a row limit may import |
| `KAGGLE2IRIS_MAX_DOWNLOAD_MB` | 500 | the largest download a load may start (and the most a row-limited load reads) |
| `KAGGLE2IRIS_PREVIEW_MAX_MB` | 200 | the largest Parquet, JSON or Excel file that is previewed |
| `KAGGLE2IRIS_CACHE_TTL` | 600 | seconds Kaggle answers are cached (search: at most 300); `0` turns the cache off |
| `KAGGLE2IRIS_MAX_JOBS` | 2 | how many background imports may run at the same time |
| `KAGGLE2IRIS_SHOW_PRIVATE` | 0 | `1` lists your token's private datasets in search results |
| `KAGGLE2IRIS_EXACT` | 1 | `0` creates text columns with the IRIS default collation instead of exact |
| `IRIS_WEB_PORT`, `IRIS_SQL_PORT` | 52773, 1972 | host ports of the container |

`KAGGLE2IRIS_CACHE_DIR` sets where downloaded files are kept; the Docker image uses `/home/irisowner/.cache/kaggle2iris`.

## How it works

1. The browser talks only to the IRIS REST API (`dc.kaggle2iris.REST`). All Kaggle requests go out from IRIS.
2. Discovery (`dc.kaggle2iris.Kaggle`, Python `kaggle2iris.kaggle`) uses Kaggle's public REST API (`https://www.kaggle.com/api/v1/`) for search, dataset details, versions and file lists. Answers are cached in IRISTEMP for a few minutes. Only `www.kaggle.com` and the storage host that downloads redirect to are ever contacted.
3. Previews download the start of a file (CSV, TSV, JSON Lines) or the whole file (Parquet, JSON, Excel up to 200 MB), read it with `kaggle2iris.readers` and return the first rows with their types.
4. A plan (`kaggle2iris.plan`) checks the dataset, file, version and target table, and computes the table definition from a sample of rows, without writing anything.
5. An import (`dc.kaggle2iris.Loader`, Python `kaggle2iris.loader`) reads the file into an Arrow table, reading only the needed start of the file when there is a row limit, maps each column to an IRIS type, creates the table and inserts the rows in batches inside one transaction.
6. Background imports (`dc.kaggle2iris.Job`) run the same import in their own IRIS process and report their progress, so the web request returns at once.
7. Every import is recorded in `dc_kaggle2iris.ImportLog`.

How CSV values are typed. A column gets a type only if every value in it fits; otherwise it stays text:

| Values in the column | IRIS type |
|---|---|
| whole numbers without leading zeros (`42`, `-7`) | `BIGINT` |
| numbers with a decimal point or exponent (`3.14`, `1e-5`) | `DOUBLE` |
| `true` / `false` | `BIT` |
| dates (`2024-01-15`) | `DATE` |
| timestamps (`2024-01-15 10:20:30`, with `T`, `Z` or an offset) | `TIMESTAMP` (UTC) |
| anything else, including `01003` | `VARCHAR(n)` |

Empty cells are NULL, and so are `NA`, `N/A`, `NaN`, `null` and `None` in a column that is otherwise numeric. Parquet columns map the same way from their own types; lists and structs become JSON text. Text longer than 1,000,000 characters goes into `LONGVARCHAR`. `n` is the length of the longest value, rounded up to a multiple of 1000 and at least 4000.

## Safety and limits

- **Existing tables are safe.** `replace` and `append` only touch tables the loader created. Any other existing table makes the import stop without changing it.
- **Imports are all or nothing.** If IRIS rejects a row, the error names it and nothing of that import stays behind: a new table is dropped again, rows appended to an existing table are rolled back. A failed `replace` leaves no table, because the old one was already dropped. A CSV line with the wrong number of fields stops the import with its line number.
- **One import per table.** Two imports never write to the same table at the same time.
- **Background imports survive the browser.** Closing the page does not stop them. If IRIS restarts during one, it is marked "interrupted" and its transaction is rolled back.
- **Full loads have limits.** Without a row limit, the loader refuses files over 1,000,000 rows or downloads over 500 MB (see [Configuration](#configuration)).
- **The browser never sees your token**, the token is never sent to the download host, and only tables created by the loader can be previewed through the API.

## Running the tests

Everything at once, against the running container:

```bash
tests/run_all.sh                  # changes no data
tests/run_all.sh --with-imports   # also imports real Kaggle files, then removes everything it added
```

Or one part at a time:

```bash
pip install pandas pyarrow openpyxl pytest
pytest tests                          # Python logic with SQLite and a fake Kaggle: no IRIS, no network
node --test tests/ui/state.test.mjs   # UI helpers, contrast of both themes
node tests/ui/mocked.mjs              # import monitoring in the UI against a fake API

tests/run_iris_tests.sh               # the Python tests inside IRIS, plus tests against real IRIS SQL
tests/smoke.sh discovery              # every REST endpoint against live Kaggle, read-only
tests/smoke.sh import                 # real imports of every file type and background jobs; drops its tables
node tests/ui/live.mjs                # the web pages against the server
node tests/ui/browser.mjs             # the app in headless Chrome
```

If the container runs on other ports, set `IRIS_WEB_PORT` (and `IRIS_SQL_PORT`) for the scripts too. The browser check starts Chrome headless with an empty, throwaway profile and only opens the local app. It covers sign-in, search, the dataset tabs, navigation, the theme, the phone layout and console errors; `--screenshots` saves fresh images to `docs/screenshots`. You need Node 24, and Chrome for that check. The scripts are bash; on Windows run them from Git Bash. GitHub Actions runs the offline tests on every push.

## Troubleshooting

**"Not Found" at http://localhost:52773/kaggle2iris/.** Add `index.html`: http://localhost:52773/kaggle2iris/index.html.

**The page says "Unauthorized".** Reload with Ctrl+F5 and sign in. If it keeps happening, click **Sign out** in the top bar and sign in again.

**The page looks outdated after an update.** IRIS lets the browser keep static files for an hour. Reload with Ctrl+F5.

**I changed the code and nothing happened.** In the `USER` namespace run `zpm "load /home/irisowner/dev/ -v -bypass-py-deps"`, then `docker compose restart`. The web server processes keep the old Python code in memory until they restart. `-bypass-py-deps` keeps IPM from reinstalling the Python libraries, which would replace the pinned versions of the image.

**"Dataset not found, or private".** Check the id on kaggle.com: it is the `owner/dataset` part of the address. If it is your private dataset, start the container with `KAGGLE_API_TOKEN`.

**"Line 1234 has 7 fields, the header has 6".** The CSV file is broken at that line, usually an unquoted comma. Fix the file on Kaggle, or import the rows before it with a row limit.

**An import is slow.** Use a row limit first, and use the background import (the web page always does). The second import of the same file uses the downloaded copy.

**"Kaggle rate limit reached."** Wait a minute. Requests with `KAGGLE_API_TOKEN` may get a higher limit.

## Project layout

```
python/kaggle2iris/     Python package: kaggle.py (Kaggle API, downloads, previews),
                        readers.py (CSV, TSV, Parquet, JSON, Excel), loader.py (types, insert),
                        plan.py (import plans and imports)
src/dc/kaggle2iris/     Loader.cls (ObjectScript and SQL entry points), Kaggle.cls, Job.cls
                        (background imports), Tables.cls, ImportLog.cls, REST.cls
web/                    the web app: index.html, app.css, js/ (no framework, no build step)
tests/                  Python tests, UI tests (tests/ui), run_all.sh and the other scripts
docs/screenshots/       images for this README
module.xml              IPM package
Dockerfile, docker-compose.yml, iris.script
requirements.txt        Python libraries for IPM installs
constraints.txt         exact versions used in the Docker image
API.md                  REST API reference
```

The Docker image is pinned to IRIS 2026.1.0.234 and to the package versions in `constraints.txt`, the versions everything was tested with.

## License

MIT. See [LICENSE](LICENSE).
