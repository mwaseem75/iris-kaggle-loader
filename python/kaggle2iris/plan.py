"""
Import plans (dry runs) and imports of Kaggle files into IRIS tables.
One file becomes one table; "all files" imports every tabular file, one table each.
"""
import json
import os

from . import kaggle, loader
from .kaggle import KaggleError, check_file, check_ref, check_version
from .loader import LimitExceeded, LoadError, TableConflict

PARAMS = ("dataset", "file", "table", "limit", "mode", "version", "sheet")
SAMPLE_ROWS = 200
DEFAULT_SCHEMA = "KG"


def default_table(name, table=None):
    """The target table of a file: the given table, else KG.<file name without extension>."""
    if table:
        return loader.safe_table(table)
    stem = os.path.splitext(os.path.basename(name))[0]
    return loader.safe_table(f"{DEFAULT_SCHEMA}.{stem}")


def _limit(value):
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise KaggleError("bad_request", "limit must be a number")
    if number < 0:
        raise KaggleError("bad_request", "limit must be 0 or more")
    return number


def _old_version(info):
    return info["pinned"] and info["version"] != info.get("currentVersion")


def _download_size(info, entry):
    """Bytes a full import downloads: the file, or the whole version zip for an older version."""
    return info.get("totalBytes") if _old_version(info) else entry.get("size")


def _entry(info, name):
    entry = kaggle.find_file(info, name)
    if not entry["importable"]:
        raise KaggleError("bad_request", f"{name} is not a file type that can be imported "
                                         f"(CSV, TSV, Parquet, JSON, JSONL, Excel).")
    return entry


def precheck(dataset, file=None, table=None, mode="replace", db=None):
    """Checks without network before a background import: the target table, or an error."""
    db = db or loader.IrisSQL()
    check_ref(dataset)
    mode = (mode or "replace").strip()
    if mode not in loader.MODES:
        raise KaggleError("bad_request", f"mode must be one of {', '.join(loader.MODES)}, not {mode!r}")
    name = check_file(file)
    if name == "*":
        if table and "." in table:
            raise KaggleError("bad_request", "With all files, table is a schema name (for example KG), "
                                             "not Schema.Table")
        return loader.safe_table(f"{table or DEFAULT_SCHEMA}.x").split(".")[0]
    target = default_table(name, table)
    try:
        loader.check_target(target, db, mode)
    except TableConflict as e:
        raise KaggleError("conflict", str(e))
    return target


def plan(dataset, file=None, table=None, limit=0, mode="replace", version=None, sheet=None, db=None):
    db = db or loader.IrisSQL()
    ref, name, version, limit = check_ref(dataset), check_file(file), check_version(version), _limit(limit)
    mode = (mode or "replace").strip()
    sheet = (sheet or "").strip() or None
    info = kaggle.details(ref, version)
    entry = _entry(info, name)
    target = default_table(name, table)
    warnings, reason = [], None
    size = _download_size(info, entry)
    if not limit:
        try:
            loader.check_limits(name, None, size)
        except LimitExceeded as e:
            reason = str(e)
    exists = db.table_exists(target)
    owned = exists and db.owns_table(target)
    if mode not in loader.MODES:
        reason = reason or f"mode must be one of {', '.join(loader.MODES)}, not {mode!r}"
    else:
        try:
            loader.check_target(target, db, mode)
        except TableConflict as e:
            reason = reason or str(e)
    action = "refused" if reason else "create" if not exists else mode

    columns, ddl, sample_rows, meta = [], None, 0, {}
    text = entry["format"] in ("csv", "tsv", "jsonl") and not _old_version(info)
    if text or (entry.get("size") or 0) <= kaggle.PREVIEW_MAX_MB * 2 ** 20:
        sample, meta, whole = kaggle.read_rows(info, entry, SAMPLE_ROWS, version, sheet,
                                           max_bytes=kaggle.HEAD_BYTES if text else kaggle.PREVIEW_MAX_MB * 2 ** 20)
        sample_rows = sample.num_rows
        ddl, names, cols = loader.build_ddl(target, sample, getattr(db, "text_collation", ""))
        for src, sql_name, col in zip(sample.column_names, names, cols):
            column = {"source": src, "name": sql_name, "type": col.sql_type}
            if sql_name.lower() != src.lower():
                column["note"] = f"renamed from {src!r}"
            columns.append(column)
        if not whole:                                    # small files are read whole: exact types
            warnings.append(f"Types and lengths come from the first {sample_rows} rows; the import checks every row.")
    else:
        warnings.append(f"The file is larger than {kaggle.PREVIEW_MAX_MB} MB, so the columns are only known "
                        f"once it is imported.")
    if meta.get("encoding") and meta["encoding"] != "utf-8":
        warnings.append(f"The file is not UTF-8; it is read as {meta['encoding']}.")
    if meta.get("sheets") and len(meta["sheets"]) > 1:
        warnings.append(f"Sheet {meta['sheet']!r} of {len(meta['sheets'])}: {', '.join(meta['sheets'])}.")
    if _old_version(info):
        warnings.append(f"Version {info['version']} is not the latest ({info.get('currentVersion')}); "
                        f"its whole archive is downloaded.")
    return {"dataset": ref, "title": info["title"], "file": name, "format": entry["format"],
            "sheet": meta.get("sheet"), "sheets": meta.get("sheets"), "version": info["version"],
            "pinned": info["pinned"], "limit": limit, "mode": mode, "table": target, "tableExists": exists,
            "ownedByLoader": bool(owned), "action": action, "reason": reason, "columns": columns, "ddl": ddl,
            "download": {"fullLoad": not limit, "bytes": size, "withinLimits": not (reason and "limit" in reason)},
            "warnings": warnings, "sampleRows": sample_rows}


