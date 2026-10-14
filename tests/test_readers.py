"""File readers: CSV/TSV typing, encodings, JSON, JSONL, Parquet and Excel. No network."""
import datetime
import io
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from kaggle2iris import readers
from kaggle2iris.loader import LoadError
from kaggle2iris.readers import file_format, infer, read


def test_file_format():
    assert file_format("Iris.csv") == "csv"
    assert file_format("data/Train.TSV") == "tsv"
    assert file_format("x.parquet") == "parquet"
    assert file_format("x.ndjson") == "jsonl"
    assert file_format("book.xlsx") == "excel"
    assert file_format("db.sqlite") is None
    assert file_format("image.png") is None


def test_infer_column_types():
    assert infer(["1", "2", None]).type == pa.int64()
    assert infer(["1", "2.5", "-3e2"]).type == pa.float64()
    assert infer(["true", "False", None]).type == pa.bool_()
    assert infer(["2024-01-15", "2023-12-31"]).type == pa.date32()
    assert infer(["2024-01-15 10:20:30", "2024-01-15T10:20"]).type == pa.timestamp("us")
    assert infer([None, None]).type == pa.string()


def test_leading_zeros_stay_text():
    # zip codes, ids: a number type would lose the zeros
    assert infer(["01003", "1004"]).type == pa.string()
    assert infer(["007.5", "1"]).type == pa.string()
    assert infer(["0", "0.25", "10"]).type == pa.float64()


def test_null_markers_in_numeric_columns():
    col = infer(["1", "NA", "n/a", "", "3"])
    assert col.type == pa.int64()
    assert col.to_pylist() == [1, None, None, None, 3]
    assert infer(["NA", "x"]).to_pylist() == ["NA", "x"]               # text keeps the marker


def test_out_of_range_ints_become_double():
    assert infer(["1", str(2 ** 64)]).type == pa.float64()


def test_bad_dates_stay_text():
    assert infer(["2023-02-30", "2023-01-01"]).type == pa.string()


def test_timestamps_with_zones_are_utc():
    col = infer(["2024-01-15T10:00:00Z", "2024-01-15 12:00:00+02:00", "2024-01-15 10:00 UTC"])
    assert col.to_pylist() == [datetime.datetime(2024, 1, 15, 10, 0)] * 3


def test_csv_read():
    data = b"id,name,score,joined\n1,Ann,9.5,2024-01-01\n2,Bob,,2024-02-01\n"
    table, info = read(data, "csv")
    assert table.column_names == ["id", "name", "score", "joined"]
    assert [f.type for f in table.schema] == [pa.int64(), pa.string(), pa.float64(), pa.date32()]
    assert table.column("score").to_pylist() == [9.5, None]
    assert info == {"encoding": "utf-8", "truncated": False}


def test_csv_quotes_and_newlines():
    data = b'a,b\n"x, y","line1\nline2"\n'
    table, _ = read(data, "csv")
    assert table.to_pylist() == [{"a": "x, y", "b": "line1\nline2"}]


def test_tsv_and_blank_lines():
    table, _ = read(b"a\tb\n1\t2\n\n3\t4\n", "tsv")
    assert table.num_rows == 2


def test_csv_limit():
    data = b"n\n" + b"".join(b"%d\n" % i for i in range(100))
    table, _ = read(data, "csv", limit=10)
    assert table.column("n").to_pylist() == list(range(10))


def test_csv_partial_drops_the_cut_line():
    table, info = read(b"a,b\n1,2\n3,4\n5,", "csv", partial=True)
    assert table.num_rows == 2 and info["truncated"] is True


def test_csv_ragged_row_names_the_line():
    with pytest.raises(LoadError, match="Line 3 has 3 fields, the header has 2"):
        read(b"a,b\n1,2\n3,4,5\n", "csv")


def test_csv_encodings():
    table, info = read("name\nCafé\n".encode("utf-8-sig"), "csv")
    assert table.column("name").to_pylist() == ["Café"] and info["encoding"] == "utf-8"
    table, info = read("name\nCafé – Bar\n".encode("cp1252"), "csv")
    assert table.column("name").to_pylist() == ["Café – Bar"] and info["encoding"] == "cp1252"
    table, info = read(b"name\n\x81x\n", "csv")                     # 0x81 is not in cp1252
    assert info["encoding"] == "latin-1"


