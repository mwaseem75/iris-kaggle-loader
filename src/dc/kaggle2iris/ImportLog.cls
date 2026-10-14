/// History of every import run.
Class dc.kaggle2iris.ImportLog Extends %Persistent
{

/// Kaggle dataset id, e.g. uciml/iris
Property Dataset As %String(MAXLEN = 200);

/// File in the dataset, e.g. Iris.csv
Property FileName As %String(MAXLEN = 500);

/// Excel sheet ("" for other file types)
Property Sheet As %String(MAXLEN = 200);

Property TableName As %String(MAXLEN = 200);

Property RowCount As %Integer;

Property Seconds As %Numeric(SCALE = 2);

Property Status As %String(VALUELIST = ",OK,ERROR");

Property Message As %String(MAXLEN = 2000);

Property ColumnsJSON As %String(MAXLEN = "");

/// Dataset version the data was read from
Property Version As %String(MAXLEN = 20);

/// Text encoding of CSV, TSV and JSON files
Property Encoding As %String(MAXLEN = 20);

Property CreatedAt As %TimeStamp [ InitialExpression = {$zdatetime($horolog,3)} ];

Index TableIdx On TableName;

ClassMethod Record(r As %DynamicObject, status As %String, msg As %String, Output id As %String) As %Status
{
    set id = ""
    set o = ..%New()
    set o.Dataset = r.dataset, o.FileName = r.file, o.Sheet = r.sheet
    set o.Version = r.version, o.Encoding = r.encoding
    set o.TableName = r.table, o.RowCount = r.rows, o.Seconds = r.seconds
    set o.Status = status, o.Message = $extract(msg, 1, 2000)
    set:$isobject(r.columns) o.ColumnsJSON = r.columns.%ToJSON()
    set sc = o.%Save()
    set:$$$ISOK(sc) id = o.%Id()
    quit sc
}

/// Only tables created by this tool may be previewed through the API.
ClassMethod IsKnownTable(table As %String) As %Boolean
{
    &sql(SELECT COUNT(*) INTO :n FROM dc_kaggle2iris.ImportLog WHERE TableName = :table AND Status = 'OK')
    quit (SQLCODE = 0) && (n > 0)
}

}
