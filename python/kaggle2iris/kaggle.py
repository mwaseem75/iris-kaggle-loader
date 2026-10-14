"""
Read-only access to Kaggle datasets: search, details, file lists, versions, previews
and downloads, through Kaggle's public REST API (no Kaggle package needed).

Public datasets work without credentials. KAGGLE_API_TOKEN (or KAGGLE_USERNAME and
KAGGLE_KEY) adds private datasets; credentials stay on the server and are never sent
to the storage host that downloads redirect to.
"""
import base64
import hashlib
import json
import os
import re
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .loader import LoadError, error_text, MAX_ROWS, MAX_DOWNLOAD_MB
from .readers import file_format, read

API = "https://www.kaggle.com/api/v1/"
SITE = "https://www.kaggle.com/datasets/"
ALLOWED_HOSTS = {"www.kaggle.com", "storage.googleapis.com"}
TIMEOUT = 20
CACHE_TTL = int(os.environ.get("KAGGLE2IRIS_CACHE_TTL", 600))
SEARCH_TTL = min(300, CACHE_TTL)
CACHE_MAX_ENTRIES = 500
CACHE_MAX_BYTES = 1_000_000
SHOW_PRIVATE = os.environ.get("KAGGLE2IRIS_SHOW_PRIVATE", "0") == "1"
PREVIEW_MAX_MB = int(os.environ.get("KAGGLE2IRIS_PREVIEW_MAX_MB", 200))
PREVIEW_ROWS = 1000                  # previews show the first 1000 rows
HEAD_BYTES = 8 * 2 ** 20             # most text read for a preview
FILE_PAGES = 5                       # 5 x 200 = the first 1000 files of a dataset
CELL_CHARS = 500
DESCRIPTION_CHARS = 300
CACHE_DIR = os.environ.get("KAGGLE2IRIS_CACHE_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "kaggle2iris")

SORTS = ("hottest", "votes", "updated", "active", "published")
FILE_TYPES = ("all", "csv", "json", "parquet", "sqlite", "bigQuery")
LICENSES = ("all", "cc", "gpl", "odb", "other")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
TAG = re.compile(r"[A-Za-z0-9 _-]{1,50}")


class KaggleError(LoadError):
    """code: bad_request, not_found, credentials, preview_unavailable, too_large, rate_limited,
    timeout, upstream"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------- input ---
def check_ref(value):
    value = (value or "").strip()
    if not value:
        raise KaggleError("bad_request", "dataset is required, for example uciml/iris")
    if not REF.fullmatch(value) or ".." in value:
        raise KaggleError("bad_request", f"{value!r} is not a Kaggle dataset id (owner/dataset)")
    return value


def check_file(value, required=True):
    value = (value or "").strip()
    if not value:
        if required:
            raise KaggleError("bad_request", "file is required")
        return None
    parts = value.split("/")
    if len(value) > 500 or any(p in ("", ".", "..") for p in parts) or any(ord(c) < 32 for c in value):
        raise KaggleError("bad_request", f"{value!r} is not a valid file name")
    return value


def check_version(value):
    if value in (None, "", 0, "0"):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise KaggleError("bad_request", "version must be a number")
    if number < 1:
        raise KaggleError("bad_request", "version must be 1 or more")
    return number


def _clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _flag(value):
    return str(value).lower() in ("1", "true", "yes")


# ------------------------------------------------------------- credentials ---
def _auth():
    token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if token:
        return {"Authorization": "Bearer " + token}
    user, key = os.environ.get("KAGGLE_USERNAME", "").strip(), os.environ.get("KAGGLE_KEY", "").strip()
    if user and key:
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{key}".encode()).decode()}
    return {}


def _secret():
    return os.environ.get("KAGGLE_API_TOKEN", "").strip() or os.environ.get("KAGGLE_KEY", "").strip() or None


def _fingerprint():
    secret = _secret()
    return hashlib.sha256(secret.encode()).hexdigest()[:8] if secret else "anon"


def has_credentials():
    return bool(_auth())


# ------------------------------------------------------------------- cache ---
class MemoryCache:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)

    def set(self, key, value):
        self.items[key] = value

    def kill(self, key):
        self.items.pop(key, None)

    def keys(self):
        return list(self.items)


class GlobalCache:
    """^CacheTemp.kaggle2iris(key) = "expires<TAB>json"; IRISTEMP, shared, not journaled."""

    def __init__(self, name="^CacheTemp.kaggle2iris"):
        import iris  # noqa: available only inside IRIS
        self.g = iris.gref(name)

    def get(self, key):
        return self.g.get([key], None)

    def set(self, key, value):
        self.g.set([key], value)

    def kill(self, key):
        self.g.kill([key])

    def keys(self):
        out, key = [], ""
        while True:
            key = self.g.order([key])
            if not key:
                return out
            out.append(key)


_store = None


def cache():
    global _store
    if _store is None:
        try:
            _store = GlobalCache()
        except Exception:
            _store = MemoryCache()
    return _store


def _read(store, key, now):
    raw = store.get(key)
    if raw is None:
        return None
    expires, _, payload = str(raw).partition("\t")
    if float(expires or 0) <= now:
        store.kill(key)
        return None
    return payload


def _prune(store, now):
    entries = []
    for key in store.keys():
        raw = store.get(key)
        expires = float(str(raw).partition("\t")[0] or 0) if raw is not None else 0
        if expires <= now:
            store.kill(key)
        else:
            entries.append((expires, key))
    for _, key in sorted(entries)[:max(0, len(entries) - CACHE_MAX_ENTRIES + 1)]:
        store.kill(key)


def cached(kind, params, ttl, fetch, refresh=False):
    """fetch() unless a fresh entry exists; errors are raised, never cached."""
    if ttl <= 0:
        return dict(fetch(), cached=False)
    store, now = cache(), time.time()
    key = hashlib.sha1(json.dumps([kind, params, _fingerprint()], sort_keys=True).encode()).hexdigest()
    if not refresh:
        payload = _read(store, key, now)
        if payload is not None:
            return dict(json.loads(payload), cached=True)
    data = fetch()
    payload = json.dumps(data)
    secret = _secret()
    if len(payload) <= CACHE_MAX_BYTES and not (secret and secret in payload):
        _prune(store, now)
        store.set(key, f"{now + ttl:.0f}\t{payload}")
    return dict(data, cached=False)


# -------------------------------------------------------------------- HTTP ---
class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Only follow redirects to Kaggle's own hosts, and never carry credentials
    to another host (downloads redirect to signed storage URLs)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlparse(req.full_url).hostname, urllib.parse.urlparse(newurl).hostname
        if new not in ALLOWED_HOSTS:
            raise KaggleError("upstream", "Kaggle redirected to an unexpected host")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and new != old:
            redirected.remove_header("Authorization")
        return redirected


_opener = urllib.request.build_opener(_SafeRedirect)


def open_url(url, headers=None, timeout=TIMEOUT):
    """An open HTTP response; HTTP and network errors become KaggleError."""
    if urllib.parse.urlparse(url).hostname not in ALLOWED_HOSTS:
        raise KaggleError("upstream", "Refusing to contact a host other than Kaggle")
    all_headers = {"User-Agent": "iris-kaggle-loader", "Accept": "application/json, */*"}
    all_headers.update(_auth() if urllib.parse.urlparse(url).hostname == "www.kaggle.com" else {})
    all_headers.update(headers or {})
    try:
        return _opener.open(urllib.request.Request(url, headers=all_headers), timeout=timeout)
    except urllib.error.HTTPError as e:
        raise _http_error(e)
    except (socket.timeout, TimeoutError):
        raise _timeout()
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise _timeout()
        if isinstance(e.reason, KaggleError):
            raise e.reason
        raise KaggleError("upstream", f"Could not reach Kaggle: {e.reason}")


def _http_error(e):
    try:
        body = json.loads(e.read() or b"{}")
        message = body.get("message") if isinstance(body, dict) else None
    except ValueError:
        message = None
    if e.code == 429:
        wait = e.headers.get("Retry-After")
        hint = "" if has_credentials() else " Setting KAGGLE_API_TOKEN may give a higher limit."
        return KaggleError("rate_limited", "Kaggle rate limit reached." +
                           (f" Try again in {wait} seconds." if wait else " Try again in a minute.") + hint)
    if e.code == 401:
        return KaggleError("credentials", "Kaggle rejected the credentials. Check KAGGLE_API_TOKEN "
                                          "(or KAGGLE_USERNAME and KAGGLE_KEY).")
    if e.code == 403:
        extra = "this token has no access to it" if has_credentials() else "private (private datasets need KAGGLE_API_TOKEN)"
        return KaggleError("not_found", f"Dataset not found, or {extra}.")
    if e.code == 404:
        return KaggleError("not_found", "Not found on Kaggle." + (f" ({message})" if message and message != "Data not found" else ""))
    if e.code == 400:
        return KaggleError("bad_request", f"Kaggle did not accept the request: {message or 'bad request'}")
    return KaggleError("upstream", f"Kaggle answered HTTP {e.code}" + (f": {message}" if message else ""))


def _timeout():
    return KaggleError("timeout", f"Kaggle did not answer within {TIMEOUT} seconds. Try again.")


def api_json(path, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    with open_url(API + path + ("?" + query if query else "")) as r:
        body = r.read()
    try:
        return json.loads(body)
    except ValueError:
        raise KaggleError("not_found", "Not found on Kaggle.")          # Kaggle answers HTML for unknown paths


# ----------------------------------------------------------------- mapping ---
def _clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


def summary(d):
    return {
        "ref": d.get("ref"),
        "title": d.get("title") or d.get("ref"),
        "subtitle": _clip(d.get("subtitle"), DESCRIPTION_CHARS),
        "owner": d.get("ownerName") or d.get("creatorName") or (d.get("ref") or "/").split("/")[0],
        "totalBytes": d.get("totalBytes"),
        "usability": d.get("usabilityRating"),
        "votes": d.get("voteCount") or 0,
        "downloads": d.get("downloadCount") or 0,
        "views": d.get("viewCount") or 0,
        "lastUpdated": d.get("lastUpdated"),
        "license": d.get("licenseName"),
        "tags": [t.get("name") for t in (d.get("tags") or []) if t.get("name")][:8],
        "private": bool(d.get("isPrivate")),
        "currentVersion": d.get("currentVersionNumber"),
        "url": d.get("url") or SITE + str(d.get("ref")),
    }


def _short_type(arrow_type):
    import pyarrow.types as pt
    t = arrow_type
    if pt.is_integer(t):
        return "integer"
    if pt.is_floating(t) or pt.is_decimal(t):
        return "number"
    if pt.is_boolean(t):
        return "boolean"
    if pt.is_timestamp(t):
        return "timestamp"
    if pt.is_date(t):
        return "date"
    if pt.is_string(t) or pt.is_large_string(t):
        return "text"
    if pt.is_list(t) or pt.is_large_list(t):
        return "list"
    if pt.is_struct(t):
        return "object"
    return str(t)


def _cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        value = value.isoformat(sep=" ") if hasattr(value, "isoformat") and not isinstance(value, type) else str(value)
    if isinstance(value, float) and value != value:
        value = None
    if isinstance(value, str) and len(value) > CELL_CHARS:
        return value[:CELL_CHARS], True
    return value, False


# --------------------------------------------------------------- endpoints ---
def search(search="", sort="hottest", file_type="all", license="all", tag=None, min_size=None, max_size=None,
           page=1, refresh=False):
    query = (search or "").strip()
    if len(query) > 100:
        raise KaggleError("bad_request", "search is limited to 100 characters")
    sort = (sort or "hottest").strip()
    if sort not in SORTS:
        raise KaggleError("bad_request", f"sort must be one of {', '.join(SORTS)}")
    file_type = (file_type or "all").strip()
    if file_type not in FILE_TYPES:
        raise KaggleError("bad_request", f"file_type must be one of {', '.join(FILE_TYPES)}")
    license = (license or "all").strip()
    if license not in LICENSES:
        raise KaggleError("bad_request", f"license must be one of {', '.join(LICENSES)}")
    tag = (tag or "").strip() or None
    if tag and not TAG.fullmatch(tag):
        raise KaggleError("bad_request", f"tag {tag!r} is not valid")
    min_size = _clamp(min_size, 0, 10 ** 13, None) if min_size not in (None, "") else None
    max_size = _clamp(max_size, 0, 10 ** 13, None) if max_size not in (None, "") else None
    page = _clamp(page, 1, 500, 1)
    params = {"search": query, "sort": sort, "file_type": file_type, "license": license, "tag": tag,
              "min": min_size, "max": max_size, "page": page, "private": SHOW_PRIVATE}

    def fetch():
        found = api_json("datasets/list", search=query or None, sortBy=sort, fileType=file_type,
                         license=license, tagIds=tag, minSize=min_size, maxSize=max_size, page=page)
        items = [summary(d) for d in found if SHOW_PRIVATE or not d.get("isPrivate")]
        return {"items": items, "count": len(items), "page": page, "hasMore": len(found) >= 20,
                "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    return cached("search", params, SEARCH_TTL, fetch, _flag(refresh))


def list_files(ref, version=None):
    """(files, truncated) for a version (None = latest), first FILE_PAGES pages."""
    files, token = [], None
    for _ in range(FILE_PAGES):
        body = api_json(f"datasets/list/{ref}", datasetVersionNumber=version, pageSize=200, pageToken=token)
        for f in body.get("datasetFiles") or []:
            fmt = file_format(f.get("name"))
            files.append({"name": f.get("name"), "size": f.get("totalBytes"), "format": fmt,
                          "importable": fmt is not None, "created": f.get("creationDate")})
        token = body.get("nextPageToken")
        if not token:
            return files, False
    return files, True


def details(dataset, version=None, refresh=False):
    ref = check_ref(dataset)
    version = check_version(version)

    def fetch():
        view = api_json(f"datasets/view/{ref}")
        versions = [{"number": v.get("versionNumber"), "date": v.get("creationDate"),
                     "notes": _clip(v.get("versionNotes"), 200), "status": v.get("status")}
                    for v in view.get("versions") or []]
        current = view.get("currentVersionNumber")
        if version and versions and version not in [v["number"] for v in versions]:
            raise KaggleError("not_found", f"Version {version} not found for {ref}. Versions: "
                                           + ", ".join(str(v["number"]) for v in versions))
        files, truncated = list_files(ref, version)
        out = summary(view)
        out.update({"description": str(view.get("description") or "")[:20000], "versions": versions,
                    "version": version or current, "pinned": bool(version), "files": files,
                    "filesTruncated": truncated,
                    "importableFiles": sum(1 for f in files if f["importable"]),
                    "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return out

    return cached("details", {"ref": ref, "version": version}, CACHE_TTL, fetch, _flag(refresh))


def find_file(info, name):
    for f in info["files"]:
        if f["name"] == name:
            return f
    importable = [f["name"] for f in info["files"] if f["importable"]][:20]
    raise KaggleError("not_found", f"File {name!r} not found in {info['ref']}."
                                   + (f" Files you can import: {', '.join(importable)}" if importable else ""))


# ---------------------------------------------------------------- downloads ---
def file_url(ref, name):
    return API + f"datasets/download/{ref}/" + urllib.parse.quote(name, safe="")


def version_url(ref, version):
    return API + f"datasets/download/{ref}?datasetVersionNumber={int(version)}"


class _Inflater:
    """Turns the raw bytes of a download into the file's bytes. Kaggle sends large files
    inside a zip that holds just that file; it is inflated while streaming. Files that are
    zips themselves (.zip, .xlsx) start with a different entry and pass through as they are."""

    def __init__(self, name):
        self.name = os.path.basename(name).encode()
        self.buffer, self.mode, self.decoder, self.stored_left = b"", None, None, None

    def feed(self, chunk):
        if self.mode is None:
            self.buffer += chunk
            if len(self.buffer) < 30:
                return b""
            if self.buffer[:4] == b"PK\x03\x04":
                flags, method = struct.unpack("<HH", self.buffer[6:10])
                csize, _, nlen, xlen = struct.unpack("<IIHH", self.buffer[18:30])
                start = 30 + nlen + xlen
                if len(self.buffer) < start:
                    return b""
                if os.path.basename(self.buffer[30:30 + nlen].decode("utf-8", "replace")).encode() != self.name:
                    self.mode, chunk, self.buffer = "raw", self.buffer, b""     # a zip file in its own right
                    return chunk
                rest, self.buffer = self.buffer[start:], b""
                if method == 8:
                    self.mode, self.decoder = "deflate", zlib.decompressobj(-15)
                elif method == 0 and not flags & 8:
                    self.mode, self.stored_left = "stored", csize
                else:
                    raise KaggleError("upstream", "The file arrived in an unsupported zip format")
                return self.feed(rest)
            self.mode, chunk, self.buffer = "raw", self.buffer, b""
            return chunk
        if self.mode == "raw":
            return chunk
        if self.mode == "deflate":
            return b"" if self.decoder.eof else self.decoder.decompress(chunk)
        out = chunk[:self.stored_left]
        self.stored_left -= len(out)
        return out

    def flush(self):
        """What is still held back at the end of the stream: a file shorter than a zip header."""
        if self.mode is None:
            self.mode, rest, self.buffer = "raw", self.buffer, b""
            return rest
        return b""

    @property
    def done(self):
        return (self.mode == "deflate" and self.decoder.eof) or (self.mode == "stored" and self.stored_left <= 0)


def head(ref, name, max_bytes=HEAD_BYTES, min_lines=None):
    """(data, complete): the first bytes of the latest version of a file, unzipped,
    stopping after max_bytes or once min_lines lines have arrived."""
    inflater = _Inflater(name)
    data = bytearray()
    with open_url(file_url(ref, name)) as r:
        while len(data) < max_bytes:
            chunk = r.read(256 * 1024)
            if not chunk:
                return bytes(data + inflater.flush()), True
            data += inflater.feed(chunk)
            if inflater.done:
                return bytes(data), True
            if min_lines and data.count(b"\n") > min_lines:
                return bytes(data), False
    return bytes(data[:max_bytes]), False


def _cache_path(ref, name, version):
    digest = hashlib.sha1(name.encode()).hexdigest()[:16]
    ext = os.path.splitext(name)[1].lower()[:12]
    return os.path.join(CACHE_DIR, *ref.split("/"), f"v{version}", digest + ext)


def download(ref, name, version, size=None, max_bytes=None, latest=True, progress=None):
    """Path of a cached copy of the file. latest: the single-file download of the current
    version; otherwise the version's zip is downloaded and the file extracted."""
    max_bytes = max_bytes or MAX_DOWNLOAD_MB * 2 ** 20
    path = _cache_path(ref, name, version)
    if os.path.exists(path) and (size is None or os.path.getsize(path) == size):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    try:
        if latest:
            inflater = _Inflater(name)
            written = 0
            with open_url(file_url(ref, name), timeout=120) as r, open(tmp, "wb") as out:
                while True:
                    chunk = r.read(1024 * 1024)
                    data = inflater.feed(chunk) if chunk else inflater.flush()
                    if not data and not chunk:
                        break
                    written += len(data)
                    if written > max_bytes:
                        raise KaggleError("too_large", f"{name} is larger than {max_bytes // 2 ** 20:,} MB.")
                    out.write(data)
                    if progress:
                        progress(written)
        else:
            import zipfile
            archive = path + ".zip.part"
            got = 0
            with open_url(version_url(ref, version), timeout=120) as r, open(archive, "wb") as out:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > max_bytes:
                        raise KaggleError("too_large", f"Version {version} of {ref} is larger than "
                                                       f"{max_bytes // 2 ** 20:,} MB to download.")
                    out.write(chunk)
                    if progress:
                        progress(got)
            try:
                with zipfile.ZipFile(archive) as z, z.open(name) as src, open(tmp, "wb") as out:
                    while True:
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        out.write(block)
            except KeyError:
                raise KaggleError("not_found", f"{name} is not part of version {version} of {ref}.")
            finally:
                os.remove(archive)
        os.replace(tmp, path)
        return path
    finally:
        for leftover in (tmp, path + ".zip.part"):
            if os.path.exists(leftover):
                os.remove(leftover)


# -------------------------------------------------------------------- rows ---
def read_rows(info, entry, need, version=None, sheet=None, max_bytes=None, progress=None):
    """(table, meta, complete) with at least `need` rows when the file has them.
    Text files of the latest version are read from their start only."""
    fmt = entry["format"]
    latest = version is None or version == info.get("currentVersion")
    small = (entry.get("size") or 0) <= HEAD_BYTES
    if fmt in ("csv", "tsv", "jsonl") and latest and need and not small:
        data, complete = head(info["ref"], entry["name"], max_bytes=max_bytes or HEAD_BYTES, min_lines=need + 1)
        table, meta = read(data, fmt, limit=need, partial=not complete)
        return table, meta, complete
    limit = max_bytes or MAX_DOWNLOAD_MB * 2 ** 20
    path = download(info["ref"], entry["name"], info["version"], entry.get("size"), max_bytes=limit,
                    latest=latest, progress=progress)
    whole = small or not need                         # small files are read completely: exact totals
    table, meta = read(path, fmt, limit=None if whole else need, sheet=sheet)
    return table, meta, whole or table.num_rows < need


def preview(dataset, file=None, offset=0, rows=20, version=None, sheet=None, refresh=False):
    ref = check_ref(dataset)
    name = check_file(file)
    version = check_version(version)
    offset = _clamp(offset, 0, PREVIEW_ROWS - 1, 0)
    rows = _clamp(rows, 1, 100, 20)
    rows = min(rows, PREVIEW_ROWS - offset)
    sheet = (sheet or "").strip() or None

    def fetch():
        info = details(ref, version)
        entry = find_file(info, name)
        if not entry["importable"]:
            raise KaggleError("preview_unavailable", f"{name} is not a file type that can be previewed or "
                                                     f"imported (CSV, TSV, Parquet, JSON, JSONL, Excel).")
        text_head = entry["format"] in ("csv", "tsv", "jsonl") and not info["pinned"]
        if not text_head and (entry.get("size") or 0) > PREVIEW_MAX_MB * 2 ** 20:
            raise KaggleError("preview_unavailable", f"{name} is {entry['size'] / 2 ** 20:,.0f} MB; files of "
                                                     f"this type are previewed up to {PREVIEW_MAX_MB} MB.")
        table, meta, complete = read_rows(info, entry, offset + rows, version, sheet,
                                          max_bytes=HEAD_BYTES if text_head else PREVIEW_MAX_MB * 2 ** 20)
        part = table.slice(offset, rows)
        cells, truncated = [], False
        for record in part.to_pylist():
            row = []
            for column in part.column_names:
                value, cut = _cell(record.get(column))
                truncated = truncated or cut
                row.append(value)
            cells.append(row)
        return {"dataset": ref, "file": name, "version": info["version"], "offset": offset,
                "columns": part.column_names,
                "types": [{"name": f.name, "type": _short_type(f.type)} for f in part.schema],
                "rows": cells, "total": table.num_rows if complete else None, "truncated": truncated,
                "encoding": meta.get("encoding"), "sheets": meta.get("sheets"), "sheet": meta.get("sheet"),
                "partial": not complete}

    return cached("preview", {"ref": ref, "file": name, "version": version, "offset": offset, "rows": rows,
                              "sheet": sheet}, CACHE_TTL, fetch, _flag(refresh))


def status():
    from . import __version__
    return {"version": __version__, "credentials": has_credentials(), "maxRows": MAX_ROWS,
            "maxDownloadMb": MAX_DOWNLOAD_MB, "previewMaxMb": PREVIEW_MAX_MB,
            "exactCollation": os.environ.get("KAGGLE2IRIS_EXACT", "1") != "0",
            "cacheTtl": CACHE_TTL, "showPrivate": SHOW_PRIVATE}


ENDPOINTS = {"search": search, "details": details, "preview": preview, "status": status}


def call(name, args="{}", endpoints=None):
    """JSON in, JSON out, for the ObjectScript wrappers. Errors come back as
    {"error": ..., "code": ...} with credentials masked."""
    import inspect
    try:
        fn = (endpoints or ENDPOINTS).get(name)
        if fn is None:
            raise KaggleError("bad_request", f"unknown endpoint {name!r}")
        kwargs = {k: v for k, v in json.loads(args or "{}").items() if v not in ("", None)}
        unknown = set(kwargs) - set(inspect.signature(fn).parameters)
        if unknown:
            raise KaggleError("bad_request", f"unknown parameters: {', '.join(sorted(unknown))}")
        return json.dumps(fn(**kwargs), default=str)
    except KaggleError as e:
        return json.dumps({"error": error_text(e), "code": e.code})
    except LoadError as e:
        return json.dumps({"error": error_text(e), "code": "bad_request"})
    except Exception as e:
        return json.dumps({"error": error_text(e), "code": "upstream"})
