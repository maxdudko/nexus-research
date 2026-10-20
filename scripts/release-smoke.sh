#!/usr/bin/env bash
# Packaged smoke test for compose.release.yml. No paid provider call is made.
#
#   scripts/release-smoke.sh              # build images, then test
#   scripts/release-smoke.sh --no-build   # reuse nexus-api / nexus-web images
#
# Runs an isolated Compose project with throwaway volumes, a random loopback port,
# a random database password and fake provider keys. compose.smoke.yml points the
# OpenAI SDK at a stub that never answers, so streams stay open without spending
# credit. On failure, scrubbed logs are written to $SMOKE_ARTIFACTS
# (default /tmp/nexus-smoke).
#
# A real research demo is manual: run compose.release.yml with real keys in
# .env.release and follow docs/release/demo.md.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BUILD=1
case "${1:-}" in
  "") ;;
  --no-build) BUILD=0 ;;
  *) echo "usage: $0 [--no-build]" >&2; exit 2 ;;
esac

PROJECT="nexus-smoke-$$"
ARTIFACTS="${SMOKE_ARTIFACTS:-/tmp/nexus-smoke}"
TMP="$(mktemp -d)"
PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
OPENAI_SENTINEL="sk-smoke-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
TAVILY_SENTINEL="tvly-smoke-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
ENV_FILE="$TMP/smoke.env"
BASE="http://127.0.0.1:$PORT"
QUESTION='{"question":"What does the packaged smoke test verify?"}'

sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$PASSWORD/" \
    -e "s/^NEXUS_OPENAI_API_KEY=.*/NEXUS_OPENAI_API_KEY=$OPENAI_SENTINEL/" \
    -e "s/^NEXUS_TAVILY_API_KEY=.*/NEXUS_TAVILY_API_KEY=$TAVILY_SENTINEL/" \
    -e "s/^NEXUS_WEB_PORT=.*/NEXUS_WEB_PORT=$PORT/" \
    .env.release.example > "$ENV_FILE"

RELEASE=(docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f compose.release.yml)
C=("${RELEASE[@]}" -f compose.smoke.yml)

