"""Import plans and imports of Kaggle files, with Kaggle faked and SQLite standing in for IRIS."""
import json
import os
import sys

import pyarrow as pa
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.dirname(__file__))
from kaggle2iris import kaggle, loader, plan
from kaggle2iris.kaggle import KaggleError
from kaggle2iris.loader import LimitExceeded
from test_kaggle import VIEW, fake, iris_routes, zipped
from test_loader import SqliteDB

quiet = lambda *a: None


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(kaggle, "_store", kaggle.MemoryCache())
    monkeypatch.setattr(kaggle, "CACHE_DIR", str(tmp_path / "cache"))
    for name in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_default_table():
    assert plan.default_table("Iris.csv") == "KG.Iris"
    assert plan.default_table("data/2019-Nov.csv") == "KG.t_2019_Nov"
    assert plan.default_table("x.csv", "Demo.T") == "Demo.T"


def test_precheck():
    db = SqliteDB()
    assert plan.precheck("uciml/iris", "Iris.csv", db=db) == "KG.Iris"
    assert plan.precheck("uciml/iris", "*", db=db) == "KG"
    assert plan.precheck("uciml/iris", "*", "Sales", db=db) == "Sales"
    with pytest.raises(KaggleError, match="schema name"):
        plan.precheck("uciml/iris", "*", "Demo.T", db=db)
    with pytest.raises(KaggleError, match="mode must be"):
        plan.precheck("uciml/iris", "Iris.csv", mode="overwrite", db=db)
    with pytest.raises(KaggleError, match="file is required"):
        plan.precheck("uciml/iris", "", db=db)
    db.execute("CREATE TABLE Demo.Theirs (a INT)")
    with pytest.raises(KaggleError) as e:
        plan.precheck("uciml/iris", "Iris.csv", "Demo.Theirs", db=db)
    assert e.value.code == "conflict"


def test_plan_for_a_new_table(monkeypatch):
    fake(monkeypatch, iris_routes())
    p = plan.plan("uciml/iris", "Iris.csv", db=SqliteDB())
    assert (p["action"], p["table"], p["version"], p["format"]) == ("create", "KG.Iris", 2, "csv")
    assert [(c["source"], c["name"], c["type"]) for c in p["columns"]] == \
        [("Id", "Id_", "BIGINT"), ("SepalLengthCm", "SepalLengthCm", "DOUBLE"), ("Species", "Species", "VARCHAR(4000)")]
    assert p["columns"][0]["note"] == "renamed from 'Id'"
    assert p["ddl"].startswith("CREATE TABLE KG.Iris")
    assert p["sampleRows"] == 150 and p["reason"] is None
    assert not any("first" in w for w in p["warnings"])                   # the whole small file was read


def test_plan_refuses(monkeypatch):
    fake(monkeypatch, iris_routes())
    db = SqliteDB()
    with pytest.raises(KaggleError, match="not a file type that can be imported"):
        plan.plan("uciml/iris", "database.sqlite", db=db)
    db.execute("CREATE TABLE Demo.Theirs (a INT)")
    p = plan.plan("uciml/iris", "Iris.csv", "Demo.Theirs", db=db)
    assert p["action"] == "refused" and "not created by" in p["reason"]
    p = plan.plan("uciml/iris", "Iris.csv", mode="nope", db=db)
    assert p["action"] == "refused" and "mode must be" in p["reason"]


def test_plan_size_limit_only_for_full_loads(monkeypatch):
    big = {"datasetFiles": [{"name": "big.csv", "totalBytes": 900 * 2 ** 20}]}
    head = b"a,b\n" + b"1,x\n" * 1000
    fake(monkeypatch, {"datasets/view/a/b": dict(VIEW, ref="a/b"), "datasets/list/a/b": big,
                       "datasets/download/a/b/big.csv": head})
    p = plan.plan("a/b", "big.csv", db=SqliteDB())
    assert p["action"] == "refused" and "MB limit" in p["reason"]
    p = plan.plan("a/b", "big.csv", limit=1000, db=SqliteDB())
    assert p["action"] == "create" and p["sampleRows"] == plan.SAMPLE_ROWS
    assert any("first 200 rows" in w for w in p["warnings"])


def test_plan_for_an_old_version_warns(monkeypatch):
    archive = zipped("Iris.csv", b"Id,x\n1,2\n")
    fake(monkeypatch, iris_routes(**{"datasets/download/uciml/iris?datasetVersionNumber=1": archive}))
    p = plan.plan("uciml/iris", "Iris.csv", version=1, db=SqliteDB())
    assert p["version"] == 1 and p["pinned"]
    assert any("not the latest" in w for w in p["warnings"])
    assert p["download"]["bytes"] == VIEW["totalBytes"]                    # the whole archive


