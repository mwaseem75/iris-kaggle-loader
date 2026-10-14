/// REST API behind the web UI.  Mounted at /api/kaggle2iris
Class dc.kaggle2iris.REST Extends %CSP.REST
{

Parameter CONTENTTYPE = "application/json";

Parameter CHARSET = "utf-8";

XData UrlMap [ XMLNamespace = "http://www.intersystems.com/urlmap" ]
{
<Routes>
  <Route Url="/load" Method="POST" Call="Load"/>
  <Route Url="/history" Method="GET" Call="History"/>
  <Route Url="/preview/:table" Method="GET" Call="Preview"/>
  <Route Url="/datasets" Method="GET" Call="SearchDatasets"/>
  <Route Url="/dataset" Method="GET" Call="DatasetDetails"/>
  <Route Url="/dataset/preview" Method="GET" Call="DatasetPreview"/>
  <Route Url="/files" Method="GET" Call="ImportableFiles"/>
  <Route Url="/status" Method="GET" Call="Status"/>
  <Route Url="/plan" Method="POST" Call="PlanImport"/>
  <Route Url="/jobs" Method="POST" Call="StartJob"/>
  <Route Url="/jobs" Method="GET" Call="ListJobs"/>
  <Route Url="/jobs/:id" Method="GET" Call="GetJob"/>
  <Route Url="/jobs/:id/cancel" Method="POST" Call="CancelJob"/>
  <Route Url="/tables" Method="GET" Call="ListTables"/>
  <Route Url="/tables/:table" Method="GET" Call="TableDetail"/>
</Routes>
}

ClassMethod Load() As %Status
{
    set b = ..Body()
    if '$isobject(b) quit ..JsonError(400, "The request body must be a JSON object", "bad_request")
    if b.dataset = "" quit ..JsonError(400, "dataset is required")
    if b.file = "" quit ..JsonError(400, "file is required")
    try {
        set r = ##class(dc.kaggle2iris.Loader).LoadQuiet(b.dataset, b.file, b.table, +b.limit, b.mode, b.version, b.sheet)
        write r.%ToJSON()
    } catch ex { return ..JsonError(400, ##class(dc.kaggle2iris.Loader).ErrorText(ex)) }
    quit $$$OK
}

ClassMethod History() As %Status
{
    set out = []
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT TOP 50 ID, Dataset, FileName, Sheet, TableName, RowCount, Seconds, Status, Message, CreatedAt, Version, Encoding "_
        "FROM dc_kaggle2iris.ImportLog ORDER BY ID DESC")
    while rs.%Next() {
        do out.%Push({"id":(rs.ID),"dataset":(rs.Dataset),"file":(rs.FileName),"sheet":(rs.Sheet),
            "table":(rs.TableName),"rows":(rs.RowCount),"seconds":(rs.Seconds),
            "status":(rs.Status),"message":(rs.Message),"createdAt":(rs.CreatedAt),
            "version":(rs.Version),"encoding":(rs.Encoding)})
    }
    write out.%ToJSON()
    quit $$$OK
}

ClassMethod Preview(table As %String) As %Status
{
    if '##class(dc.kaggle2iris.ImportLog).IsKnownTable(table) quit ..JsonError(404, "unknown table")
    set n = $get(%request.Data("rows", 1), 20), n = $select(n > 200:200, n < 1:20, 1:+n)
    // optional paging; without offset the query and the response are as before
    set offset = $get(%request.Data("offset", 1))
    set st = ##class(%SQL.Statement).%New(1)        // ODBC mode: dates and timestamps as text, not internal numbers
    if offset '= "" {
        set offset = $select(offset?1.9N:+offset, 1:0)
        set rs = ##class(%SQL.Statement).%ExecDirect(.st,
            "SELECT * FROM (SELECT TOP ALL * FROM "_table_" ORDER BY %ID) WHERE %VID BETWEEN ? AND ?", offset + 1, offset + n)
    } else {
        set rs = ##class(%SQL.Statement).%ExecDirect(.st, "SELECT TOP "_n_" * FROM "_table)
    }
    if rs.%SQLCODE < 0 quit ..JsonError(500, rs.%Message)
    set meta = rs.%GetMetadata(), cols = []
    set lobs = ""
    for i = 1:1:meta.columnCount {
        do cols.%Push(meta.columns.GetAt(i).colName)
        // clientType 12/13 = binary/character stream: %GetData returns an OID, not the text
        set:$listfind($listbuild(12, 13), meta.columns.GetAt(i).clientType) lobs = lobs_$listbuild(i)
    }
    set rows = []
    while rs.%Next() {
        set row = []
        for i = 1:1:meta.columnCount {
            set v = rs.%GetData(i)
            if $listfind(lobs, i), v '= "" { set s = ##class(%Stream.Object).%Open(v), v = $select($isobject(s):s.Read(300), 1:"") }
            do row.%Push($extract(v, 1, 300))
        }
        do rows.%Push(row)
    }
    set cnt = ##class(%SQL.Statement).%ExecDirect(, "SELECT COUNT(*) AS c FROM "_table)
    do cnt.%Next()
    set out = {"table":(table),"total":(cnt.c),"columns":(cols),"rows":(rows)}
    set:offset'="" out.offset = offset
    write out.%ToJSON()
    quit $$$OK
}

