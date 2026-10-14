/// One-line import of Kaggle datasets into IRIS tables.
/// <example>
/// do ##class(dc.kaggle2iris.Loader).Load("uciml/iris","Iris.csv","Demo.Iris")
/// </example>
Class dc.kaggle2iris.Loader
{

/// Load one file of a Kaggle dataset into a table.
/// dataset : Kaggle id, e.g. "uciml/iris"
/// file    : file in the dataset, e.g. "Iris.csv" (CSV, TSV, Parquet, JSON, JSONL, Excel)
/// table   : target table "Schema.Table" (default KG.&lt;file name&gt;)
/// limit   : max rows (0 = all)
/// mode    : "replace" | "append" | "fail" (only tables created by kaggle2iris are changed)
/// version : dataset version number ("" = latest)
/// sheet   : Excel sheet ("" = the first)
ClassMethod Load(dataset As %String, file As %String, table As %String = "", limit As %Integer = 0, mode As %String = "replace", version As %String = "", sheet As %String = "", Output result As %DynamicObject) As %Status
{
    set sc = $$$OK, result = ""
    try {
        set result = ..Run(dataset, file, table, limit, mode, version, sheet, 1)
        write !,"Loaded ",result.rows," rows into ",result.table," in ",result.seconds,"s (version ",result.version,")",!
    } catch ex {
        set sc = ex.AsStatus()
        write !,"Import failed: ",..ErrorText(ex),!
    }
    quit sc
}

/// Same as Load but returns the result object (used by the REST API, no terminal output).
ClassMethod LoadQuiet(dataset As %String, file As %String, table As %String = "", limit As %Integer = 0, mode As %String = "replace", version As %String = "", sheet As %String = "") As %DynamicObject
{
    quit ..Run(dataset, file, table, limit, mode, version, sheet, 0)
}

/// Every tabular file of a dataset, one table each: schema.&lt;file name&gt; (schema default KG).
/// Stops at the first file that fails; the tables imported before it stay.
ClassMethod LoadAll(dataset As %String, schema As %String = "", limit As %Integer = 0, mode As %String = "replace", version As %String = "", Output result As %DynamicObject) As %Status
{
    set sc = $$$OK, result = {"dataset":(dataset), "tables":[]}
    try {
        set files = ..Files(dataset, version, schema)
        set iter = files.files.%GetIterator()
        while iter.%GetNext(, .f) {
            set r = ..Run(dataset, f.file, f.table, limit, mode, version, "", 1)
            do result.tables.%Push(r)
            write "Loaded ",r.rows," rows into ",r.table,!
        }
    } catch ex {
        set sc = ex.AsStatus()
        write !,"Import failed: ",..ErrorText(ex),!
    }
    quit sc
}

/// SQL function: SELECT dc_kaggle2iris.load_dataset('uciml/iris','Iris.csv','Demo.Iris')
/// Arguments as in Load; trailing ones can be left out. Returns the import result as JSON
/// with "status":"OK"; a failed import returns {"status":"ERROR","error":...} instead of an SQL error.
ClassMethod SqlLoad(dataset As %String(MAXLEN="") = "", file As %String(MAXLEN="") = "", table As %String(MAXLEN="") = "", limit As %Integer = 0, mode As %String(MAXLEN="") = "", version As %String(MAXLEN="") = "", sheet As %String(MAXLEN="") = "") As %String(MAXLEN="") [ SqlName = load_dataset, SqlProc ]
{
    try {
        set r = ..LoadQuiet(..Arg(dataset), ..Arg(file), ..Arg(table), +limit, ..Arg(mode), ..Arg(version), ..Arg(sheet))
        set r.status = "OK"
    } catch ex {
        set r = {"status":"ERROR", "error":(..ErrorText(ex))}
    }
    quit r.%ToJSON()
}

/// SQL function: SELECT dc_kaggle2iris.dataset_info('uciml/iris')
/// Title, versions and files of a dataset as JSON, without downloading data.
ClassMethod SqlInfo(dataset As %String(MAXLEN="") = "") As %String(MAXLEN="") [ SqlName = dataset_info, SqlProc ]
{
    try { set r = ..Info(..Arg(dataset)) }
    catch ex { set r = {"error":(..ErrorText(ex))} }
    quit r.%ToJSON()
}

/// Import plan (dry run): what Load would create, as JSON. Nothing is written.
/// args: {dataset, file, table, limit, mode, version, sheet}
ClassMethod Plan(args As %DynamicObject) As %String
{
    quit ..PlanPy(args.%ToJSON())
}

/// Title, versions and files of a dataset.
ClassMethod Info(dataset As %String) As %DynamicObject
{
    if $zstrip(dataset, "<>W") = "" $$$ThrowStatus($$$ERROR($$$GeneralError, "dataset is required"))
    set r = {}.%FromJSON(..InfoPy(dataset))
    if r.error '= "" $$$ThrowStatus($$$ERROR($$$GeneralError, r.error))
    quit r
}

/// The files an "all files" import takes, with their target tables.
ClassMethod Files(dataset As %String, version As %String = "", schema As %String = "") As %DynamicObject
{
    set r = {}.%FromJSON(..FilesPy({"dataset":(dataset), "version":(version), "schema":(schema)}.%ToJSON()))
    if r.error '= "" $$$ThrowStatus($$$ERROR($$$GeneralError, r.error))
    quit r
}

/// The error message without the "ERROR #5001: " prefix of general errors.
ClassMethod ErrorText(ex As %Exception.AbstractException) As %String
{
    set t = $system.Status.GetErrorText(ex.AsStatus())
    quit $select($extract(t, 1, 13) = "ERROR #5001: ":$extract(t, 14, *), 1:t)
}

/// SQL passes NULL as "" and '' as $c(0); both mean "not given".
ClassMethod Arg(v As %String) As %String [ Internal ]
{
    quit $select(v = $char(0):"", 1:$zstrip(v, "<>W"))
}

/// The import behind Load, LoadQuiet, SqlLoad and background jobs. Only one import at a
/// time may write to a table. jobId: report progress to dc.kaggle2iris.Job and honour cancel.
ClassMethod Run(dataset, file, table, limit, mode, version, sheet, verbose, jobId As %String = "", Output logId As %String, Output cancelled As %Boolean) As %DynamicObject [ Internal ]
{
    set:mode="" mode = "replace"
    set (logId, locked) = "", cancelled = 0
    try {
        if $zstrip(dataset, "<>W") = "" $$$ThrowStatus($$$ERROR($$$GeneralError, "dataset is required"))
        if $zstrip(file, "<>W") = "" $$$ThrowStatus($$$ERROR($$$GeneralError, "file is required"))
        set target = ..TargetPy(file, table)
        lock +^dc.kaggle2iris.Table($zconvert(target, "U")):0
        if '$test $$$ThrowStatus($$$ERROR($$$GeneralError, "Another import into "_target_" is running. Try again when it has finished."))
        set locked = $zconvert(target, "U")
        set result = {}.%FromJSON(..LoadPy(dataset, file, table, +limit, mode, version, sheet, verbose, jobId))
        if result.error '= "" {
            set cancelled = +result.cancelled
            $$$ThrowStatus($$$ERROR($$$GeneralError, result.error))
        }
        do ##class(dc.kaggle2iris.ImportLog).Record(result, "OK", "", .logId)
    } catch ex {
        do ##class(dc.kaggle2iris.ImportLog).Record({"dataset":(dataset),"file":(file),"sheet":(sheet),"table":(table),"version":(version)}, "ERROR", ..ErrorText(ex), .logId)
        lock:locked'="" -^dc.kaggle2iris.Table(locked)
        throw ex
    }
    lock -^dc.kaggle2iris.Table(locked)
    quit result
}