def test_importable_files_get_unique_tables(monkeypatch):
    files = {"datasetFiles": [{"name": "2019/sales.csv", "totalBytes": 10}, {"name": "2020/sales.csv", "totalBytes": 10},
                              {"name": "readme.md", "totalBytes": 10}, {"name": "book.xlsx", "totalBytes": 10}]}
    fake(monkeypatch, {"datasets/view/a/b": dict(VIEW, ref="a/b"), "datasets/list/a/b": files})
    r = plan.importable_files("a/b", schema="Shop")
    assert [(f["file"], f["table"]) for f in r["files"]] == \
        [("2019/sales.csv", "Shop.sales"), ("2020/sales.csv", "Shop.sales_2"), ("book.xlsx", "Shop.book")]


def test_load(monkeypatch):
    fake(monkeypatch, iris_routes())
    db = SqliteDB()
    phases = []
    r = plan.load("uciml/iris", "Iris.csv", "Demo.Iris", db=db, log=quiet,
                  progress=lambda phase, *a: phases.append(phase))
    assert r["rows"] == 150 and r["table"] == "Demo.Iris" and r["version"] == 2 and r["encoding"] == "utf-8"
    assert r["file"] == "Iris.csv" and r["dataset"] == "uciml/iris"
    assert db.execute("select count(*), max(Id_) from Demo_Iris").fetchone() == (150, 150)
    assert phases[0] == "resolving" and "fetching" in phases and "inserting" in phases and phases[-1] == "finishing"


def test_load_with_a_row_limit(monkeypatch):
    fake(monkeypatch, iris_routes())
    db = SqliteDB()
    r = plan.load("uciml/iris", "Iris.csv", "Demo.Iris", limit=10, db=db, log=quiet)
    assert r["rows"] == 10                                  # a small file is read whole, then cut
    plan.load("uciml/iris", "Iris.csv", "Demo.Iris", limit=5, mode="append", db=db, log=quiet)
    assert db.execute("select count(*) from Demo_Iris").fetchone()[0] == 15


def test_full_load_row_limit(monkeypatch):
    fake(monkeypatch, iris_routes())
    monkeypatch.setattr(loader, "MAX_ROWS", 100)
    with pytest.raises(LimitExceeded, match="150 rows, more than the 100 row limit"):
        plan.load("uciml/iris", "Iris.csv", db=SqliteDB(), log=quiet)


def test_limit_beyond_the_download_cap(monkeypatch):
    fake(monkeypatch, iris_routes())
    monkeypatch.setattr(kaggle, "read_rows", lambda *a, **k: (pa.table({"a": [1, 2]}), {}, False))
    with pytest.raises(LimitExceeded, match="Only 2 rows of Iris.csv fit"):
        plan.load("uciml/iris", "Iris.csv", limit=1000, db=SqliteDB(), log=quiet)


def test_cancel_before_writing(monkeypatch):
    fake(monkeypatch, iris_routes())
    db = SqliteDB()
    with pytest.raises(loader.Cancelled):
        plan.load("uciml/iris", "Iris.csv", "Demo.Iris", db=db, log=quiet, cancelled=lambda: True)
    assert not db.table_exists("Demo.Iris")


def test_bad_limit():
    with pytest.raises(KaggleError, match="limit must be a number"):
        plan.load("uciml/iris", "Iris.csv", limit="many", db=SqliteDB(), log=quiet)


def test_json_calls(monkeypatch):
    fake(monkeypatch, iris_routes())
    monkeypatch.setattr(loader, "IrisSQL", SqliteDB)
    r = json.loads(plan.call(json.dumps({"dataset": "uciml/iris", "file": "Iris.csv", "limit": ""})))
    assert r["action"] == "create"
    r = json.loads(plan.call(json.dumps({"dataset": "uciml/iris", "split": "train"})))
    assert r == {"error": "unknown parameters: split", "code": "bad_request"}
    r = json.loads(plan.call(json.dumps({"dataset": "uciml/iris", "file": "nope.csv"})))
    assert r["code"] == "not_found"
    assert json.loads(plan.call_files(json.dumps({"dataset": "uciml/iris"})))["files"][0]["table"] == "KG.Iris"
    assert json.loads(plan.call_precheck(json.dumps({"dataset": "uciml/iris", "file": "*"}))) == {"table": "KG"}
    r = json.loads(plan.call_precheck(json.dumps({"dataset": "bad id", "file": "x.csv"})))
    assert r["code"] == "bad_request"
