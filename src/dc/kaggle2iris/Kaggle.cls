/// Read-only discovery of Kaggle datasets (search, details, file preview) for the web UI.
/// Every method returns JSON; errors come back as {"error":...,"code":...}.
/// The work is done in Python (kaggle2iris.kaggle); Kaggle credentials never leave the server.
Class dc.kaggle2iris.Kaggle
{

ClassMethod Search(search As %String = "", sort As %String = "", fileType As %String = "", license As %String = "", tag As %String = "", minSize As %String = "", maxSize As %String = "", page As %String = "", refresh As %String = "") As %String
{
    quit ..Call("search", {"search":(search), "sort":(sort), "file_type":(fileType), "license":(license), "tag":(tag),
        "min_size":(minSize), "max_size":(maxSize), "page":(page), "refresh":(refresh)})
}

ClassMethod Details(dataset As %String = "", version As %String = "", refresh As %String = "") As %String
{
    quit ..Call("details", {"dataset":(dataset), "version":(version), "refresh":(refresh)})
}

ClassMethod Preview(dataset As %String = "", file As %String = "", offset As %String = "", rows As %String = "", version As %String = "", sheet As %String = "", refresh As %String = "") As %String
{
    quit ..Call("preview", {"dataset":(dataset), "file":(file), "offset":(offset), "rows":(rows), "version":(version), "sheet":(sheet), "refresh":(refresh)})
}

/// Package and IRIS version, limits, and whether Kaggle credentials are set (never their value)
ClassMethod Status() As %String
{
    set r = {}.%FromJSON(..Call("status", {}))
    set:r.error="" r.iris = $system.Version.GetNumber()_" (Build "_$system.Version.GetBuildNumber()_")"
    quit r.%ToJSON()
}

ClassMethod Call(name As %String, args As %DynamicObject) As %String [ Internal ]
{
    quit ..CallPy(name, args.%ToJSON())
}

ClassMethod CallPy(name As %String, args As %String) As %String [ Internal, Language = python ]
{
from kaggle2iris import kaggle
return kaggle.call(name, args)
}

}
