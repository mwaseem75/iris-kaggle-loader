"""Tests against real IRIS SQL (no dataset downloads).

Run inside the container with tests/run_iris_tests.sh; outside IRIS they are skipped.
Tables are created in schema KGTest and dropped again.
"""
import datetime
import decimal
import io
import json
import os
import sys
import urllib.parse

import pytest

try:
    import iris
    if not hasattr(iris, "sql"):          # the Native API package, not embedded Python
        raise ImportError
except ImportError:
    pytest.skip("needs IRIS embedded Python (run tests/run_iris_tests.sh)", allow_module_level=True)

import pyarrow as pa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from kaggle2iris.loader import IrisSQL, LoadError, load_dataframe  # noqa: E402

QUIET = lambda *_: None   # noqa: E731


def rows(sql, *args):
    return [list(r) for r in iris.sql.exec(sql, *args)]


@pytest.fixture
def db():
    created = []
    db = IrisSQL()
    yield db, created
    for t in created:
        iris.sql.exec(f"DROP TABLE IF EXISTS {t}")
        db.unmark_owned(t)


def test_values_round_trip(db):
    db, created = db
    created.append("KGTest.Types")
    t = pa.table({
        "n": pa.array([1, None], pa.int64()),
        "f32": pa.array([1.4, None], pa.float32()),
        "s": pa.array(["", None]),
        "d": pa.array([datetime.date(1700, 6, 1), None]),
        "ts": pa.array([datetime.datetime(2024, 1, 15, 10, 20, 30, 123456), None]),
        "dec": pa.array([decimal.Decimal("-0.001"), None], pa.decimal128(10, 3)),
        "emoji": pa.array(["\U0001F917 café", None]),
    })
    r = load_dataframe(t, "KGTest.Types", db, log=QUIET)
    assert "dec_" in r["columns"]                                   # DEC is reserved in IRIS
    got = rows("SELECT n, f32, s, LENGTH(s), CASE WHEN s IS NULL THEN 1 ELSE 0 END, "
               "{fn CONVERT(d, SQL_VARCHAR)}, emoji FROM KGTest.Types ORDER BY %ID")
    assert got[0][:2] == [1, 1.4]                                    # float32 1.4 stays 1.4
    assert got[0][3:5] == [0, 0]                                     # empty string, not NULL
    assert got[0][5] == "1700-06-01"
    assert got[0][6] == "\U0001F917 café"
    assert got[1][0] in (None, "") and got[1][4] == 1               # NULL stays NULL
    ts = rows("SELECT %EXTERNAL(ts) FROM KGTest.Types WHERE n = 1")[0][0]
    assert str(ts).startswith("2024-01-15 10:20:30.123456")


def test_exact_collation(db):
    db, created = db
    created.append("KGTest.Coll")
    load_dataframe(pa.table({"v": ["Iris-setosa", "IRIS-SETOSA", "iris-setosa "]}), "KGTest.Coll", db, log=QUIET)
    assert rows("SELECT COUNT(DISTINCT v) FROM KGTest.Coll") == [[3]]
    assert rows("SELECT COUNT(*) FROM KGTest.Coll WHERE %SQLUPPER(v) = %SQLUPPER('iris-setosa')") == [[3]]


def test_foreign_table_untouched(db):
    db, created = db
    created.append("KGTest.People")
    iris.sql.exec("CREATE TABLE KGTest.People (Name VARCHAR(50))")
    iris.sql.exec("INSERT INTO KGTest.People (Name) VALUES ('Ann')")
    for mode in ("replace", "append"):
        with pytest.raises(LoadError, match="not created by kaggle2iris"):
            load_dataframe(pa.table({"x": [1]}), "KGTest.People", db, mode=mode, log=QUIET)
    assert rows("SELECT Name FROM KGTest.People") == [["Ann"]]


def test_reserved_table_name(db):
    db, created = db
    created.append("KGTest.Foreign_")
    r = load_dataframe(pa.table({"x": [1]}), "KGTest.Foreign", db, log=QUIET)
    assert r["table"] == "KGTest.Foreign_"