def test_empty_csv():
    with pytest.raises(LoadError, match="empty"):
        read(b"", "csv")


def test_json_list_and_wrapped_list():
    table, _ = read(b'[{"a": 1, "b": "x"}, {"a": 2}]', "json")
    assert table.to_pylist() == [{"a": 1, "b": "x"}, {"a": 2, "b": None}]
    table, _ = read(b'{"meta": {"v": 1}, "rows": [{"a": 1}, {"a": 2}]}', "json", limit=1)
    assert table.to_pylist() == [{"a": 1}]
    with pytest.raises(LoadError, match="list of records"):
        read(b'{"a": [1], "b": [2]}', "json")
    with pytest.raises(LoadError, match="not valid JSON"):
        read(b"{nope", "json")


def test_json_mixed_types_become_text():
    table, _ = read(b'[{"v": 1}, {"v": "x"}, {"v": {"k": 2}}]', "json")
    assert table.column("v").to_pylist() == ["1", '"x"', '{"k": 2}']


def test_jsonl():
    table, _ = read(b'{"a": 1}\n\n{"a": 2}\n{"a": 3}\n', "jsonl", limit=2)
    assert table.column("a").to_pylist() == [1, 2]
    table, _ = read(b'{"a": 1}\n{"a": 2}\n{"a"', "jsonl", partial=True)
    assert table.num_rows == 2
    with pytest.raises(LoadError, match="Line 2 is not valid JSON"):
        read(b'{"a": 1}\nnope\n', "jsonl")


def test_parquet(tmp_path):
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"x": list(range(1000)), "y": ["a"] * 1000}), path, row_group_size=100)
    table, _ = read(str(path), "parquet")
    assert table.num_rows == 1000
    table, _ = read(str(path), "parquet", limit=150)
    assert table.num_rows == 150 and table.schema.field("x").type == pa.int64()
    buf = io.BytesIO()
    pq.write_table(pa.table({"x": [1]}), buf)
    assert read(buf.getvalue(), "parquet")[0].num_rows == 1
    with pytest.raises(LoadError, match="Parquet"):
        read(b"not parquet", "parquet")


def test_binary_formats_cannot_be_partial():
    with pytest.raises(LoadError, match="completely"):
        read(b"x", "parquet", partial=True)


def test_excel(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sales"
    sheet.append(["region", "units", "price", "when", "ok", None])
    sheet.append(["North", 3, 2.5, datetime.datetime(2024, 1, 2, 9, 30), True])
    sheet.append(["South", 4, 3, datetime.datetime(2024, 1, 3), False])
    sheet.append([None, None, None, None, None])
    other = book.create_sheet("Notes")
    other.append(["note"])
    other.append(["hello"])
    path = tmp_path / "b.xlsx"
    book.save(path)

    table, info = read(str(path), "excel")
    assert info == {"sheets": ["Sales", "Notes"], "sheet": "Sales"}
    assert table.column_names == ["region", "units", "price", "when", "ok"]   # empty trailing header dropped
    assert table.num_rows == 2                                                  # empty row skipped
    assert [f.type for f in table.schema] == [pa.string(), pa.int64(), pa.float64(), pa.timestamp("us"), pa.bool_()]
    table, info = read(path.read_bytes(), "excel", sheet="Notes")
    assert table.to_pylist() == [{"note": "hello"}] and info["sheet"] == "Notes"
    with pytest.raises(LoadError, match="Sheet 'Nope' not found. Sheets: Sales, Notes"):
        read(str(path), "excel", sheet="Nope")


def test_excel_numbers_saved_as_text(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    for row in (["rank", "zip"], ["1", "01003"], ["2", "02134"]):
        book.active.append(row)
    path = tmp_path / "t.xlsx"
    book.save(path)
    table, _ = read(str(path), "excel")
    assert table.schema.field("rank").type == pa.int64() and table.column("zip").to_pylist() == ["01003", "02134"]


def test_unsupported_format():
    with pytest.raises(LoadError, match="cannot be imported"):
        read(b"x", "sqlite")


def test_reader_module_has_no_network_imports():
    src = open(readers.__file__, encoding="utf-8").read()
    assert "urllib" not in src and "socket" not in src
