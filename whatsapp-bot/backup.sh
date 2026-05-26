#!/bin/bash
# Backup diario de wa-bot: comprime /opt/whatsapp-bot/data y mantiene últimos 30 días.
# Cron: 0 3 * * * /opt/whatsapp-bot/backup.sh >> /var/log/wa-bot-backup.log 2>&1

set -e
BACKUP_DIR="/var/backups/whatsapp-bot"
SRC_DIR="/opt/whatsapp-bot/data"
TS=$(date +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/wabot_$TS.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando backup → $OUT"
tar -czf "$OUT" -C "$(dirname $SRC_DIR)" "$(basename $SRC_DIR)"
SIZE=$(du -h "$OUT" | cut -f1)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] OK · $OUT ($SIZE)"

# Rotación: mantener solo últimos 30 backups
KEEP=30
TOTAL=$(ls -1 "$BACKUP_DIR"/wabot_*.tar.gz 2>/dev/null | wc -l)
if [ "$TOTAL" -gt "$KEEP" ]; then
    DELETE=$((TOTAL - KEEP))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Borrando $DELETE backups antiguos"
    ls -1t "$BACKUP_DIR"/wabot_*.tar.gz | tail -n "$DELETE" | xargs -r rm -f
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backups actuales: $(ls -1 $BACKUP_DIR/wabot_*.tar.gz 2>/dev/null | wc -l)"