def test_rejected_row_rolls_back_append(db):
    db, created = db
    created.append("KGTest.Grow")
    load_dataframe(pa.table({"v": ["short"]}), "KGTest.Grow", db, log=QUIET)       # VARCHAR(4000)
    too_long = pa.table({"v": ["ok", "x" * 5000]})
    with pytest.raises(LoadError, match="Row 2 was rejected by IRIS.*rolled back"):
        load_dataframe(too_long, "KGTest.Grow", db, mode="append", log=QUIET)
    assert rows("SELECT v FROM KGTest.Grow") == [["short"]]                         # "ok" undone too


def test_failure_inside_caller_transaction(db):
    db, created = db
    created += ["KGTest.Caller", "KGTest.Grow2"]
    iris.sql.exec("CREATE TABLE KGTest.Caller (v VARCHAR(10))")
    load_dataframe(pa.table({"v": ["short"]}), "KGTest.Grow2", db, log=QUIET)
    iris.sql.exec("START TRANSACTION")
    iris.sql.exec("INSERT INTO KGTest.Caller (v) VALUES ('mine')")
    with pytest.raises(LoadError):
        load_dataframe(pa.table({"v": ["x" * 5000]}), "KGTest.Grow2", db, mode="append", log=QUIET)
    assert iris.execute("return $TLEVEL") == 1                                      # caller still in its transaction
    assert rows("SELECT v FROM KGTest.Caller") == [["mine"]]                        # and its row survived
    iris.sql.exec("ROLLBACK")


def test_wide_row_fits(db):
    db, created = db
    created.append("KGTest.Wide")
    big = "x" * 900_000
    r = load_dataframe(pa.table({f"c{i}": [big] for i in range(4)}), "KGTest.Wide", db, log=QUIET)
    assert list(r["columns"].values()).count("LONGVARCHAR") == 1
    assert rows("SELECT COUNT(*) FROM KGTest.Wide") == [[1]]


@pytest.fixture
def no_trace(monkeypatch, tmp_path):
    """Deletes the ImportLog and Job rows a test creates and its KGTest tables. Kaggle
    answers are cached in memory only, so fakes never reach the shared cache global."""
    from kaggle2iris import kaggle
    monkeypatch.setattr(kaggle, "_store", kaggle.MemoryCache())
    monkeypatch.setattr(kaggle, "CACHE_DIR", str(tmp_path / "cache"))
    log = rows("SELECT COALESCE(MAX(ID), 0) FROM dc_kaggle2iris.ImportLog")[0][0]
    job = rows("SELECT COALESCE(MAX(ID), 0) FROM dc_kaggle2iris.Job")[0][0]
    yield
    for sql, last in [("DELETE FROM dc_kaggle2iris.ImportLog WHERE ID > ? AND (Dataset = '' OR Dataset IS NULL "
                       "OR Dataset LIKE 'kgtest/%')", log),
                      ("DELETE FROM dc_kaggle2iris.Job WHERE ID > ? AND Dataset LIKE 'kgtest/%'", job)]:
        try:
            iris.sql.exec(sql, last)
        except Exception as e:     # iris.sql raises a blank SQLError when nothing was deleted
            if str(e).strip():
                raise
    for (t,) in rows("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'KGTest'"):
        iris.sql.exec(f"DROP TABLE KGTest.{t}")
        IrisSQL().unmark_owned(f"KGTest.{t}")


def test_sql_functions_report_errors(no_trace):
    r = json.loads(rows("SELECT dc_kaggle2iris.load_dataset('')")[0][0])
    assert r == {"status": "ERROR", "error": "dataset is required"}
    r = json.loads(rows("SELECT dc_kaggle2iris.load_dataset('kgtest/fake')")[0][0])
    assert r == {"status": "ERROR", "error": "file is required"}
    r = json.loads(rows("SELECT dc_kaggle2iris.dataset_info(NULL)")[0][0])
    assert r == {"error": "dataset is required"}