phase() { printf '\n== %s\n' "$*"; }
ok() { printf 'ok   %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

scrub() {
  sed -e "s/$PASSWORD/[password]/g" \
      -e "s/$OPENAI_SENTINEL/[openai-key]/g" \
      -e "s/$TAVILY_SENTINEL/[tavily-key]/g"
}

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    mkdir -p "$ARTIFACTS"
    "${C[@]}" ps -a > "$TMP/ps.txt" 2>&1 || true
    "${C[@]}" logs --no-color --timestamps > "$TMP/compose.log" 2>&1 || true
    for file in "$TMP"/*.txt "$TMP"/*.log; do
      [[ -f $file ]] && scrub < "$file" > "$ARTIFACTS/$(basename "$file")"
    done
    printf '\nsmoke failed; scrubbed logs in %s\n' "$ARTIFACTS" >&2
  fi
  "${C[@]}" down -v --remove-orphans --timeout 5 > /dev/null 2>&1 || true
  rm -rf "$TMP"
  exit "$status"
}
trap cleanup EXIT

# --- helpers --------------------------------------------------------------------

# Print "STATUS BODY" for a URL through the Web container's published port.
web() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-s -m 10 -o "$TMP/body" -w '%{http_code}' -X "$method")
  [[ -n $body ]] && args+=(-H 'content-type: application/json' -d "$body")
  local code
  code="$(curl "${args[@]}" "$BASE$path" || true)"
  printf '%s %s' "$code" "$(cat "$TMP/body" 2>/dev/null)"
}

# Print "STATUS BODY" for a URL fetched from inside the API container.
inside_api() {
  "${C[@]}" exec -T api python - "$1" <<'PY'
import sys, urllib.error, urllib.request
try:
    response = urllib.request.urlopen(sys.argv[1], timeout=5)
    print(response.status, response.read().decode())
except urllib.error.HTTPError as exc:
    print(exc.code, exc.read().decode())
PY
}

json_field() { python3 -c 'import json, sys; value = json.load(sys.stdin)
for key in sys.argv[1].split("."): value = value[key]
print(value)' "$1"; }

stub_posts() {
  "${C[@]}" exec -T stub-openai python -c \
    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/count').read().decode())" \
    | json_field posts
}

psql_value() { "${C[@]}" exec -T postgres psql -U nexus -d nexus -Atc "$1"; }

container() { "${C[@]}" ps -a -q "$1"; }

health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$(container "$1")"; }

wait_healthy() {
  local service="$1" timeout="${2:-120}" waited=0
  until [[ "$(health "$service")" == healthy ]]; do
    (( waited >= timeout )) && fail "$service not healthy after ${timeout}s (state: $(health "$service"))"
    sleep 2; waited=$((waited + 2))
  done
}

wait_ready() {
  local waited=0
  until [[ "$(web GET /api/v1/health/ready)" == 200* ]]; do
    (( waited >= 60 )) && fail "readiness did not recover: $(web GET /api/v1/health/ready)"
    sleep 2; waited=$((waited + 2))
  done
}

run_status() { web GET "/api/v1/research/runs/$1" | cut -d' ' -f2- | json_field run.status; }

wait_status() {
  local run_id="$1" wanted="$2" timeout="$3" waited=0 status=""
  while (( waited < timeout )); do
    status="$(run_status "$run_id" 2>/dev/null || true)"
    [[ $status == "$wanted" ]] && { echo "$status"; return 0; }
    sleep 1; waited=$((waited + 1))
  done
  echo "${status:-unknown}"
  return 1
}

sse_run_id() { sed -n 's/^data: //p' "$1" | head -n 1 | json_field run_id; }

# --- 1. configuration ---------------------------------------------------------

phase "1. configuration"
"${RELEASE[@]}" config --quiet || fail "compose.release.yml is invalid"
ok "compose.release.yml renders"
"${C[@]}" config --quiet || fail "compose.smoke.yml override is invalid"
ok "release + smoke override renders"
python3 scripts/check_release_compose.py > "$TMP/check.txt" || { cat "$TMP/check.txt"; fail "static release checks"; }
ok "static release checks ($(grep -c '^ok' "$TMP/check.txt") passed)"

if (( BUILD )); then
  phase "build"
  "${RELEASE[@]}" build --quiet api web || fail "image build"
  ok "images built"
fi

API_IMAGE="nexus-api:0.1.0"
WEB_IMAGE="nexus-web:0.1.0"

# --- 2. image hygiene -------------------------------------------------------------

phase "2. image hygiene"
for image in "$API_IMAGE" "$WEB_IMAGE"; do
  docker image inspect "$image" > /dev/null 2>&1 || fail "$image is missing; run without --no-build"
  uid="$(docker run --rm --entrypoint id "$image" -u)"
  [[ $uid != 0 ]] || fail "$image runs as root"
  ok "$image runs as uid $uid"
  envfiles="$(docker run --rm --entrypoint sh "$image" -c \
    "find / -xdev \( -name .env -o -name '.env.*' \) -not -path '*/node_modules/*' 2>/dev/null" || true)"
  [[ -z $envfiles ]] || fail "$image contains env files: $envfiles"
  ok "$image contains no .env files"
  secrets="$(docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
    | grep -Ei '^[A-Z_]*(KEY|SECRET|PASSWORD|TOKEN)[A-Z_]*=' || true)"
  [[ -z $secrets ]] || fail "$image bakes secret-like variables: $secrets"
  ok "$image bakes no secret-like environment variables"
done
leaks="$(docker run --rm --entrypoint sh "$WEB_IMAGE" -c \
  "grep -rlE 'NEXUS_|OPENAI_API_KEY|TAVILY|API_PROXY_TARGET' /app/.next/static || true")"
[[ -z $leaks ]] || fail "browser bundle references server settings: $leaks"
ok "browser bundle references no server settings"

# --- 3. startup -----------------------------------------------------------------

phase "3. startup"
"${C[@]}" up -d > "$TMP/up.log" 2>&1 || { cat "$TMP/up.log"; fail "compose up"; }
wait_healthy api
wait_healthy web
init_exit="$(docker inspect -f '{{.State.ExitCode}}' "$(container init)")"
[[ $init_exit == 0 ]] || fail "init exited $init_exit"
"${C[@]}" logs init > "$TMP/init.log" 2>&1
grep -q 'init - domain schema at head' "$TMP/init.log" || fail "init did not report the domain schema"
grep -q 'init - checkpoint tables ready' "$TMP/init.log" || fail "init did not report checkpoint tables"
ok "init exited 0 after migrations and checkpoint setup"
ok "api and web are healthy"

# --- 4. routes ------------------------------------------------------------------

phase "4. routes through web"
response="$(web GET /)"
[[ $response == 200* && $response == *"<html"* ]] || fail "web / : ${response:0:80}"
ok "GET / renders HTML"
[[ "$(web GET /api/v1/health/live)" == '200 {"status":"ok"}' ]] || fail "live through proxy"
ok "GET /api/v1/health/live is 200"
[[ "$(web GET /api/v1/health/ready)" == '200 {"status":"ok","postgres":"ok","qdrant":"ok"}' ]] \
  || fail "ready through proxy: $(web GET /api/v1/health/ready)"
ok "GET /api/v1/health/ready is 200 with postgres and qdrant ok"
[[ "$(web GET /api/v1/research/runs)" == 200* ]] || fail "run history through proxy"
ok "GET /api/v1/research/runs is 200"
for path in /docs /openapi.json; do
  [[ "$(inside_api "http://127.0.0.1:8000$path")" == 404* ]] || fail "$path is served in production"
done
ok "API docs and OpenAPI are disabled in production"
[[ "$(stub_posts)" == 0 ]] || fail "a provider call happened during startup"
ok "no provider call during startup"

# --- 5. exposure ----------------------------------------------------------------

phase "5. published ports"
"${C[@]}" ps --format json > "$TMP/ps.json"
python3 - "$TMP/ps.json" "$PORT" <<'PY' || fail "unexpected published ports"
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
port = int(sys.argv[2])
published = {
    (row["Service"], p["URL"], p["PublishedPort"])
    for row in rows
    for p in row.get("Publishers") or []
    if p.get("PublishedPort")
}
expected = {("web", "127.0.0.1", port)}
if published != expected:
    print(f"published: {sorted(published)}", file=sys.stderr)
    sys.exit(1)
PY
ok "only web is published, on 127.0.0.1:$PORT"

# --- 6. init idempotency ----------------------------------------------------------

phase "6. init rerun"
before="$(psql_value "select version_num from alembic_version")|$(psql_value "select count(*) || ':' || max(v) from checkpoint_migrations")"
"${C[@]}" run --rm init > "$TMP/init-rerun.log" 2>&1 || { cat "$TMP/init-rerun.log"; fail "init rerun"; }
after="$(psql_value "select version_num from alembic_version")|$(psql_value "select count(*) || ':' || max(v) from checkpoint_migrations")"
[[ $before == "$after" ]] || fail "schema changed on rerun: $before -> $after"
ok "init rerun exits 0 and leaves schema unchanged ($after)"

# --- 7. streaming and cancellation ----------------------------------------------

phase "7. SSE through web and client cancellation"
curl -sN -m 6 -X POST -H 'content-type: application/json' -H 'accept: text/event-stream' \
  -d "$QUESTION" "$BASE/api/v1/research/stream" > "$TMP/sse-cancel.txt" || true
[[ "$(head -n 1 "$TMP/sse-cancel.txt")" == "event: research.started" ]] \
  || fail "stream did not start with research.started"
pings="$(grep -c '^: ping' "$TMP/sse-cancel.txt" || true)"
(( pings >= 3 )) || fail "expected at least 3 heartbeats in 6s, got $pings"
! grep -Eq '^event: research\.(completed|failed|interrupted)' "$TMP/sse-cancel.txt" \
  || fail "stream ended on its own before the client disconnected"
ok "research.started then $pings heartbeats arrived before the client closed"
(( "$(stub_posts)" >= 1 )) || fail "the run never reached the model stub"
ok "the run reached the model stub"
CANCELLED_RUN="$(sse_run_id "$TMP/sse-cancel.txt")"
status="$(wait_status "$CANCELLED_RUN" interrupted 10)" || fail "cancelled run is '$status', expected interrupted"
ok "client disconnect stored run $CANCELLED_RUN as interrupted"

# --- 8. shutdown during a stream -----------------------------------------------

phase "8. API shutdown during a stream"
curl -sN -m 90 -X POST -H 'content-type: application/json' -H 'accept: text/event-stream' \
  -d "$QUESTION" "$BASE/api/v1/research/stream" > "$TMP/sse-shutdown.txt" &
stream_pid=$!
waited=0
until grep -q '^: ping' "$TMP/sse-shutdown.txt" 2>/dev/null; do
  (( waited >= 30 )) && fail "second stream produced no heartbeat"
  sleep 1; waited=$((waited + 1))
done
SHUTDOWN_RUN="$(sse_run_id "$TMP/sse-shutdown.txt")"
started=$SECONDS
"${C[@]}" stop api > /dev/null 2>&1
elapsed=$((SECONDS - started))
api_exit="$(docker inspect -f '{{.State.ExitCode}}' "$(container api)")"
[[ $api_exit != 137 ]] || fail "api was killed after the grace period (exit 137, ${elapsed}s)"
(( elapsed < 15 )) || fail "api took ${elapsed}s to stop"
ok "api stopped in ${elapsed}s with exit code $api_exit"
wait "$stream_pid" || true
response="$(web GET /api/v1/research/runs)"
[[ $response == 502* && $response == *history_unavailable* ]] || fail "proxy while api is down: ${response:0:80}"
ok "web stays up and returns 502 history_unavailable while api is down"
"${C[@]}" start api > /dev/null 2>&1
wait_healthy api
status="$(run_status "$SHUTDOWN_RUN")"
case "$status" in
  interrupted) ok "run $SHUTDOWN_RUN interrupted by shutdown is stored as interrupted" ;;
  running) warn "run $SHUTDOWN_RUN was left as running after shutdown (documented stale-running caveat)" ;;
  *) fail "run interrupted by shutdown has status '$status'" ;;
esac

# --- 9. dependency outage -------------------------------------------------------

phase "9. dependency outage"
for service in postgres qdrant; do
  "${C[@]}" stop "$service" > /dev/null 2>&1
  ready="$(web GET /api/v1/health/ready)"
  [[ $ready == 503* && $ready == *"\"$service\":\"unavailable\""* ]] || fail "ready with $service stopped: $ready"
  [[ "$(web GET /api/v1/health/live)" == 200* ]] || fail "live with $service stopped"
  "${C[@]}" start "$service" > /dev/null 2>&1
  wait_ready
  ok "$service down: ready 503 and live 200; ready recovers after restart"
done

"${C[@]}" logs --no-color > "$TMP/logs-before-restart.txt" 2>&1

# --- 10. persistence ------------------------------------------------------------

phase "10. restart persistence"
"${C[@]}" down > /dev/null 2>&1
"${C[@]}" up -d > "$TMP/up-again.log" 2>&1 || { cat "$TMP/up-again.log"; fail "compose up after down"; }
wait_healthy api
wait_healthy web
runs="$(web GET '/api/v1/research/runs?limit=50')"
for run_id in "$CANCELLED_RUN" "$SHUTDOWN_RUN"; do
  [[ $runs == *"$run_id"* ]] || fail "run $run_id is missing after restart"
done
ok "run history survives down and up"

# --- 11. secret leak scan -------------------------------------------------------

phase "11. log secret scan"
"${C[@]}" logs --no-color > "$TMP/logs-after-restart.txt" 2>&1
for needle in "$PASSWORD" "$OPENAI_SENTINEL" "$TAVILY_SENTINEL" "postgresql+asyncpg://"; do
  if grep -qF -- "$needle" "$TMP/logs-before-restart.txt" "$TMP/logs-after-restart.txt"; then
    fail "logs contain a secret or database URL"
  fi
done
ok "no password, provider key or database URL in any service log"

printf '\nall release smoke checks passed\n'