def importable_files(dataset, version=None, schema=None):
    """The files an "all files" import takes, with their target tables."""
    ref, version = check_ref(dataset), check_version(version)
    info = kaggle.details(ref, version)
    schema = loader.safe_table(f"{schema or DEFAULT_SCHEMA}.x").split(".")[0]
    used, out = set(), []
    for f in info["files"]:
        if not f["importable"]:
            continue
        target = schema + "." + default_table(f["name"]).split(".", 1)[1]
        base, n = target, 1
        while target.lower() in used:                    # same file name in two folders
            n += 1
            target = f"{base}_{n}"
        used.add(target.lower())
        out.append({"file": f["name"], "table": target, "size": f["size"], "format": f["format"]})
    return {"dataset": ref, "version": info["version"], "files": out}


def load(dataset, file=None, table=None, limit=0, mode="replace", version=None, sheet=None,
         db=None, log=print, progress=None, cancelled=None, batch_size=1000):
    """Import one file, e.g. load("uciml/iris", "Iris.csv", "Demo.Iris")."""
    db = db or loader.IrisSQL()
    progress = progress or (lambda *a: None)
    ref, name, version, limit = check_ref(dataset), check_file(file), check_version(version), _limit(limit)
    sheet = (sheet or "").strip() or None

    def check_cancel():
        if cancelled and cancelled():
            raise loader.Cancelled("Import cancelled")

    progress("resolving", 0, 0)
    info = kaggle.details(ref, version)
    entry = _entry(info, name)
    target = default_table(name, table)
    if not limit:
        loader.check_limits(name, None, _download_size(info, entry))
    loader.check_target(target, db, mode)
    check_cancel()
    log(f"Fetching {ref} / {name} (version {info['version']})" + (f" limit {limit}" if limit else ""))
    progress("fetching", 0, 0)
    expected = _download_size(info, entry) or 0          # bytes; the UI shows them while phase is "fetching"
    data, meta, complete = kaggle.read_rows(info, entry, limit or None, version, sheet,
                                            max_bytes=loader.MAX_DOWNLOAD_MB * 2 ** 20,
                                            progress=lambda done: progress("fetching", done, max(done, expected)))
    if limit and data.num_rows > limit:
        data = data.slice(0, limit)                     # small files are read whole
    if limit and data.num_rows < limit and not complete:
        raise LimitExceeded(f"Only {data.num_rows:,} rows of {name} fit into the {loader.MAX_DOWNLOAD_MB:,} MB "
                            f"download limit; lower the row limit or raise KAGGLE2IRIS_MAX_DOWNLOAD_MB.")
    if not limit and data.num_rows > loader.MAX_ROWS:
        raise LimitExceeded(f"{name} has {data.num_rows:,} rows, more than the {loader.MAX_ROWS:,} row limit "
                            f"for a full load. Set a row limit, or raise KAGGLE2IRIS_MAX_ROWS.")
    check_cancel()
    result = loader.load_dataframe(data, target, db, mode, batch_size, log, progress, cancelled)
    progress("finishing", result["rows"], result["rows"])
    result.update({"dataset": ref, "title": info["title"], "file": name, "sheet": meta.get("sheet"),
                   "version": info["version"], "pinned": info["pinned"], "encoding": meta.get("encoding")})
    return result


def _json_call(fn, args, allowed):
    try:
        kwargs = {k: v for k, v in json.loads(args or "{}").items() if v not in ("", None)}
        unknown = set(kwargs) - set(allowed)
        if unknown:
            raise KaggleError("bad_request", f"unknown parameters: {', '.join(sorted(unknown))}")
        return json.dumps(fn(**kwargs), default=str)
    except Exception as e:
        code = e.code if isinstance(e, KaggleError) else "bad_request" if isinstance(e, LoadError) else "upstream"
        return json.dumps({"error": loader.error_text(e), "code": code})


def call(args="{}"):
    """JSON in, JSON out, for Loader.Plan."""
    return _json_call(plan, args, PARAMS)


def call_precheck(args="{}"):
    return _json_call(lambda **kw: {"table": precheck(**kw)}, args, ("dataset", "file", "table", "mode"))


def call_files(args="{}"):
    return _json_call(importable_files, args, ("dataset", "version", "schema"))