def test_cache_on_real_global():
    from kaggle2iris import kaggle
    store = kaggle.GlobalCache("^CacheTemp.kaggle2iristest")
    try:
        store.set("k1", "9999999999\t{}")
        store.set("k2", "1\t{}")
        assert store.get("k1") == "9999999999\t{}" and store.get("nope") is None
        assert sorted(store.keys()) == ["k1", "k2"]
        assert kaggle._read(store, "k2", 100) is None and store.get("k2") is None   # expired, removed
        assert iris.cls("%SYS.Namespace").GetGlobalDest("USER", "CacheTemp.kaggle2iris").endswith("iristemp/")
    finally:
        iris.gref("^CacheTemp.kaggle2iristest").kill([])


def test_kaggle_wrappers_return_coded_errors():
    k = iris.cls("dc.kaggle2iris.Kaggle")
    assert json.loads(k.Details("a b"))["code"] == "bad_request"
    assert json.loads(k.Preview("a/b", ""))["code"] == "bad_request"
    assert json.loads(k.Search("", "random"))["code"] == "bad_request"
    status = json.loads(k.Status())
    assert isinstance(status["credentials"], bool) and status["iris"].startswith("20")
    for name in ("KAGGLE_API_TOKEN", "KAGGLE_KEY"):
        secret = os.environ.get(name)
        assert not secret or secret not in json.dumps(status)


# ------------------------------------------------------- background jobs ---
JOB = iris.cls("dc.kaggle2iris.Job")
REF = "kgtest/fake"


def csv_rows(n):
    return b"text,label\n" + b"".join(b"row %d,%d\n" % (i, i % 2) for i in range(n))


def fake_kaggle(monkeypatch, files=None, on_download=None):
    """Make kaggle2iris read canned Kaggle answers instead of the network (same process only)."""
    from kaggle2iris import kaggle
    files = files or {"data.csv": csv_rows(2500)}
    view = {"ref": REF, "title": "Fake", "currentVersionNumber": 3, "totalBytes": 1000,
            "versions": [{"versionNumber": 3, "creationDate": "2026-01-01", "status": "Ready"}]}
    listing = {"datasetFiles": [{"name": n, "totalBytes": len(b)} for n, b in files.items()]}

    def answer(url, headers=None, timeout=None):
        path = url[len(kaggle.API):]
        if path.startswith(f"datasets/view/{REF}"):
            body = view
        elif path.startswith(f"datasets/list/{REF}"):
            body = listing
        elif path.startswith(f"datasets/download/{REF}/"):
            if on_download:
                on_download()
            body = files[urllib.parse.unquote(path.split(f"{REF}/", 1)[1])]
        else:
            raise kaggle.KaggleError("not_found", "Not found on Kaggle.")
        return io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
    monkeypatch.setattr(kaggle, "open_url", answer)


def new_job(table, file="data.csv", state="queued", cancel=0, dataset=REF):
    iris.sql.exec("INSERT INTO dc_kaggle2iris.Job (Dataset, FileName, TableName, Mode, RowLimit, State, "
                  "CancelRequested, CreatedAt) VALUES (?, ?, ?, 'replace', 0, ?, ?, CURRENT_TIMESTAMP)",
                  dataset, file, table, state, cancel)
    return str(rows("SELECT MAX(ID) FROM dc_kaggle2iris.Job")[0][0])


def wait_for(job_id, states=("ok", "error", "cancelled", "interrupted"), seconds=20):
    import time
    end = time.time() + seconds
    while time.time() < end:
        job = json.loads(JOB.Get(job_id))
        assert "state" in job, job
        if job["state"] in states:
            return job
        time.sleep(0.3)
    raise AssertionError(f"job {job_id} did not finish: {job}")


