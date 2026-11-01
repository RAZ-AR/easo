#!/bin/sh
# Daily backup of the database and uploaded files into ~/easo-backups (keeps 14 days locally).
# Cron:  0 3 * * *  cd ~/easo && ./deploy/backup.sh >> ~/easo-backups/backup.log 2>&1
#
# Optional, from .env:
#   BACKUP_REMOTE    rclone target outside this machine, e.g. "oci:easo-backups" (Oracle Object Storage);
#                    copies are kept there for BACKUP_REMOTE_DAYS (default 60)
#   BACKUP_PING_URL  healthchecks.io check: pinged on success, "/fail" on error; silence = alert
set -eu
cd "$(dirname "$0")/.."

env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
remote="${BACKUP_REMOTE:-$(env_value BACKUP_REMOTE)}"
remote_days="${BACKUP_REMOTE_DAYS:-$(env_value BACKUP_REMOTE_DAYS)}"
ping="${BACKUP_PING_URL:-$(env_value BACKUP_PING_URL)}"

ping_status() {
	[ -n "$ping" ] && curl -fsS -m 10 --retry 3 "$ping$1" > /dev/null || true
}
trap 'ping_status /fail' EXIT

dir="${BACKUP_DIR:-$HOME/easo-backups}"
stamp="$(date +%Y%m%d-%H%M)"
mkdir -p "$dir"
compose="docker compose -f ${EASO_COMPOSE_FILE:-docker-compose.prod.yml}"

$compose exec -T db pg_dump -U easo easo | gzip > "$dir/db-$stamp.sql.gz"
$compose exec -T api tar -C /app -czf - media > "$dir/media-$stamp.tar.gz"

# A broken or empty archive must not count as a backup
gzip -t "$dir/db-$stamp.sql.gz"
gzip -t "$dir/media-$stamp.tar.gz"
gunzip -c "$dir/db-$stamp.sql.gz" | grep -q "PostgreSQL database dump complete"

if [ -n "$remote" ]; then
	rclone copy "$dir" "$remote" --include "*-$stamp.*"
	rclone delete "$remote" --min-age "${remote_days:-60}d"
fi

find "$dir" -type f -name "*.gz" -mtime +14 -delete
trap - EXIT
ping_status ""
echo "backup done: $dir ($stamp)${remote:+, copied to $remote}"
