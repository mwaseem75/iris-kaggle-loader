/// Background imports. Start() returns at once; the import runs in its own process
/// through the same code as Loader.Load and reports its progress here. Every method
/// returns JSON; errors come back as {"error":...,"code":...}.
/// After creation, rows are only changed with column UPDATEs, so the import process
/// and a cancel request from another process never overwrite each other.
/// File "*" imports every tabular file of the dataset, one table each; then the table
/// argument is the schema (default KG).
Class dc.kaggle2iris.Job Extends %Persistent
{

Property State As %String(VALUELIST = ",queued,running,ok,error,cancelled,interrupted") [ InitialExpression = "queued" ];

/// resolving, fetching, inserting, finishing
Property Phase As %String(MAXLEN = 20);

Property RowsDone As %Integer [ InitialExpression = 0 ];

Property RowsTotal As %Integer [ InitialExpression = 0 ];

Property Dataset As %String(MAXLEN = 200);

/// File in the dataset, or "*" for all tabular files
Property FileName As %String(MAXLEN = 500);

Property Sheet As %String(MAXLEN = 200);

/// Target table, or the schema when FileName is "*"
Property TableName As %String(MAXLEN = 200);

Property Mode As %String(MAXLEN = 20);

/// Dataset version ("" = latest)
Property Version As %String(MAXLEN = 20);

Property RowLimit As %Integer [ InitialExpression = 0 ];

/// Files finished and files in total (1 for a single file)
Property FilesDone As %Integer [ InitialExpression = 0 ];

Property FilesTotal As %Integer [ InitialExpression = 1 ];

/// File being imported right now
Property CurrentFile As %String(MAXLEN = 500);

Property Username As %String(MAXLEN = 160);

Property CreatedAt As %TimeStamp [ InitialExpression = {$zdatetime($horolog,3)} ];

Property StartedAt As %TimeStamp;

Property FinishedAt As %TimeStamp;

Property CancelRequested As %Boolean [ InitialExpression = 0 ];

/// The same JSON that POST /load returns; for "*" {"tables":[...], "rows":...}
Property Result As %String(MAXLEN = "");

Property Error As %String(MAXLEN = 2000);

Property ImportLogId As %String(MAXLEN = 20);

Property Pid As %String(MAXLEN = 20);

Index StateIdx On State;

/// args: {dataset, file, table, limit, mode, version, sheet}, like POST /load
ClassMethod Start(args As %DynamicObject) As %String
{
    try {
        set check = {}.%FromJSON(..PrecheckPy({"dataset":(args.dataset), "file":(args.file), "table":(args.table), "mode":(args.mode)}.%ToJSON()))
        if check.code '= "" return check.%ToJSON()
        do ..Reap()
        &sql(SELECT COUNT(*) INTO :running FROM dc_kaggle2iris.Job WHERE State IN ('queued', 'running'))
        if running >= ..MaxJobs() {
            return {"error":($select(running=1:"One import is", 1:running_" imports are")_" already running. Try again when one has finished."), "code":"busy"}.%ToJSON()
        }
        set job = ..%New()
        set job.Dataset = args.dataset, job.FileName = $zstrip(args.file, "<>W"), job.TableName = check.table
        set job.Sheet = args.sheet, job.Mode = $select(args.mode="":"replace", 1:args.mode)
        set job.Version = args.version, job.RowLimit = +args.limit, job.Username = $username
        $$$ThrowOnError(job.%Save())
        set id = job.%Id()
        job ##class(dc.kaggle2iris.Job).Run(id)::10
        if '$test {
            set now = ..Now(), msg = "Could not start a background process"
            &sql(UPDATE dc_kaggle2iris.Job SET State = 'error', Error = :msg, FinishedAt = :now WHERE ID = :id)
        }
        return ..Get(id)
    } catch ex {
        return {"error":(##class(dc.kaggle2iris.Loader).ErrorText(ex)), "code":"upstream"}.%ToJSON()
    }
}

/// Runs in the background process started by Start()
ClassMethod Run(id As %String)
{
    lock +^dc.kaggle2iris.Job(id):10
    if '$test quit
    &sql(SELECT State, CancelRequested, Dataset, FileName, Sheet, TableName, RowLimit, Mode, Version
         INTO :state, :cancel, :dataset, :file, :sheet, :table, :limit, :mode, :version
         FROM dc_kaggle2iris.Job WHERE ID = :id)
    if (SQLCODE '= 0) || (state '= "queued") { lock -^dc.kaggle2iris.Job(id) quit }
    set now = ..Now(), pid = $job
    if cancel {
        set msg = "Import cancelled before it started"
        &sql(UPDATE dc_kaggle2iris.Job SET State = 'cancelled', Error = :msg, StartedAt = :now, FinishedAt = :now WHERE ID = :id)
        lock -^dc.kaggle2iris.Job(id)
        quit
    }
    &sql(UPDATE dc_kaggle2iris.Job SET State = 'running', Phase = 'resolving', StartedAt = :now, Pid = :pid WHERE ID = :id)
    set (logId, cancelled) = ""
    if file = "*" {
        do ..RunAll(id, dataset, table, limit, mode, version, .logId)
    } else {
        &sql(UPDATE dc_kaggle2iris.Job SET CurrentFile = :file WHERE ID = :id)
        try {
            set result = ##class(dc.kaggle2iris.Loader).Run(dataset, file, table, limit, mode, version, sheet, 0, id, .logId, .cancelled)
            set json = result.%ToJSON(), rows = result.rows
            &sql(UPDATE dc_kaggle2iris.Job SET State = 'ok', Result = :json, RowsDone = :rows, RowsTotal = :rows, FilesDone = 1 WHERE ID = :id)
        } catch ex {
            set state = $select(cancelled:"cancelled", 1:"error"), msg = $extract(##class(dc.kaggle2iris.Loader).ErrorText(ex), 1, 2000)
            &sql(UPDATE dc_kaggle2iris.Job SET State = :state, Error = :msg WHERE ID = :id)
        }
    }
    set now = ..Now()
    &sql(UPDATE dc_kaggle2iris.Job SET Phase = NULL, CurrentFile = NULL, ImportLogId = :logId, FinishedAt = :now WHERE ID = :id)
    lock -^dc.kaggle2iris.Job(id)
}

/// Every tabular file, one table each in schema. Stops at the first failure or cancel;
/// the tables imported before it stay and are listed in the result.
ClassMethod RunAll(id, dataset, schema, limit, mode, version, Output logId) [ Private ]
{
    set (logId, cancelled) = "", out = {"dataset":(dataset), "tables":[], "rows":0}
    try {
        set files = ##class(dc.kaggle2iris.Loader).Files(dataset, version, schema)
        set total = files.files.%Size(), out.version = files.version
        if total = 0 $$$ThrowStatus($$$ERROR($$$GeneralError, "The dataset has no files that can be imported (CSV, TSV, Parquet, JSON, JSONL, Excel)."))
        &sql(UPDATE dc_kaggle2iris.Job SET FilesTotal = :total WHERE ID = :id)
        set iter = files.files.%GetIterator(), done = 0
        while iter.%GetNext(, .f) {
            if ..IsCancelled(id) { set cancelled = 1  $$$ThrowStatus($$$ERROR($$$GeneralError, "Import cancelled")) }
            set name = f.file
            &sql(UPDATE dc_kaggle2iris.Job SET CurrentFile = :name, RowsDone = 0, RowsTotal = 0 WHERE ID = :id)
            try {
                set r = ##class(dc.kaggle2iris.Loader).Run(dataset, f.file, f.table, limit, mode, version, "", 0, id, .logId, .cancelled)
            } catch inner {
                $$$ThrowStatus($$$ERROR($$$GeneralError, f.file_": "_##class(dc.kaggle2iris.Loader).ErrorText(inner)))
            }
            do out.tables.%Push(r)
            set out.rows = out.rows + r.rows, done = done + 1
            &sql(UPDATE dc_kaggle2iris.Job SET FilesDone = :done WHERE ID = :id)
        }
        set json = out.%ToJSON(), rows = out.rows
        &sql(UPDATE dc_kaggle2iris.Job SET State = 'ok', Result = :json, RowsDone = :rows, RowsTotal = :rows WHERE ID = :id)
    } catch ex {
        set state = $select(cancelled:"cancelled", 1:"error"), msg = $extract(##class(dc.kaggle2iris.Loader).ErrorText(ex), 1, 2000)
        set json = $select(out.tables.%Size():out.%ToJSON(), 1:"")
        &sql(UPDATE dc_kaggle2iris.Job SET State = :state, Error = :msg, Result = :json WHERE ID = :id)
    }
}

/// Called by the import (Python) after each batch
ClassMethod Progress(id As %String, phase As %String, done As %Integer = 0, total As %Integer = 0)
{
    &sql(UPDATE dc_kaggle2iris.Job SET Phase = :phase, RowsDone = :done, RowsTotal = :total WHERE ID = :id)
}

ClassMethod IsCancelled(id As %String) As %Boolean
{
    &sql(SELECT CancelRequested INTO :cancel FROM dc_kaggle2iris.Job WHERE ID = :id)
    quit (SQLCODE = 0) && cancel
}

/// The import stops before its next batch of rows (or its next file); a download in progress is not interrupted
ClassMethod Cancel(id As %String) As %String
{
    do ..Reap()
    &sql(SELECT State INTO :state FROM dc_kaggle2iris.Job WHERE ID = :id)
    if SQLCODE '= 0 quit ..NotFound(id)
    if (state '= "queued") && (state '= "running") {
        quit {"error":("Job "_id_" has already finished ("_state_")."), "code":"conflict"}.%ToJSON()
    }
    &sql(UPDATE dc_kaggle2iris.Job SET CancelRequested = 1 WHERE ID = :id)
    quit ..Get(id)
}

ClassMethod Get(id As %String) As %String
{
    do ..Reap()
    set rs = ##class(%SQL.Statement).%ExecDirect(, ..#SELECT_" WHERE ID = ?", id)
    quit $select(rs.%Next():..Row(rs).%ToJSON(), 1:..NotFound(id))
}

ClassMethod List(limit As %Integer = 50) As %String
{
    do ..Reap()
    set limit = $select(+limit < 1:50, +limit > 200:200, 1:+limit), items = []
    set rs = ##class(%SQL.Statement).%ExecDirect(, $replace(..#SELECT, "SELECT ", "SELECT TOP "_limit_" ")_" ORDER BY ID DESC")
    while rs.%Next() { do items.%Push(..Row(rs)) }
    quit {"items":(items), "count":(items.%Size())}.%ToJSON()
}

/// Running or queued jobs whose process is gone (IRIS restarted, process killed)
/// are marked interrupted; their transaction was rolled back by IRIS.
ClassMethod Reap()
{
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT ID, State, DATEDIFF('s', CreatedAt, CURRENT_TIMESTAMP) AS age FROM dc_kaggle2iris.Job WHERE State IN ('queued', 'running')")
    while rs.%Next() {
        set id = rs.ID
        if (rs.State = "queued") && (rs.age < 60) continue      // still starting
        lock +^dc.kaggle2iris.Job(id):0
        if '$test continue                                      // its process holds the lock
        set now = ..Now(), msg = "The import process ended unexpectedly (for example, IRIS was restarted)."
        &sql(UPDATE dc_kaggle2iris.Job SET State = 'interrupted', Phase = NULL, Error = :msg, FinishedAt = :now
             WHERE ID = :id AND State IN ('queued', 'running'))
        lock -^dc.kaggle2iris.Job(id)
    }
}

Parameter SELECT = "SELECT ID, State, Phase, RowsDone, RowsTotal, Dataset, FileName, Sheet, TableName, Mode, Version, RowLimit, FilesDone, FilesTotal, CurrentFile, Username, CreatedAt, StartedAt, FinishedAt, CancelRequested, Result, Error, ImportLogId, DATEDIFF('s', StartedAt, COALESCE(FinishedAt, CURRENT_TIMESTAMP)) AS Elapsed FROM dc_kaggle2iris.Job";

ClassMethod Row(rs) As %DynamicObject [ Private ]
{
    set o = {"id":(rs.ID), "state":(rs.State), "phase":(rs.Phase), "rowsDone":(+rs.RowsDone), "rowsTotal":(+rs.RowsTotal),
        "dataset":(rs.Dataset), "file":(rs.FileName), "sheet":(rs.Sheet), "table":(rs.TableName), "mode":(rs.Mode),
        "version":(rs.Version), "limit":(+rs.RowLimit), "filesDone":(+rs.FilesDone), "filesTotal":(+rs.FilesTotal),
        "currentFile":(rs.CurrentFile), "user":(rs.Username), "createdAt":(rs.CreatedAt),
        "startedAt":(rs.StartedAt), "finishedAt":(rs.FinishedAt), "error":(rs.Error), "importLogId":(rs.ImportLogId)}
    do o.%Set("cancelRequested", +rs.CancelRequested, "boolean")
    do o.%Set("elapsed", $select(rs.StartedAt="":"", 1:+rs.Elapsed), $select(rs.StartedAt="":"null", 1:"number"))
    if rs.Result '= "" { do o.%Set("result", {}.%FromJSON(rs.Result)) } else { do o.%Set("result", "", "null") }
    for key = "phase", "sheet", "version", "currentFile", "startedAt", "finishedAt", "error", "importLogId" {
        do:o.%Get(key)="" o.%Set(key, "", "null")
    }
    quit o
}

ClassMethod NotFound(id As %String) As %String [ Private ]
{
    quit {"error":("Job "_id_" not found."), "code":"not_found"}.%ToJSON()
}

ClassMethod MaxJobs() As %Integer [ Private ]
{
    set n = +$system.Util.GetEnviron("KAGGLE2IRIS_MAX_JOBS")
    quit $select(n > 0:n, 1:2)
}

ClassMethod Now() As %String [ Private ]
{
    quit $zdatetime($horolog, 3)
}

ClassMethod PrecheckPy(args As %String) As %String [ Internal, Language = python ]
{
from kaggle2iris import plan
return plan.call_precheck(args)
}

}