def test_job_runs_and_records(no_trace, monkeypatch):
    fake_kaggle(monkeypatch)
    job_id = new_job("KGTest.JobOk")
    JOB.Run(job_id)                                   # in this process, so the fake Kaggle applies
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "ok" and job["rowsDone"] == job["rowsTotal"] == 2500
    assert job["result"]["rows"] == 2500 and job["result"]["version"] == 3 and job["result"]["file"] == "data.csv"
    assert (job["filesDone"], job["filesTotal"], job["currentFile"]) == (1, 1, None)
    assert job["phase"] is None and job["finishedAt"] and isinstance(job["elapsed"], int)
    log = rows("SELECT Status, RowCount, TableName, FileName, Version, Encoding FROM dc_kaggle2iris.ImportLog WHERE ID = ?",
               job["importLogId"])
    assert [str(v) for v in log[0]] == ["OK", "2500", "KGTest.JobOk", "data.csv", "3", "utf-8"]
    assert rows("SELECT COUNT(*), MAX(label_) FROM KGTest.JobOk") == [[2500, 1]]


def test_job_for_all_files(no_trace, monkeypatch):
    fake_kaggle(monkeypatch, files={"a/sales.csv": csv_rows(10), "b/sales.csv": csv_rows(20), "notes.txt": b"hi"})
    job_id = new_job("KGTest", file="*")
    JOB.Run(job_id)
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "ok" and (job["filesDone"], job["filesTotal"]) == (2, 2)
    assert [(t["table"], t["rows"]) for t in job["result"]["tables"]] == [("KGTest.sales", 10), ("KGTest.sales_2", 20)]
    assert job["result"]["rows"] == 30
    assert rows("SELECT COUNT(*) FROM KGTest.sales_2") == [[20]]


def test_all_files_job_stops_at_a_bad_file(no_trace, monkeypatch):
    fake_kaggle(monkeypatch, files={"a.csv": csv_rows(5), "b.csv": b"x,y\n1,2,3\n", "c.csv": csv_rows(5)})
    job_id = new_job("KGTest", file="*")
    JOB.Run(job_id)
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "error" and job["error"].startswith("b.csv: Line 2 has 3 fields")
    assert [t["table"] for t in job["result"]["tables"]] == ["KGTest.a"]       # the tables before it stay
    assert rows("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'KGTest' AND TABLE_NAME = 'c'") == [[0]]


def test_job_progress_and_cancel_flag(no_trace):
    job_id = new_job("KGTest.JobP")
    JOB.Progress(job_id, "inserting", 1000, 2500)
    job = json.loads(JOB.Get(job_id))
    assert (job["phase"], job["rowsDone"], job["rowsTotal"]) == ("inserting", 1000, 2500)
    assert JOB.IsCancelled(job_id) == 0
    assert json.loads(JOB.Cancel(job_id))["cancelRequested"] is True
    assert JOB.IsCancelled(job_id) == 1


def test_job_cancelled_before_start(no_trace):
    job_id = new_job("KGTest.JobC", cancel=1)
    JOB.Run(job_id)
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "cancelled" and "before it started" in job["error"]
    assert rows("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'KGTest' AND TABLE_NAME = 'JobC'") == [[0]]


def test_job_cancelled_while_fetching(no_trace, monkeypatch):
    job_id = new_job("KGTest.JobF")
    # another process asks to cancel while the file is being downloaded
    fake_kaggle(monkeypatch, on_download=lambda: iris.sql.exec(
        "UPDATE dc_kaggle2iris.Job SET CancelRequested = 1 WHERE ID = ?", job_id))
    JOB.Run(job_id)
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "cancelled" and "cancelled" in job["error"].lower()
    assert rows("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'KGTest' AND TABLE_NAME = 'JobF'") == [[0]]


def test_background_job_and_table_lock(no_trace):
    """Start() spawns a real background process; it stops at the table lock this test
    holds, before any network access."""
    iris.execute('lock +^dc.kaggle2iris.Table("KGTEST.LOCKED")')
    try:
        started = json.loads(JOB.Start(iris.cls("%DynamicObject")._FromJSON(
            json.dumps({"dataset": REF, "file": "data.csv", "table": "KGTest.Locked", "limit": 5}))))
        assert started["state"] in ("queued", "running", "error") and started["table"] == "KGTest.Locked"
        job = wait_for(started["id"])
        assert job["state"] == "error" and "Another import into KGTest.Locked is running" in job["error"]
        assert job["startedAt"] and job["importLogId"]
        # normal imports take the same lock in Loader.Run; IRIS locks are re-entrant within a
        # process, so that cannot be shown from this process
    finally:
        iris.execute('lock -^dc.kaggle2iris.Table("KGTEST.LOCKED")')