/// Kaggle search: ?search=&sort=&fileType=&license=&tag=&minSize=&maxSize=&page=&refresh=
ClassMethod SearchDatasets() As %Status
{
    quit ..Reply(##class(dc.kaggle2iris.Kaggle).Search(..Param("search"), ..Param("sort"), ..Param("fileType"),
        ..Param("license"), ..Param("tag"), ..Param("minSize"), ..Param("maxSize"), ..Param("page"), ..Param("refresh")))
}

/// Dataset details: ?id=owner/slug&version=&refresh=
ClassMethod DatasetDetails() As %Status
{
    quit ..Reply(##class(dc.kaggle2iris.Kaggle).Details(..Param("id"), ..Param("version"), ..Param("refresh")))
}

/// Rows of a file on Kaggle: ?id=&file=&offset=&rows=&version=&sheet=&refresh=
ClassMethod DatasetPreview() As %Status
{
    quit ..Reply(##class(dc.kaggle2iris.Kaggle).Preview(..Param("id"), ..Param("file"), ..Param("offset"),
        ..Param("rows"), ..Param("version"), ..Param("sheet"), ..Param("refresh")))
}

/// The files an "all files" import takes, with their tables: ?id=&version=&schema=
ClassMethod ImportableFiles() As %Status
{
    quit ..Reply(##class(dc.kaggle2iris.Loader).FilesPy({"dataset":(..Param("id")), "version":(..Param("version")), "schema":(..Param("schema"))}.%ToJSON()))
}

ClassMethod Status() As %Status
{
    quit ..Reply(##class(dc.kaggle2iris.Kaggle).Status())
}

/// Import plan (dry run), body like POST /load
ClassMethod PlanImport() As %Status
{
    set body = ..Body()
    if '$isobject(body) quit ..JsonError(400, "The request body must be a JSON object", "bad_request")
    quit ..Reply(##class(dc.kaggle2iris.Loader).Plan(body))
}

/// Background import, body like POST /load; answers 202 with the job
ClassMethod StartJob() As %Status
{
    set body = ..Body()
    if '$isobject(body) quit ..JsonError(400, "The request body must be a JSON object", "bad_request")
    quit ..Reply(##class(dc.kaggle2iris.Job).Start(body), "202 Accepted")
}

ClassMethod ListJobs() As %Status
{
    quit ..Reply(##class(dc.kaggle2iris.Job).List(..Param("limit")))
}

ClassMethod GetJob(id As %String) As %Status
{
    if id '? 1.9N quit ..JsonError(404, "Job "_id_" not found.", "not_found")
    quit ..Reply(##class(dc.kaggle2iris.Job).Get(id))
}

ClassMethod CancelJob(id As %String) As %Status
{
    if id '? 1.9N quit ..JsonError(404, "Job "_id_" not found.", "not_found")
    quit ..Reply(##class(dc.kaggle2iris.Job).Cancel(id))
}

ClassMethod ListTables() As %Status
{
    quit ..Reply(##class(dc.kaggle2iris.Tables).List())
}

ClassMethod TableDetail(table As %String) As %Status
{
    quit ..Reply(##class(dc.kaggle2iris.Tables).Detail(table))
}

ClassMethod Body() As %DynamicObject [ Private ]
{
    try { set body = {}.%FromJSON(%request.Content) } catch { set body = "" }
    quit $select($isobject(body) && body.%IsA("%DynamicObject"):body, 1:"")
}

ClassMethod Param(name As %String) As %String [ Private ]
{
    quit $get(%request.Data(name, 1))
}

/// Writes a Kaggle or plan result, or maps its error code to an HTTP status
ClassMethod Reply(json As %String, okStatus As %String = "") As %Status [ Private ]
{
    set r = {}.%FromJSON(json)
    if r.code '= "" {
        quit ..JsonError($case(r.code, "bad_request":400, "not_found":404, "credentials":403,
            "preview_unavailable":409, "conflict":409, "rate_limited":429, "busy":429,
            "too_large":413, "timeout":504, :502), r.error, r.code)
    }
    set:okStatus'="" %response.Status = okStatus
    write json
    quit $$$OK
}

ClassMethod JsonError(code As %Integer, msg As %String, errorCode As %String = "") As %Status
{
    set %response.Status = code_" "_$case(code, 400:"Bad Request", 403:"Forbidden", 404:"Not Found",
        409:"Conflict", 413:"Payload Too Large", 429:"Too Many Requests", 502:"Bad Gateway", 504:"Gateway Timeout", :"Error")
    set body = {"error":(msg)}
    set:errorCode'="" body.code = errorCode
    write body.%ToJSON()
    quit $$$OK
}

}
