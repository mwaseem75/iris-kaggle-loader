#!/usr/bin/env bash
# Live checks against Kaggle (needs internet, no Kaggle account).
#   tests/smoke.sh discovery   read-only: search, details, previews and import plans over REST
#   tests/smoke.sh import      loads real files into schema KGSmoke (also as background
#                              jobs) and drops them again
#   tests/smoke.sh             both
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
if docker compose version >/dev/null 2>&1; then dc=(docker compose); else dc=(docker-compose); fi
mode=${1:-all}
B="http://localhost:${IRIS_WEB_PORT:-52773}/api/kaggle2iris"
AUTH="${IRIS_USERNAME:-_SYSTEM}:${IRIS_PASSWORD:-SYS}"
fails=0
ECOM="mkechinov/ecommerce-behavior-data-from-multi-category-store"   # 2019-Nov.csv: 8.4 GB, sent zipped
OLIST="olistbr/brazilian-ecommerce"                                   # 9 CSV files

# check NAME STATUS PATTERN [ABSENT] -- curl args; PATTERN must match the body, ABSENT must not
check() {
  local name=$1 want=$2 pattern=$3 absent=$4 out status
  shift 5
  out=$(curl -s -m 120 -w '\n%{http_code}' "$@" || true)
  status=${out##*$'\n'}
  out=${out%$'\n'*}
  if [ "$status" = "$want" ] && { [ -z "$pattern" ] || grep -qE -- "$pattern" <<<"$out"; } \
     && { [ -z "$absent" ] || ! grep -qE -- "$absent" <<<"$out"; }; then
    echo "PASS  $name (HTTP $status)"
  else
    echo "FAIL  $name (HTTP $status, expected $want) ${out:0:200}"
    fails=$((fails + 1))
  fi
}

discovery() {
  for path in "datasets?search=iris" "dataset?id=a/b" "dataset/preview?id=a/b&file=c.csv" "files?id=a/b" "status" \
              "jobs" "jobs/1" "tables" "tables/KG.Iris" "history"; do
    check "no login: GET /$path" 401 "" "" -- "$B/$path"
  done
  for path in "plan" "jobs" "jobs/1/cancel" "load"; do
    check "no login: POST /$path" 401 "" "" -- -X POST -H "Content-Type: application/json" -d '{}' "$B/$path"
  done
  check "status" 200 '"credentials": ?(true|false)' 'KGAT_[A-Za-z0-9]{8}' -- -u "$AUTH" "$B/status"
  check "search iris" 200 '"ref": "uciml/iris"' "" -- -u "$AUTH" "$B/datasets?search=iris&sort=votes"
  check "search again is cached" 200 '"cached": ?true' "" -- -u "$AUTH" "$B/datasets?search=iris&sort=votes"
  check "search with filters" 200 '"items": \[\{' "" -- -u "$AUTH" "$B/datasets?fileType=parquet&license=cc&maxSize=5000000&page=2"
  check "search, bad sort" 400 '"code":"bad_request"' "" -- -u "$AUTH" "$B/datasets?sort=random"
  check "details uciml/iris" 200 '"currentVersion": 2.*"name": "Iris.csv"' "" -- -u "$AUTH" "$B/dataset?id=uciml/iris"
  check "details, pinned version" 200 '"version": 1, "pinned": true' "" -- -u "$AUTH" "$B/dataset?id=uciml/iris&version=1"
  check "details, unknown version" 404 'Version 9 not found' "" -- -u "$AUTH" "$B/dataset?id=uciml/iris&version=9"
  check "details, missing dataset" 404 'KAGGLE_API_TOKEN.*"code":"not_found"' "" -- -u "$AUTH" "$B/dataset?id=no-such-user/no-such-dataset"
  check "details, bad id" 400 '"code":"bad_request"' "" -- -u "$AUTH" "$B/dataset?id=a%20b"
  check "files for all-files import" 200 '"table": "KG.olist_orders_dataset"' "" -- -u "$AUTH" "$B/files?id=$OLIST"
  check "preview rows 100-104" 200 '"offset": 100.*"total": 150' "" -- -u "$AUTH" "$B/dataset/preview?id=uciml/iris&file=Iris.csv&offset=100&rows=5"
  check "preview, unknown file" 404 'Files you can import: Iris.csv' "" -- -u "$AUTH" "$B/dataset/preview?id=uciml/iris&file=nope.csv"
  check "preview, SQLite file" 409 '"code":"preview_unavailable"' "" -- -u "$AUTH" "$B/dataset/preview?id=uciml/iris&file=database.sqlite"
  check "preview, older version" 200 '"version": 1.*"total": 150' "" -- -u "$AUTH" "$B/dataset/preview?id=uciml/iris&file=Iris.csv&version=1&rows=2"
  check "preview, 8 GB zipped CSV" 200 '"columns": \["event_time".*"partial": true' "" -- -u "$AUTH" "$B/dataset/preview?id=$ECOM&file=2019-Nov.csv&rows=3"
  check "preview, Parquet" 200 '"columns": \["Season", "DateTime".*"type": "timestamp"' "" -- -u "$AUTH" \
    "$B/dataset/preview?id=irkaal/english-premier-league-results&file=results.parquet&rows=3"
  check "preview, JSON" 200 '"columns": \["id", "ingredients"\].*"total": 9944' "" -- -u "$AUTH" \
    "$B/dataset/preview?id=kaggle/recipe-ingredients-dataset&file=test.json&rows=3"
  check "preview, Excel" 200 '"columns": \["Rank", "Team/NOC".*"sheets": \["Details"\]' "" -- -u "$AUTH" \
    "$B/dataset/preview?id=arjunprasadsarkhel/2021-olympics-in-tokyo&file=Medals.xlsx&rows=3"
  check "preview, file in a folder" 200 '"columns": \["Area"' "" -- -u "$AUTH" \
    "$B/dataset/preview?id=muratkokludataset/raisin-dataset&file=Raisin_Dataset%2FRaisin_Dataset.xlsx&rows=2"
  local json=(-u "$AUTH" -X POST -H "Content-Type: application/json")
  check "plan iris" 200 '"action": "(create|replace)".*"name": "SepalLengthCm"' "" -- "${json[@]}" \
    -d '{"dataset":"uciml/iris","file":"Iris.csv","table":"KGSmoke.plan_iris"}' "$B/plan"
  check "plan, foreign table refused" 200 '"action": "refused".*not created by kaggle2iris' "" -- "${json[@]}" \
    -d '{"dataset":"uciml/iris","file":"Iris.csv","table":"dc_kaggle2iris.ImportLog","mode":"append"}' "$B/plan"
  check "plan, full load over the limits refused" 200 '"action": "refused".*MB limit' "" -- "${json[@]}" \
    -d "{\"dataset\":\"$ECOM\",\"file\":\"2019-Nov.csv\",\"table\":\"KGSmoke.big\"}" "$B/plan"
  check "plan, the same file with a row limit" 200 '"action": "(create|replace)".*first 200 rows' "" -- "${json[@]}" \
    -d "{\"dataset\":\"$ECOM\",\"file\":\"2019-Nov.csv\",\"table\":\"KGSmoke.big\",\"limit\":1000}" "$B/plan"
  check "plan, SQLite file" 400 'not a file type that can be imported' "" -- "${json[@]}" \
    -d '{"dataset":"uciml/iris","file":"database.sqlite"}' "$B/plan"
  check "plan, bad body" 400 '"code":"bad_request"' "" -- "${json[@]}" -d 'not json' "$B/plan"
  check "jobs list" 200 '"items":' "" -- -u "$AUTH" "$B/jobs?limit=5"
  check "job, unknown id" 404 '"code":"not_found"' "" -- -u "$AUTH" "$B/jobs/999999999"
  check "job start, foreign table" 409 '"code":"conflict"' "" -- "${json[@]}" \
    -d '{"dataset":"uciml/iris","file":"Iris.csv","table":"dc_kaggle2iris.ImportLog","mode":"append"}' "$B/jobs"
  check "job start, bad id" 400 '"code":"bad_request"' "" -- "${json[@]}" -d '{"dataset":"a b","file":"x.csv"}' "$B/jobs"
  check "job start, all files into Schema.Table" 400 'schema name' "" -- "${json[@]}" \
    -d '{"dataset":"uciml/iris","file":"*","table":"Demo.T"}' "$B/jobs"
  check "job cancel, unknown id" 404 '"code":"not_found"' "" -- "${json[@]}" -d '{}' "$B/jobs/999999999/cancel"
  check "tables list" 200 '"items":' "" -- -u "$AUTH" "$B/tables"
  check "table, not created by kaggle2iris" 404 '"code":"not_found"' "" -- -u "$AUTH" "$B/tables/dc_kaggle2iris.ImportLog"
  local first
  first=$(curl -s -u "$AUTH" "$B/tables" | grep -oE '"table":"[A-Za-z0-9_]+\.[A-Za-z0-9_]+"' | head -1 | cut -d'"' -f4 || true)
  if [ -n "$first" ]; then
    check "table detail ($first)" 200 '"columns":\[\{"name":' "" -- -u "$AUTH" "$B/tables/$first"
    check "table rows with offset" 200 '"offset":1' "" -- -u "$AUTH" "$B/preview/$first?rows=2&offset=1"
    check "table rows without offset" 200 '"rows":' '"offset"' -- -u "$AUTH" "$B/preview/$first?rows=2"
  else
    echo "SKIP  table detail and paging (no tables yet)"
  fi
  echo "DISCOVERY $([ "$fails" -eq 0 ] && echo PASSED || echo "FAILED: $fails check(s)")"
}

imports() {
out=$("${dc[@]}" exec -T iris iris session IRIS -U USER <<'OS'
 set ecom = "mkechinov/ecommerce-behavior-data-from-multi-category-store"
 // name, dataset, file, table, limit, mode, version, sheet, expected status, expected rows or error text
 set c($i(c)) = $lb("CSV, full","uciml/iris","Iris.csv","KGSmoke.iris",0,"","","","OK",150)
 set c($i(c)) = $lb("CSV, older version","uciml/iris","Iris.csv","KGSmoke.iris_v1",0,"","1","","OK",150)
 set c($i(c)) = $lb("CSV, append","uciml/iris","Iris.csv","KGSmoke.iris",10,"append","","","OK",10)
 set c($i(c)) = $lb("zipped 8 GB CSV, first rows",ecom,"2019-Nov.csv","KGSmoke.ecom",2000,"","","","OK",2000)
 set c($i(c)) = $lb("Parquet","irkaal/english-premier-league-results","results.parquet","KGSmoke.epl",500,"","","","OK",500)
 set c($i(c)) = $lb("JSON","kaggle/recipe-ingredients-dataset","test.json","KGSmoke.recipes",300,"","","","OK",300)
 set c($i(c)) = $lb("Excel","arjunprasadsarkhel/2021-olympics-in-tokyo","Medals.xlsx","KGSmoke.medals",0,"","","","OK",93)
 set c($i(c)) = $lb("Excel, named sheet, folder","muratkokludataset/raisin-dataset","Raisin_Dataset/Raisin_Dataset.xlsx","KGSmoke.raisin",0,"","","Raisin_Dataset","OK",900)
 set c($i(c)) = $lb("missing file","uciml/iris","nope.csv","KGSmoke.x",5,"","","","ERROR","not found in uciml/iris")
 set c($i(c)) = $lb("SQLite file","uciml/iris","database.sqlite","KGSmoke.x",5,"","","","ERROR","not a file type that can be imported")
 set c($i(c)) = $lb("missing dataset","no-such-user/no-such-dataset","x.csv","KGSmoke.x",5,"","","","ERROR","Dataset not found, or private")
 set c($i(c)) = $lb("unknown version","uciml/iris","Iris.csv","KGSmoke.x",5,"","9","","ERROR","Version 9 not found")
 set c($i(c)) = $lb("unknown sheet","arjunprasadsarkhel/2021-olympics-in-tokyo","Medals.xlsx","KGSmoke.x",5,"","","Nope","ERROR","Sheet 'Nope' not found")
 set c($i(c)) = $lb("too big",ecom,"2019-Nov.csv","KGSmoke.x",0,"","","","ERROR","MB limit")
 set c($i(c)) = $lb("foreign table","uciml/iris","Iris.csv","dc_kaggle2iris.ImportLog",5,"append","","","ERROR","not created by kaggle2iris")
 set c($i(c)) = $lb("bad mode","uciml/iris","Iris.csv","KGSmoke.iris",5,"overwrite","","","ERROR","mode must be one of")
 set q = "SELECT dc_kaggle2iris.load_dataset(?,?,?,?,?,?,?) AS r", fails = 0
 for i=1:1:c { set a = c(i), t = $zh, rs = ##class(%SQL.Statement).%ExecDirect(, q, $li(a,2), $li(a,3), $li(a,4), $li(a,5), $li(a,6), $li(a,7), $li(a,8)) do rs.%Next() set r = {}.%FromJSON(rs.r), ok = (r.status = $li(a,9)) && $s(r.status = "OK": r.rows = $li(a,10), 1: r.error [ $li(a,10)), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  ", $li(a), " (", $j($zh - t, 0, 1), "s)  ", $s(r.status = "OK": r.rows_" rows into "_r.table_", version "_r.version, 1: r.error) }
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT COUNT(*) AS n FROM KGSmoke.iris") do rs.%Next() set ok = (rs.n = 160), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  iris table has 150 + 10 appended rows: ", rs.n
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT TOP 1 Version AS v FROM dc_kaggle2iris.ImportLog WHERE TableName = 'KGSmoke.iris_v1' ORDER BY ID DESC") do rs.%Next() set ok = (rs.v = 1), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  version 1 logged: ", rs.v
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT SUM(Gold) AS g FROM KGSmoke.medals") do rs.%Next() set ok = (rs.g = 340), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  Excel numbers saved as text became numbers (gold medals: ", rs.g, ")"
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT COUNT(*) AS n FROM KGSmoke.epl WHERE DateTime IS NOT NULL AND FTHG >= 0") do rs.%Next() set ok = (rs.n = 500), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  Parquet timestamps and integers: ", rs.n
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT TABLE_NAME AS t FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'KGSmoke'") while rs.%Next() { set x = ##class(%SQL.Statement).%ExecDirect(, "DROP TABLE KGSmoke."_rs.t) kill ^dc.kaggle2iris.Owned($zcvt("KGSMOKE."_rs.t, "U")) }
 write !, "SMOKE ", $s(fails:"FAILED: "_fails_" check(s)", 1:"PASSED"), !
 if fails do $system.Process.Terminate(, 1)
 halt
OS
) || true
grep -aE "^(PASS|FAIL|SMOKE)" <<<"$out" || echo "$out"
grep -aq "^SMOKE PASSED" <<<"$out" || fails=$((fails + 1))
}

# background imports: one file, all files of a dataset, and a cancelled one
wait_job() {
  local id=$1 state i
  for i in $(seq 1 90); do
    state=$(curl -s -u "$AUTH" "$B/jobs/$id" | grep -oE '"state":"[a-z]+"' | cut -d'"' -f4 || true)
    [[ "$state" =~ ^(ok|error|cancelled|interrupted)$ ]] && return 0
    sleep 2
  done
}

jobs() {
  local json=(-u "$AUTH" -H "Content-Type: application/json") id body
  body=$(curl -s "${json[@]}" -X POST "$B/jobs" \
    -d "{\"dataset\":\"$OLIST\",\"file\":\"olist_orders_dataset.csv\",\"limit\":300,\"table\":\"KGSmoke.job_orders\"}")
  id=$(grep -oE '"id":"?[0-9]+' <<<"$body" | grep -oE '[0-9]+' || true)
  wait_job "$id"
  check "background job finishes" 200 '"state":"ok".*"rowsDone":300' "" -- -u "$AUTH" "$B/jobs/$id"
  check "table rows show timestamps as text" 200 '"delivered","20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"' "" -- -u "$AUTH" "$B/preview/KGSmoke.job_orders?rows=1"
  body=$(curl -s "${json[@]}" -X POST "$B/jobs" -d "{\"dataset\":\"$OLIST\",\"file\":\"*\",\"limit\":50,\"table\":\"KGSmoke\"}")
  id=$(grep -oE '"id":"?[0-9]+' <<<"$body" | grep -oE '[0-9]+' || true)
  wait_job "$id"
  check "all-files job imports every CSV" 200 '"state":"ok".*"filesDone":9,"filesTotal":9.*"table":"KGSmoke.olist_sellers_dataset"' "" \
    -- -u "$AUTH" "$B/jobs/$id"
  body=$(curl -s "${json[@]}" -X POST "$B/jobs" \
    -d "{\"dataset\":\"$ECOM\",\"file\":\"2019-Nov.csv\",\"limit\":300000,\"table\":\"KGSmoke.job_cancel\"}")
  id=$(grep -oE '"id":"?[0-9]+' <<<"$body" | grep -oE '[0-9]+' || true)
  curl -s "${json[@]}" -X POST -d '{}' "$B/jobs/$id/cancel" >/dev/null
  wait_job "$id"
  check "cancelled job" 200 '"state":"cancelled"' "" -- -u "$AUTH" "$B/jobs/$id"
  "${dc[@]}" exec -T iris iris session IRIS -U USER >/dev/null <<'OS'
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT TABLE_NAME AS t FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'KGSmoke'") while rs.%Next() { set x = ##class(%SQL.Statement).%ExecDirect(, "DROP TABLE KGSmoke."_rs.t) kill ^dc.kaggle2iris.Owned($zcvt("KGSMOKE."_rs.t, "U")) }
 halt
OS
}

case "$mode" in
  discovery) discovery ;;
  import) imports; jobs ;;
  all) discovery; imports; jobs ;;
  *) echo "usage: tests/smoke.sh [discovery|import]"; exit 2 ;;
esac
[ "$fails" -eq 0 ]