def test_start_checks_first(no_trace):
    start = lambda **kw: json.loads(JOB.Start(iris.cls("%DynamicObject")._FromJSON(json.dumps(kw))))
    assert start(dataset="a b", file="x.csv")["code"] == "bad_request"
    assert start(dataset=REF)["code"] == "bad_request"                       # no file
    assert start(dataset=REF, file="x.csv", mode="zap")["code"] == "bad_request"
    assert start(dataset=REF, file="*", table="KGTest.T")["code"] == "bad_request"   # all files: a schema
    iris.sql.exec("CREATE TABLE KGTest.Theirs (a INT)")
    assert start(dataset=REF, file="x.csv", table="KGTest.Theirs")["code"] == "conflict"
    ids = [new_job("KGTest.Q1"), new_job("KGTest.Q2")]           # two young queued jobs
    busy = start(dataset=REF, file="x.csv", table="KGTest.Q3")
    assert busy["code"] == "busy" and "imports are already running" in busy["error"]
    for job_id in ids:
        iris.sql.exec("UPDATE dc_kaggle2iris.Job SET State = 'error' WHERE ID = ?", job_id)


def test_interrupted_and_finished_jobs(no_trace):
    job_id = new_job("KGTest.Gone", state="running")              # no process holds its lock
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "interrupted" and "ended unexpectedly" in job["error"]
    assert json.loads(JOB.Cancel(job_id))["code"] == "conflict"
    assert json.loads(JOB.Get("999999999"))["code"] == "not_found"
    listed = json.loads(JOB.List(5))
    assert listed["count"] <= 5 and listed["items"][0]["id"] == job_id


def test_plan_and_files_in_iris(no_trace, monkeypatch):
    fake_kaggle(monkeypatch)
    loader_cls = iris.cls("dc.kaggle2iris.Loader")
    iris.sql.exec("CREATE TABLE KGTest.Theirs (a INT)")
    body = lambda **kw: iris.cls("%DynamicObject")._FromJSON(json.dumps(kw))
    r = json.loads(loader_cls.Plan(body(dataset=REF, file="data.csv", table="KGTest.Theirs")))
    assert r["action"] == "refused" and "not created by kaggle2iris" in r["reason"]
    r = json.loads(loader_cls.Plan(body(dataset=REF, file="data.csv", table="KGTest.Planned")))
    assert r["action"] == "create" and [c["name"] for c in r["columns"]] == ["text_", "label_"]
    assert "COLLATE %EXACT" in r["ddl"]
    assert rows("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'KGTest' AND TABLE_NAME = 'Planned'") == [[0]]
    r = json.loads(loader_cls.FilesPy(json.dumps({"dataset": REF, "schema": "KGTest"})))
    assert r["files"] == [{"file": "data.csv", "table": "KGTest.data", "size": len(csv_rows(2500)), "format": "csv"}]


def test_tables_list_and_detail(no_trace):
    load_dataframe(pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]}), "KGTest.Listed", IrisSQL(), log=QUIET)
    iris.sql.exec("CREATE TABLE KGTest.NotOurs (a INT)")
    tables = iris.cls("dc.kaggle2iris.Tables")
    items = json.loads(tables.List())["items"]
    names = [t["table"] for t in items]
    assert "KGTest.Listed" in names and "KGTest.NotOurs" not in names
    item = next(t for t in items if t["table"] == "KGTest.Listed")
    assert (item["rows"], item["columnCount"], item["dataset"], item["file"]) == (3, 2, None, None)
    d = json.loads(tables.Detail("kgtest.listed"))                      # case-insensitive
    assert d["table"] == "KGTest.Listed"
    assert d["columns"] == [{"name": "a", "type": "BIGINT"}, {"name": "b", "type": "VARCHAR(4000)"}]
    for bad in ("KGTest.NotOurs", "x;drop", "KGTest.Missing"):
        assert json.loads(tables.Detail(bad))["code"] == "not_found", bad
