"""The Kaggle client without network: Kaggle's answers are faked at open_url()."""
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from kaggle2iris import kaggle
from kaggle2iris.kaggle import KaggleError

IRIS_CSV = b"Id,SepalLengthCm,Species\n" + b"".join(b"%d,5.%d,Iris-setosa\n" % (i, i % 10) for i in range(1, 151))

VIEW = {"ref": "uciml/iris", "title": "Iris Species", "subtitle": "Classify iris plants", "ownerName": "UCI",
        "totalBytes": 15347, "usabilityRating": 0.79, "voteCount": 4965, "downloadCount": 950639,
        "lastUpdated": "2016-09-27T07:38:05Z", "licenseName": "CC0: Public Domain",
        "tags": [{"name": "biology"}], "isPrivate": False, "currentVersionNumber": 2,
        "description": "The classic data set.",
        "versions": [{"versionNumber": 2, "creationDate": "2016-09-27", "versionNotes": "Republish", "status": "Ready"},
                     {"versionNumber": 1, "creationDate": "2016-01-12", "versionNotes": "Initial", "status": "Ready"}]}
FILES = {"datasetFiles": [{"name": "Iris.csv", "totalBytes": len(IRIS_CSV), "creationDate": "2016-09-27"},
                          {"name": "database.sqlite", "totalBytes": 10240}]}


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())


class FakeKaggle:
    """Routes URLs to canned answers and records what was asked."""

    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def __call__(self, url, headers=None, timeout=None):
        self.urls.append(url)
        for prefix, answer in self.routes.items():
            if url.startswith(kaggle.API + prefix):
                if isinstance(answer, Exception):
                    raise answer
                return Response(answer(url) if callable(answer) else answer)
        raise KaggleError("not_found", "Not found on Kaggle.")


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(kaggle, "_store", kaggle.MemoryCache())
    monkeypatch.setattr(kaggle, "CACHE_DIR", str(tmp_path / "cache"))
    for name in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
        monkeypatch.delenv(name, raising=False)


def fake(monkeypatch, routes):
    f = FakeKaggle(routes)
    monkeypatch.setattr(kaggle, "open_url", f)
    return f


def iris_routes(**extra):
    routes = {"datasets/view/uciml/iris": VIEW, "datasets/list/uciml/iris": FILES,
              "datasets/download/uciml/iris/Iris.csv": IRIS_CSV}
    routes.update(extra)
    return routes


