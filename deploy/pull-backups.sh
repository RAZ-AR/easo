#!/bin/sh
# Copy the server's backups to this computer: a second place, in case the server is lost.
# Usage:  EASO_HOST=ubuntu@<IP> EASO_KEY=~/.ssh/qb_oracle ./deploy/pull-backups.sh
# On a Mac it can run daily with launchd: see docs/06-deploy-oracle.md, section 6.
set -eu
host="${EASO_HOST:?set EASO_HOST=ubuntu@<server IP>}"
key="${EASO_KEY:-$HOME/.ssh/qb_oracle}"
dest="${EASO_BACKUP_DEST:-$HOME/easo-backups}"
src="${EASO_REMOTE_DIR:-easo-backups}"
mkdir -p "$dest"
# No --delete: what was copied here stays even after the server drops it
rsync -az --include "*.gz" --exclude "*" -e "ssh -i $key -o BatchMode=yes -o ConnectTimeout=20" \
	"$host:$src/" "$dest/"
echo "$(date '+%Y-%m-%d %H:%M') pulled into $dest: $(ls "$dest" | wc -l | tr -d ' ') files"
