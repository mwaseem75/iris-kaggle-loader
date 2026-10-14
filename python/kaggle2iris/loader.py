"""
kaggle2iris - load Kaggle datasets into InterSystems IRIS tables.

Runs inside IRIS embedded Python (uses the `iris` module), but the SQL layer is
pluggable so the logic can also be tested outside IRIS.
"""
import base64
import datetime
import json
import math
import os
import re
import time
import unicodedata

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

MODES = ("replace", "append", "fail")
# caps for loads without a row limit
MAX_ROWS = int(os.environ.get("KAGGLE2IRIS_MAX_ROWS", 1_000_000))
MAX_DOWNLOAD_MB = int(os.environ.get("KAGGLE2IRIS_MAX_DOWNLOAD_MB", 500))


class LoadError(ValueError):
    """Expected failure; the message is shown to the user as is."""


class LimitExceeded(LoadError):
    """A full load over KAGGLE2IRIS_MAX_ROWS / KAGGLE2IRIS_MAX_DOWNLOAD_MB."""


class TableConflict(LoadError):
    """The target table exists and may not be changed (mode 'fail', or not ours)."""


class Cancelled(LoadError):
    pass


RESERVED = set("""
add all alter and any as asc between by case cast check column constraint count
create cross current date default delete desc distinct drop else end escape exists
false for foreign from full group having id in index inner insert into is join key
label language left level like max min name not null of on or order outer position
primary references right select set size some sum table text then time timestamp
to top true type union unique update user using value values view when where with
""".split())


# ---------------------------------------------------------------- naming ---
def is_reserved(word, own_list=True):
    """IRIS reserved word, or in RESERVED (kept so old column names stay text_, label_)."""
    if own_list and word.lower() in RESERVED:
        return True
    try:
        import iris  # noqa: available only inside IRIS
        return bool(iris.system.SQL.IsReservedWord(word))
    except Exception:
        return False


def ascii_name(text):
    """'Café' -> 'Cafe'"""
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")


def safe_column(name, used):
    """Turn any dataset column name into a valid, unique SQL column name."""
    col = re.sub(r"[^A-Za-z0-9_]", "_", ascii_name(name)).strip("_") or "col"
    if col[0].isdigit():
        col = "c_" + col
    if is_reserved(col):
        col = col + "_"
    base, n = col, 1
    while col.lower() in used:
        n += 1
        col = f"{base}_{n}"
    used.add(col.lower())
    return col


def safe_table(name):
    """'Demo.Iris' -> 'Demo.Iris'; 'iris' -> 'KG.iris'."""
    if "." not in name:
        name = "KG." + name
    schema, table = name.split(".", 1)
    def clean(s):
        s = re.sub(r"[^A-Za-z0-9_]", "_", ascii_name(s)).strip("_") or "t"
        s = "t_" + s if s[0].isdigit() else s
        return s + "_" if is_reserved(s, own_list=False) else s
    return f"{clean(schema)}.{clean(table)}"


# ----------------------------------------------------------- type mapping ---
# Types come from the Arrow schema, not pandas (pandas turns ints with nulls into floats).
MIN_VARCHAR = 4000
LONG_VALUE = 1_000_000        # longer values -> LONGVARCHAR
ROW_BUDGET = 3_000_000        # an IRIS row must fit in one string (3,641,144 chars)
INT64_MAX = 2 ** 63 - 1


class Column:
    def __init__(self, sql_type, values, lengths=None):
        self.sql_type = sql_type
        self.values = values          # None = NULL
        self.lengths = lengths        # text columns: UTF-16 length of each value


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return base64.b64encode(o).decode("ascii")
    if hasattr(o, "tolist"):          # numpy arrays and scalars
        return o.tolist()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def _json(v):
    return json.dumps(v, default=_json_default, ensure_ascii=False)


