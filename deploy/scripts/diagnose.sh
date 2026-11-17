#!/bin/sh
# Diagnose "502 Bad Gateway" / API-down problems on the server.
#
#   cd deploy && sh scripts/diagnose.sh            # Option A (docker-compose.yml)
#   COMPOSE_FILE=docker-compose.app.yml sh scripts/diagnose.sh   # Option B application host
#
# Read-only: it only inspects containers, logs and configuration. It never prints secret values.
set -u
cd "$(dirname "$0")/.." || exit 1
DC="docker compose"
[ -n "${COMPOSE_FILE:-}" ] && DC="docker compose -f $COMPOSE_FILE"
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
hdr()  { printf '\n== %s ==\n' "$*"; }
FAILED=0

hdr "1. Containers"
$DC ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Status}}' 2>/dev/null || $DC ps -a
for svc in backend frontend nginx; do
  state=$($DC ps -a --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s{print $2}')
  case "$state" in
    running) ok "$svc is running" ;;
    "") bad "$svc container does not exist (was it ever started? run: $DC up -d)" ;;
    *) bad "$svc is $state" ;;
  esac
done
mig=$($DC ps -a --format '{{.Service}} {{.State}} {{.Status}}' 2>/dev/null | awk '$1=="migrate"{print $2, $3, $4}')
case "$mig" in
  *"Exited (0)"*|*"exited (0)"*) ok "migrate finished successfully" ;;
  "") warn "migrate container not found (Option B DB host, or never run)" ;;
  *) bad "migrate: $mig — the backend does NOT start until migrations succeed. Logs below." ;;
esac

hdr "2. Migration log (last 40 lines)"
$DC logs --no-color --tail 40 migrate 2>/dev/null || true

hdr "3. Backend log (last 60 lines)"
$DC logs --no-color --tail 60 backend 2>/dev/null || true

hdr "4. Configuration (.env) — names only, values hidden"
if [ ! -f .env ]; then
  bad ".env is missing in $(pwd) (cp .env.example .env and fill it in)"
else
  for v in DJANGO_SECRET_KEY FIELD_ENCRYPTION_KEYS POSTGRES_PASSWORD POSTGRES_HOST DJANGO_ALLOWED_HOSTS REDIS_URL; do
    if grep -Eq "^$v=.+" .env; then ok "$v is set"; else bad "$v is empty or missing (production refuses to start without it)"; fi
  done
  hosts=$(grep -E '^DJANGO_ALLOWED_HOSTS=' .env | cut -d= -f2-)
  for h in backend localhost; do
    echo "$hosts" | tr ',' '\n' | grep -qx "$h" || warn "DJANGO_ALLOWED_HOSTS does not contain '$h' (health checks / internal calls get 400)"
  done
  if [ -n "${PUBLIC_HOST:-}" ]; then
    echo "$hosts" | tr ',' '\n' | grep -qx "$PUBLIC_HOST" || bad "DJANGO_ALLOWED_HOSTS does not contain $PUBLIC_HOST"
  fi
fi

hdr "5. Backend health from inside the backend container"
if $DC exec -T backend curl -fsS -m 10 http://localhost:8000/healthz/ 2>&1; then echo; ok "backend answers on :8000"; else bad "backend does not answer on :8000 (see backend log above)"; fi

hdr "6. Backend reachable from nginx (this is what returns 502)"
if $DC exec -T nginx wget -q -T 10 -O - http://backend:8000/healthz/ 2>&1; then echo; ok "nginx can reach backend:8000"; else bad "nginx cannot reach backend:8000"; fi
$DC exec -T nginx nginx -t 2>&1 | tail -2

hdr "7. Database"
if $DC exec -T backend python manage.py check --database default --deploy >/dev/null 2>&1; then
  ok "Django system checks pass and the database is reachable"
else
  bad "Django checks failed:"; $DC exec -T backend python manage.py check --database default --deploy 2>&1 | tail -15
fi
$DC exec -T backend python manage.py showmigrations --plan 2>/dev/null | grep -c '\[ \]' | awk '{ if ($1>0) print "  \033[31mFAIL\033[0m " $1 " unapplied migrations"; else print "  \033[32mOK\033[0m   all migrations applied" }'

hdr "8. Disk and memory"
df -h / /srv/pizza 2>/dev/null | sed 's/^/  /'
free -m 2>/dev/null | sed 's/^/  /'
dmesg 2>/dev/null | grep -i "out of memory\|oom-kill" | tail -3 | sed 's/^/  /'

hdr "Result"
if [ "$FAILED" = 0 ]; then
  echo "  No failures found. If the site still shows 502, restart nginx so it re-resolves the backend:"
  echo "    $DC up -d --force-recreate nginx"
else
  echo "  Fix the FAIL items above (see deploy/TROUBLESHOOTING.md), then:"
  echo "    $DC up -d --build && $DC up -d --force-recreate nginx"
fi