def zipped(name, data, method=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        z.writestr(name, data)
    return buf.getvalue()


# ------------------------------------------------------------------ input ---
def test_check_ref():
    assert kaggle.check_ref(" uciml/iris ") == "uciml/iris"
    for bad in ("", "iris", "a/b/c", "../x", "a/..", "a b/c", "a/b?x=1"):
        with pytest.raises(KaggleError) as e:
            kaggle.check_ref(bad)
        assert e.value.code == "bad_request"


def test_check_file_and_version():
    assert kaggle.check_file("data/train.csv") == "data/train.csv"
    for bad in ("../x.csv", "a//b.csv", "x\n.csv", "/abs.csv"):
        with pytest.raises(KaggleError):
            kaggle.check_file(bad)
    assert kaggle.check_file("", required=False) is None
    assert kaggle.check_version("") is None and kaggle.check_version("3") == 3
    with pytest.raises(KaggleError, match="number"):
        kaggle.check_version("latest")
    with pytest.raises(KaggleError, match="1 or more"):
        kaggle.check_version(-1)


# ------------------------------------------------------------ credentials ---
def test_no_credentials_by_default():
    assert kaggle._auth() == {} and not kaggle.has_credentials()


def test_token_and_legacy_key(monkeypatch):
    monkeypatch.setenv("KAGGLE_USERNAME", "ann")
    monkeypatch.setenv("KAGGLE_KEY", "k3y")
    assert kaggle._auth() == {"Authorization": "Basic YW5uOmszeQ=="}
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_abc")
    assert kaggle._auth() == {"Authorization": "Bearer KGAT_abc"}              # the token wins


def test_only_kaggle_hosts():
    with pytest.raises(KaggleError, match="other than Kaggle"):
        kaggle.open_url("https://example.com/x")


def test_redirect_drops_credentials_on_another_host():
    handler = kaggle._SafeRedirect()
    req = urllib.request.Request(kaggle.API + "datasets/download/a/b/x.csv", headers={"Authorization": "Bearer s"})
    new = handler.redirect_request(req, None, 302, "Found", {}, "https://storage.googleapis.com/bucket/x.csv?sig=1")
    assert not new.has_header("Authorization")
    same = handler.redirect_request(req, None, 302, "Found", {}, "https://www.kaggle.com/api/v1/other")
    assert same.has_header("Authorization")
    with pytest.raises(KaggleError, match="unexpected host"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/x")


def http_error(code, body=b"{}", headers=None):
    return urllib.error.HTTPError("https://www.kaggle.com/api/v1/x", code, "x", headers or {}, io.BytesIO(body))


def test_http_errors(monkeypatch):
    e = kaggle._http_error(http_error(403, b'{"message": "Permission \'datasets.get\' was denied"}'))
    assert e.code == "not_found" and "KAGGLE_API_TOKEN" in str(e)
    assert kaggle._http_error(http_error(401)).code == "credentials"         # never 401: no browser sign-in prompt
    assert kaggle._http_error(http_error(404)).code == "not_found"
    assert kaggle._http_error(http_error(400, b'{"message": "bad sort"}')).code == "bad_request"
    assert kaggle._http_error(http_error(500)).code == "upstream"
    e = kaggle._http_error(http_error(429, headers={"Retry-After": "30"}))
    assert e.code == "rate_limited" and "30 seconds" in str(e)
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_abc")
    assert "this token has no access" in str(kaggle._http_error(http_error(403)))


def test_html_answer_means_not_found(monkeypatch):
    fake(monkeypatch, {"datasets/view/": b"<html>404</html>"})
    with pytest.raises(KaggleError) as e:
        kaggle.api_json("datasets/view/a/b")
    assert e.value.code == "not_found"


# ---------------------------------------------------------------- search ---
def test_search_maps_and_filters(monkeypatch):
    private = dict(VIEW, ref="me/secret", isPrivate=True)
    f = fake(monkeypatch, {"datasets/list?": [VIEW, private]})
    r = kaggle.search("iris", sort="votes", file_type="csv", license="cc", tag="biology", min_size="10", page="2")
    assert [d["ref"] for d in r["items"]] == ["uciml/iris"]                 # private ones hidden by default
    d = r["items"][0]
    assert (d["title"], d["votes"], d["license"], d["tags"], d["currentVersion"]) == \
        ("Iris Species", 4965, "CC0: Public Domain", ["biology"], 2)
    assert d["url"] == "https://www.kaggle.com/datasets/uciml/iris"
    assert r["hasMore"] is False and r["page"] == 2 and r["cached"] is False
    url = f.urls[0]
    for part in ("search=iris", "sortBy=votes", "fileType=csv", "license=cc", "tagIds=biology", "minSize=10", "page=2"):
        assert part in url
    assert "maxSize" not in url
    assert kaggle.search("iris", sort="votes", file_type="csv", license="cc", tag="biology", min_size="10", page="2")["cached"]


def test_search_rejects_bad_input(monkeypatch):
    fake(monkeypatch, {})
    for kwargs in ({"sort": "likes"}, {"file_type": "exe"}, {"license": "mine"}, {"tag": "<b>"}, {"search": "x" * 101}):
        with pytest.raises(KaggleError) as e:
            kaggle.search(**kwargs)
        assert e.value.code == "bad_request"


# --------------------------------------------------------------- details ---
def test_details(monkeypatch):
    f = fake(monkeypatch, iris_routes())
    d = kaggle.details("uciml/iris")
    assert d["version"] == 2 and d["pinned"] is False
    assert [v["number"] for v in d["versions"]] == [2, 1]
    assert [(x["name"], x["format"], x["importable"]) for x in d["files"]] == \
        [("Iris.csv", "csv", True), ("database.sqlite", None, False)]
    assert d["importableFiles"] == 1 and d["filesTruncated"] is False
    assert d["description"] == "The classic data set."
    kaggle.details("uciml/iris")
    assert len(f.urls) == 2                                               # second call from the cache


def test_details_pinned_and_unknown_version(monkeypatch):
    f = fake(monkeypatch, iris_routes())
    d = kaggle.details("uciml/iris", "1")
    assert d["version"] == 1 and d["pinned"] is True
    assert "datasetVersionNumber=1" in f.urls[-1]
    with pytest.raises(KaggleError, match="Version 7 not found for uciml/iris. Versions: 2, 1"):
        kaggle.details("uciml/iris", 7)


def test_file_list_is_capped(monkeypatch):
    def page(url):
        return {"datasetFiles": [{"name": f"img{i}.jpg", "totalBytes": 1} for i in range(200)], "nextPageToken": "t"}
    fake(monkeypatch, {"datasets/list/a/b": page})
    files, truncated = kaggle.list_files("a/b")
    assert len(files) == 200 * kaggle.FILE_PAGES and truncated


def test_find_file_lists_choices(monkeypatch):
    fake(monkeypatch, iris_routes())
    info = kaggle.details("uciml/iris")
    with pytest.raises(KaggleError, match="Files you can import: Iris.csv"):
        kaggle.find_file(info, "nope.csv")


# ------------------------------------------------------------- downloads ---
def test_file_url_quotes_folders():
    assert kaggle.file_url("a/b", "data/train set.csv").endswith("datasets/download/a/b/data%2Ftrain%20set.csv")


@pytest.mark.parametrize("method", [zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED])
def test_inflater_unzips_while_streaming(method):
    raw = zipped("big.csv", IRIS_CSV * 20, method)
    inflater = kaggle._Inflater("data/big.csv")
    out = b"".join(inflater.feed(raw[i:i + 7]) for i in range(0, len(raw), 7))
    assert out == IRIS_CSV * 20 and inflater.done


def test_inflater_passes_plain_files_and_zip_files_through():
    assert kaggle._Inflater("x.csv").feed(IRIS_CSV) == IRIS_CSV
    raw = zipped("x.csv", b"a\n1\n")
    assert kaggle._Inflater("x.zip").feed(raw) == raw                       # a .zip file is kept as it is
    book = zipped("[Content_Types].xml", b"<Types/>")                       # what an .xlsx starts with
    assert kaggle._Inflater("Medals.xlsx").feed(book) == book


def test_head_stops_early(monkeypatch):
    fake(monkeypatch, iris_routes())
    data, complete = kaggle.head("uciml/iris", "Iris.csv")
    assert data == IRIS_CSV and complete
    big = b"n\n" + b"x\n" * 400_000
    fake(monkeypatch, {"datasets/download/a/b/big.csv": big})
    data, complete = kaggle.head("a/b", "big.csv", min_lines=10)
    assert not complete and data.count(b"\n") > 10 and len(data) < len(big)
    data, complete = kaggle.head("a/b", "big.csv", max_bytes=1000)
    assert len(data) == 1000 and not complete


def test_head_of_a_zipped_file(monkeypatch):
    fake(monkeypatch, {"datasets/download/a/b/big.csv": zipped("big.csv", IRIS_CSV)})
    data, complete = kaggle.head("a/b", "big.csv")
    assert data == IRIS_CSV and complete


def test_download_latest_is_cached(monkeypatch):
    f = fake(monkeypatch, iris_routes())
    seen = []
    path = kaggle.download("uciml/iris", "Iris.csv", 2, size=len(IRIS_CSV), progress=seen.append)
    assert open(path, "rb").read() == IRIS_CSV and seen[-1] == len(IRIS_CSV)
    assert kaggle.download("uciml/iris", "Iris.csv", 2, size=len(IRIS_CSV)) == path
    assert len(f.urls) == 1
    assert not [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".part")]


def test_tiny_files_are_not_lost(monkeypatch):
    # shorter than a zip header: held back while checking for one, then flushed at the end
    tiny = b"x,y\n1,2\n"
    fake(monkeypatch, {"datasets/download/a/b/t.csv": tiny})
    assert kaggle.head("a/b", "t.csv") == (tiny, True)
    assert open(kaggle.download("a/b", "t.csv", 1), "rb").read() == tiny


def test_download_cap(monkeypatch):
    fake(monkeypatch, iris_routes())
    with pytest.raises(KaggleError) as e:
        kaggle.download("uciml/iris", "Iris.csv", 2, max_bytes=100)
    assert e.value.code == "too_large"
    assert not os.path.exists(kaggle._cache_path("uciml/iris", "Iris.csv", 2))


def test_download_old_version_from_its_zip(monkeypatch):
    archive = zipped("Iris.csv", b"Id,x\n1,2\n")
    f = fake(monkeypatch, {"datasets/download/uciml/iris?datasetVersionNumber=1": archive})
    path = kaggle.download("uciml/iris", "Iris.csv", 1, latest=False)
    assert open(path, "rb").read() == b"Id,x\n1,2\n"
    assert f.urls == [kaggle.API + "datasets/download/uciml/iris?datasetVersionNumber=1"]
    with pytest.raises(KaggleError, match="not part of version 1"):
        kaggle.download("uciml/iris", "Other.csv", 1, latest=False)


# --------------------------------------------------------------- preview ---
def test_preview(monkeypatch):
    fake(monkeypatch, iris_routes())
    p = kaggle.preview("uciml/iris", "Iris.csv", offset=10, rows=5)
    assert p["columns"] == ["Id", "SepalLengthCm", "Species"]
    assert [t["type"] for t in p["types"]] == ["integer", "number", "text"]
    assert p["rows"][0] == [11, 5.1, "Iris-setosa"] and len(p["rows"]) == 5
    assert p["total"] == 150 and p["partial"] is False and p["offset"] == 10


def test_preview_limits(monkeypatch):
    fake(monkeypatch, iris_routes())
    p = kaggle.preview("uciml/iris", "Iris.csv", offset=99999, rows=500)
    assert p["offset"] == kaggle.PREVIEW_ROWS - 1 and len(p["rows"]) <= 1
    with pytest.raises(KaggleError) as e:
        kaggle.preview("uciml/iris", "database.sqlite")
    assert e.value.code == "preview_unavailable"


def test_preview_of_a_large_binary_file_is_refused(monkeypatch):
    files = {"datasetFiles": [{"name": "big.parquet", "totalBytes": (kaggle.PREVIEW_MAX_MB + 1) * 2 ** 20}]}
    f = fake(monkeypatch, {"datasets/view/a/b": dict(VIEW, ref="a/b"), "datasets/list/a/b": files})
    with pytest.raises(KaggleError, match="previewed up to"):
        kaggle.preview("a/b", "big.parquet")
    assert not any("download" in u for u in f.urls)


def test_long_cells_are_cut():
    value, cut = kaggle._cell("x" * 2000)
    assert len(value) == kaggle.CELL_CHARS and cut
    assert kaggle._cell(float("nan")) == (None, False)
    assert kaggle._cell({"a": 1}) == ('{"a": 1}', False)


# ----------------------------------------------------------------- cache ---
def test_errors_are_not_cached(monkeypatch):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise KaggleError("timeout", "slow")
        return {"ok": True}
    with pytest.raises(KaggleError):
        kaggle.cached("x", {}, 60, flaky)
    assert kaggle.cached("x", {}, 60, flaky) == {"ok": True, "cached": False}
    assert kaggle.cached("x", {}, 60, flaky)["cached"] is True
    assert kaggle.cached("x", {}, 60, flaky, refresh=True)["cached"] is False


def test_cache_is_per_credential(monkeypatch):
    kaggle.cached("x", {}, 60, lambda: {"who": "anon"})
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_abc")
    assert kaggle.cached("x", {}, 60, lambda: {"who": "token"})["who"] == "token"


def test_answers_holding_the_secret_are_not_cached(monkeypatch):
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_abc")
    kaggle.cached("x", {}, 60, lambda: {"echo": "KGAT_abc"})
    assert kaggle.cache().keys() == []


def test_cache_expiry_and_size(monkeypatch):
    store = kaggle.cache()
    store.set("old", "1\t{}")
    kaggle._prune(store, now=100)
    assert store.keys() == []
    monkeypatch.setattr(kaggle, "CACHE_MAX_ENTRIES", 3)
    for i in range(5):
        kaggle._prune(store, now=0)
        store.set(f"k{i}", f"{1000 + i}\t{{}}")
    assert len(store.keys()) <= 3 and "k4" in store.keys()


# ------------------------------------------------------------------ call ---
def test_call_json_in_json_out(monkeypatch):
    fake(monkeypatch, iris_routes())
    r = json.loads(kaggle.call("details", json.dumps({"dataset": "uciml/iris", "version": ""})))
    assert r["ref"] == "uciml/iris"
    assert json.loads(kaggle.call("nope", "{}"))["code"] == "bad_request"
    r = json.loads(kaggle.call("details", json.dumps({"dataset": "uciml/iris", "evil": 1})))
    assert r == {"error": "unknown parameters: evil", "code": "bad_request"}
    r = json.loads(kaggle.call("details", json.dumps({"dataset": "nobody/nothing"})))
    assert r["code"] == "not_found"


def test_call_masks_credentials(monkeypatch):
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_SuperSecret")

    def leak(**_):
        raise RuntimeError("failed with KGAT_SuperSecret")
    r = json.loads(kaggle.call("x", "{}", endpoints={"x": leak}))
    assert "KGAT_SuperSecret" not in r["error"] and "***" in r["error"] and r["code"] == "upstream"


def test_status_never_shows_the_token(monkeypatch):
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_SuperSecret")
    s = kaggle.status()
    assert s["credentials"] is True and "KGAT_SuperSecret" not in json.dumps(s)


def test_no_kaggle_package_is_used():
    src = open(kaggle.__file__, encoding="utf-8").read()
    assert "import kaggle\n" not in src and "from kaggle " not in src and "kagglehub" not in src