def _is_null(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def to_arrow_array(series):
    """A pandas column as an Arrow array; mixed Python objects become JSON text."""
    try:
        return pa.array(series, from_pandas=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
        return pa.array([None if _is_null(v) else _json(v) for v in series], pa.string())


def to_arrow(data):
    """pandas DataFrame or pyarrow Table -> pyarrow Table."""
    if isinstance(data, pa.Table):
        return data
    return pa.table([to_arrow_array(data[c]) for c in data.columns],
                    names=[str(c) for c in data.columns])


def _text(values):
    """VARCHAR column sized to its longest value (at least MIN_VARCHAR)."""
    lengths = np.array([0 if v is None else len(v.encode("utf-16-le")) // 2 for v in values],
                       dtype=np.int64)
    longest = int(lengths.max()) if len(lengths) else 0
    if longest > LONG_VALUE:
        return Column("LONGVARCHAR", values, lengths)
    return Column(f"VARCHAR({max(MIN_VARCHAR, -(-longest // 1000) * 1000)})", values, lengths)


def sql_column(arr):
    """IRIS SQL type and bind values for one Arrow column."""
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    t = arr.type
    if pa.types.is_dictionary(t):
        arr = arr.dictionary_decode()
        t = arr.type
    if pa.types.is_null(t):
        return Column(f"VARCHAR({MIN_VARCHAR})", [None] * len(arr), np.zeros(len(arr), dtype=np.int64))
    if pa.types.is_boolean(t):
        return Column("BIT", [None if v is None else int(v) for v in arr.to_pylist()])
    if pa.types.is_integer(t):
        vals = arr.to_pylist()
        if not pa.types.is_uint64(t) or all(v is None or v <= INT64_MAX for v in vals):
            return Column("BIGINT", vals)
        return _text([None if v is None else str(v) for v in vals])
    if pa.types.is_floating(t):
        vals = arr.to_pylist()
        if not pa.types.is_float64(t):
            # float32 1.4 would otherwise become 1.399999976158142
            narrow = np.float32 if pa.types.is_float32(t) else np.float16
            vals = [None if v is None else float(str(narrow(v))) for v in vals]
        if any(v is not None and math.isinf(v) for v in vals):
            return _text([None if v is None or math.isnan(v) else repr(v) for v in vals])
        return Column("DOUBLE", [None if v is None or math.isnan(v) else v for v in vals])
    if pa.types.is_decimal(t):
        vals = [None if v is None else str(v) for v in arr.to_pylist()]
        if t.precision <= 18:
            return Column(f"NUMERIC({t.precision},{t.scale})", vals)
        return _text(vals)
    if pa.types.is_date(t):
        return Column("DATE", arr.cast(pa.date32()).to_pylist())
    if pa.types.is_timestamp(t):
        vals = pc.cast(arr, pa.timestamp("us"), safe=False).to_pylist()
        return Column("TIMESTAMP", [None if v is None else v.isoformat(sep=" ") for v in vals])
    is_string_view = getattr(pa.types, "is_string_view", lambda _: False)
    if pa.types.is_string(t) or pa.types.is_large_string(t) or is_string_view(t):
        return _text(arr.to_pylist())
    if pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
        return _text([None if v is None else base64.b64encode(v).decode("ascii") for v in arr.to_pylist()])
    if pa.types.is_nested(t):         # stored as JSON
        return _text([None if v is None else _json(v) for v in arr.to_pylist()])
    return _text([None if v is None else str(v) for v in arr.to_pylist()])   # time, duration, ...


def fit_rows(columns):
    """Move the widest VARCHAR columns to LONGVARCHAR until every row fits ROW_BUDGET."""
    text = [c for c in columns if c.sql_type.startswith("VARCHAR")]
    if not text or not len(text[0].lengths):
        return
    totals = sum(c.lengths for c in text)
    while text and int(totals.max()) > ROW_BUDGET:
        widest = max(text, key=lambda c: int(c.lengths.max()))
        widest.sql_type = "LONGVARCHAR"
        totals = totals - widest.lengths
        text.remove(widest)


def sql_type(data):
    """IRIS SQL type of a pandas Series or Arrow array."""
    arr = data if isinstance(data, (pa.Array, pa.ChunkedArray)) else to_arrow_array(data)
    return sql_column(arr).sql_type


# ---------------------------------------------------------- SQL backends ---
class IrisSQL:
    """Embedded Python backend (inside IRIS)."""

    def __init__(self):
        import iris  # noqa: available only inside IRIS
        self.iris = iris

    def execute(self, sql, *args):
        return self.iris.sql.exec(sql, *args)

    # iris.sql binds logical values: "" is NULL, "\x00" is '', DATE is $h
    HOROLOG_EPOCH = datetime.date(1840, 12, 31)

    @classmethod
    def bind(cls, v):
        if v is None:
            return ""
        if isinstance(v, str):
            return v or "\x00"
        if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
            return (v - cls.HOROLOG_EPOCH).days
        return v

    # default SQLUPPER ignores case and trailing spaces
    text_collation = "" if os.environ.get("KAGGLE2IRIS_EXACT", "1") == "0" else " COLLATE %EXACT"

    def executemany(self, sql, rows, first_row=1):
        stmt = self.iris.sql.prepare(sql)
        bind = self.bind
        for i, r in enumerate(rows):
            try:
                stmt.execute(*[bind(v) for v in r])
            except Exception as e:
                raise LoadError(f"Row {first_row + i:,} was rejected by IRIS: {e}") from e

    # a savepoint inside the caller's transaction, so a rollback leaves its work alone
    def begin(self):
        self.nested = self.iris.execute("return $TLEVEL") > 0
        self.execute("SAVEPOINT KAGGLE2IRIS" if self.nested else "START TRANSACTION")

    def commit(self):
        if self.nested:
            self.iris.execute("tcommit")
        else:
            self.execute("COMMIT")

    def rollback(self):
        self.execute("ROLLBACK TO SAVEPOINT KAGGLE2IRIS" if self.nested else "ROLLBACK")

    def table_exists(self, table):
        schema, name = table.split(".", 1)
        rs = self.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", schema, name)
        return list(rs)[0][0] > 0

    # older tables have no marker, only an ImportLog entry
    def owns_table(self, table):
        if self.iris.gref("^dc.kaggle2iris.Owned").data([table.upper()]):
            return True
        try:
            rs = self.execute("SELECT COUNT(*) FROM dc_kaggle2iris.ImportLog "
                              "WHERE UPPER(TableName) = ? AND Status = 'OK'", table.upper())
            return list(rs)[0][0] > 0
        except Exception:  # no ImportLog outside the IPM module
            return False

    def columns(self, table):
        schema, name = table.split(".", 1)
        rs = self.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? "
                          "AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION", schema, name)
        return [r[0] for r in rs]

    def mark_owned(self, table):
        self.iris.gref("^dc.kaggle2iris.Owned")[table.upper()] = 1

    def unmark_owned(self, table):
        self.iris.gref("^dc.kaggle2iris.Owned").kill([table.upper()])


# ------------------------------------------------------------- core API ---
def build_ddl(table, data, text_collation=""):
    """CREATE TABLE statement, SQL column names and Column objects for a
    pandas DataFrame or pyarrow Table."""
    data = to_arrow(data)
    used = set()
    cols = [safe_column(c, used) for c in data.column_names]
    columns = [sql_column(a) for a in data.columns]
    fit_rows(columns)
    collate = lambda t: text_collation if t.startswith("VARCHAR") else ""
    body = ",\n  ".join(f"{c} {col.sql_type}{collate(col.sql_type)}"
                        for c, col in zip(cols, columns))
    return f"CREATE TABLE {table} (\n  {body}\n)", cols, columns


def check_target(table, db, mode):
    """(exists, owned) for the target, or TableConflict if it may not be changed."""
    if mode not in MODES:
        raise LoadError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
    exists = db.table_exists(table)
    owned = exists and db.owns_table(table)
    if exists and mode == "fail":
        raise TableConflict(f"Table {table} already exists (mode 'fail')")
    if exists and not owned:
        raise TableConflict(f"Table {table} already exists and was not created by kaggle2iris, so it "
                            f"is not replaced or appended to. Choose another table name.")
    return exists, owned


def load_dataframe(df, table, db, mode="replace", batch_size=1000, log=print,
                   progress=None, cancelled=None):
    """Create (or replace/append) `table` from a pandas DataFrame or pyarrow
    Table and insert all rows. progress(phase, done, total) is called per batch;
    cancelled() is checked before each batch."""
    if mode not in MODES:
        raise LoadError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
    table = safe_table(table)
    ddl, cols, columns = build_ddl(table, df, getattr(db, "text_collation", ""))

    exists = exists_before = check_target(table, db, mode)[0]
    if exists and mode == "replace":
        db.execute(f"DROP TABLE {table}")
        exists = False
    created = not exists
    if created:
        db.execute(ddl)
        db.mark_owned(table)
        log(f"Created {table} ({len(cols)} columns)")

    ins = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    t0, total = time.time(), len(columns[0].values)
    count = lambda: list(db.execute(f"SELECT COUNT(*) FROM {table}"))[0][0]
    before = 0 if created else count()
    progress = progress or (lambda *a: None)
    db.begin()
    try:
        progress("inserting", 0, total)
        for start in range(0, total, batch_size):
            if cancelled and cancelled():
                raise Cancelled("Import cancelled")
            rows = list(zip(*(c.values[start:start + batch_size] for c in columns)))
            db.executemany(ins, rows, start + 1)
            log(f"  {min(start + batch_size, total):,}/{total:,} rows")
            progress("inserting", min(start + batch_size, total), total)
        arrived = count() - before
        if arrived != total:
            raise LoadError(f"{total:,} rows were read but {arrived:,} arrived in {table}")
        db.commit()
    except Exception as e:
        db.rollback()
        if created:
            db.execute(f"DROP TABLE {table}")
            db.unmark_owned(table)
            replaced = mode == "replace" and exists_before
            note = f" {table} was removed." if replaced else f" {table} was not created."
        else:
            note = f" The rows of this import were rolled back; {table} is unchanged."
        raise (Cancelled if isinstance(e, Cancelled) else LoadError)(error_text(e) + note) from e
    secs = round(time.time() - t0, 2)
    types = {c: col.sql_type for c, col in zip(cols, columns)}
    return {"table": table, "rows": total, "columns": types, "seconds": secs, "ddl": ddl}


def check_limits(what, rows, size):
    """LimitExceeded when a full load would be too big (rows or bytes may be None)."""
    if rows and rows > MAX_ROWS:
        raise LimitExceeded(f"{what} has {rows:,} rows, more than the {MAX_ROWS:,} row limit for a full "
                            f"load. Set a row limit, or raise KAGGLE2IRIS_MAX_ROWS.")
    if size and size > MAX_DOWNLOAD_MB * 2 ** 20:
        raise LimitExceeded(f"Loading {what} downloads about {size / 2 ** 20:,.0f} MB, more than the "
                            f"{MAX_DOWNLOAD_MB:,} MB limit. Raise KAGGLE2IRIS_MAX_DOWNLOAD_MB to allow it.")


SECRETS = ("KAGGLE_API_TOKEN", "KAGGLE_KEY")


def error_text(e):
    """One readable line for an exception, with Kaggle credentials masked."""
    text = str(e) if isinstance(e, LoadError) else f"{type(e).__name__}: {' '.join(str(e).split())}"
    for name in SECRETS:
        secret = os.environ.get(name, "")
        if len(secret) >= 4:
            text = text.replace(secret, "***")
    return text