ClassMethod TargetPy(file, table) As %String [ Internal, Language = python ]
{
from kaggle2iris import plan
return plan.default_table(file, table or None)
}

ClassMethod PlanPy(args As %String) As %String [ Internal, Language = python ]
{
from kaggle2iris import plan
return plan.call(args)
}

ClassMethod FilesPy(args As %String) As %String [ Internal, Language = python ]
{
from kaggle2iris import plan
return plan.call_files(args)
}

ClassMethod LoadPy(dataset, file, table, limit, mode, version, sheet, verbose, jobId = "") As %String [ Language = python ]
{
import json, kaggle2iris
log = print if verbose else (lambda *a: None)
progress = cancelled = None
if jobId:
    import iris
    job = iris.cls("dc.kaggle2iris.Job")

    def progress(phase, done=0, total=0):
        job.Progress(jobId, phase, done or 0, total or 0)

    def cancelled():
        return bool(job.IsCancelled(jobId))
try:
    r = kaggle2iris.load(dataset, file, table or None, int(limit or 0), mode or "replace", version or None,
                         sheet or None, log=log, progress=progress, cancelled=cancelled)
except Exception as e:
    r = {"error": kaggle2iris.error_text(e), "cancelled": isinstance(e, kaggle2iris.loader.Cancelled)}
return json.dumps(r, default=str)
}

ClassMethod InfoPy(dataset) As %String [ Language = python ]
{
import json
from kaggle2iris import kaggle
r = json.loads(kaggle.call("details", json.dumps({"dataset": dataset})))
if "error" not in r:
    r = {k: r.get(k) for k in ("ref", "title", "version", "versions", "license", "totalBytes", "files", "url")}
return json.dumps(r, default=str)
}

}
