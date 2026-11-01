#!/bin/sh
# One-off move of a running QB stack to the Easo names, keeping all data.
#   ./deploy/rename-to-easo.sh          local stack (docker-compose.yml):       qb      -> easo
#   ./deploy/rename-to-easo.sh prod     server stack (docker-compose.prod.yml): qb-prod -> easo-prod
# What it does: dumps the database, stops the old stack, copies its volumes under the new project name,
# renames the Postgres role and databases (qb -> easo, qb_test -> easo_test) and starts the new stack.
# The old volumes are left untouched: remove them by hand once the new stack is checked.
set -eu
cd "$(dirname "$0")/.."

if [ "${1:-}" = prod ]; then
	old=qb-prod new=easo-prod file=docker-compose.prod.yml vols="pgdata redisdata media caddy_data caddy_config"
	backups="$HOME/easo-backups"
else
	old=qb new=easo file=docker-compose.yml vols="pgdata media"
	backups="${TMPDIR:-/tmp}"
fi

env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
password="${POSTGRES_PASSWORD:-$(env_value POSTGRES_PASSWORD)}"
if [ -z "$password" ]; then
	[ "$old" = qb ] || { echo "POSTGRES_PASSWORD is not set in .env" >&2; exit 1; }
	password=easo  # the local default in docker-compose.yml
fi

docker volume inspect "${old}_pgdata" > /dev/null 2>&1 || { echo "no volume ${old}_pgdata: nothing to move" >&2; exit 1; }
for v in $vols; do
	if docker volume inspect "${new}_$v" > /dev/null 2>&1; then
		echo "volume ${new}_$v already exists: stopping so nothing is overwritten" >&2
		exit 1
	fi
done

# 0. Build the new images while the old stack still serves, so the downtime is only steps 1-5
profiles=""
docker ps --format "{{.Names}}" | grep -qx "${old}-bot-1" && profiles="--profile bot"  # the optional bot
# shellcheck disable=SC2086
docker compose -p "$new" -f "$file" $profiles build

# 1. A full dump first, from the running old database
mkdir -p "$backups"
dump="$backups/before-easo-$(date +%Y%m%d-%H%M).sql.gz"
docker exec "${old}-db-1" pg_dumpall -U qb | gzip > "$dump"
gzip -t "$dump"
gunzip -c "$dump" | grep -q "PostgreSQL database cluster dump complete"
echo "dump: $dump"

# 2. Stop the old stack (volumes stay)
# shellcheck disable=SC2086
docker compose -p "$old" -f "$file" $profiles down --remove-orphans

# 3. Copy every volume under the new project name
for v in $vols; do
	docker volume inspect "${old}_$v" > /dev/null 2>&1 || continue
	docker volume create --label "com.docker.compose.project=$new" --label "com.docker.compose.volume=$v" \
		"${new}_$v" > /dev/null
	docker run --rm -v "${old}_$v:/from:ro" -v "${new}_$v:/to" alpine sh -c 'cp -a /from/. /to/'
	echo "volume ${old}_$v -> ${new}_$v"
done

# 4. Rename the role and the databases inside the copied data
docker compose -p "$new" -f "$file" up -d db
until docker compose -p "$new" -f "$file" exec -T db pg_isready -q; do sleep 1; done
psql() { docker compose -p "$new" -f "$file" exec -T db psql -v ON_ERROR_STOP=1 -q "$@"; }
# A role cannot rename itself: a temporary superuser does it
psql -U qb -d postgres -c "CREATE ROLE easo_rename SUPERUSER LOGIN"
psql -U easo_rename -d postgres <<'SQL'
ALTER ROLE qb RENAME TO easo;
ALTER DATABASE qb RENAME TO easo;
SELECT 'ALTER DATABASE qb_test RENAME TO easo_test' WHERE EXISTS (SELECT FROM pg_database WHERE datname = 'qb_test')
\gexec
SQL
printf '%s\n' "ALTER ROLE easo PASSWORD :'pw';" "DROP ROLE easo_rename;" | psql -U easo -d easo -v pw="$password"
echo "postgres: role and databases renamed to easo"

# 5. Start everything under the new name (migrations run on api start)
# shellcheck disable=SC2086
docker compose -p "$new" -f "$file" $profiles up -d --build
i=0
until docker compose -p "$new" -f "$file" exec -T api python -c \
	"import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready')" > /dev/null 2>&1; do
	i=$((i + 1))
	[ "$i" -lt 90 ] || { echo "api is not ready after 3 minutes: check 'docker compose -f $file logs api'" >&2; exit 1; }
	sleep 2
done
echo "done: $new is up and ready."
echo "When everything looks right, remove the old volumes:"
for v in $vols; do printf '  docker volume rm %s_%s\n' "$old" "$v"; done
