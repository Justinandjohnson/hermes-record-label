#!/usr/bin/env bash
# =============================================================================
# sync_to_cloud.sh — Upload new audio files to Backblaze B2 vault
# =============================================================================
# Uses boto3 via vault_tools.py — no rclone, no extra dependencies.
# Credentials come from .env (B2_WRITE_KEY_ID, B2_WRITE_APPLICATION_KEY).
#
# Usage:
#   ./scripts/sync_to_cloud.sh                    # syncs configured watch folder
#   ./scripts/sync_to_cloud.sh /path/to/folder    # explicit folder
#   ./scripts/sync_to_cloud.sh /path/to/file.wav  # single file
#   ./scripts/sync_to_cloud.sh --status           # show last sync log
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${AI_RECORD_LABEL_DATA:-$HOME/Library/Application Support/ai-record-label}"
VENV_PYTHON="$ROOT/.venv/bin/python3"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${CYAN}▸${NC} $1"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; }

# Load .env
ENV_FILE="$ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^[A-Z_]+=.*' "$ENV_FILE" | grep -v '^#')
  set +a
fi

# --status mode
if [[ "${1:-}" == "--status" ]]; then
  "$VENV_PYTHON" -c "
import sys; sys.path.insert(0, '$ROOT')
from scripts.vault_tools import get_sync_status
import json
print(json.dumps(get_sync_status(), indent=2, default=str))
"
  exit 0
fi

# Resolve target (file or folder)
if [[ -n "${1:-}" && "${1:-}" != "--"* ]]; then
  TARGET="$1"
else
  # Read from settings.json or WATCH_FOLDER env
  TARGET="${WATCH_FOLDER:-}"
  if [[ -z "$TARGET" ]]; then
    SETTINGS="$DATA_DIR/settings.json"
    if [[ -f "$SETTINGS" ]]; then
      TARGET=$(python3 -c "
import json
try:
    d = json.load(open('$SETTINGS'))
    print(d.get('ableton_export_folder') or d.get('ableton_project_folder') or '')
except: print('')
" 2>/dev/null || true)
    fi
  fi
fi

if [[ -z "${TARGET:-}" ]]; then
  err "No folder configured. Either:"
  echo "  1. Pass a path: $0 /path/to/music"
  echo "  2. Set Ableton Export Folder in Settings"
  exit 1
fi

if [[ ! -e "$TARGET" ]]; then
  err "Path does not exist: $TARGET"
  exit 1
fi

log "Uploading to Backblaze B2 vault..."
log "Source: $TARGET"

"$VENV_PYTHON" - "$TARGET" "$ROOT" <<'PYEOF'
import sys, os, time, pathlib

target_arg = sys.argv[1]
root = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(root))

# Load .env so B2 credentials are available
env_file = root / '.env'
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
from scripts.vault_tools import _get_s3_client, BUCKET, log_sync_operation

target = pathlib.Path(target_arg)
AUDIO_EXTENSIONS = {'.wav', '.flac', '.mp3', '.aiff', '.aif', '.ogg', '.m4a'}

# Collect files
if target.is_file():
    files = [target] if target.suffix.lower() in AUDIO_EXTENSIONS else []
else:
    files = [f for f in target.rglob('*') if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]

if not files:
    print(f'No audio files found in {target}')
    sys.exit(0)

client = _get_s3_client(write=True)

uploaded = 0
skipped = 0
errors = 0

for f in sorted(files):
    # B2 key: music/<relative-path-from-target>
    if target.is_file():
        b2_key = f'music/{f.name}'
    else:
        try:
            rel = f.relative_to(target)
        except ValueError:
            rel = pathlib.Path(f.name)
        b2_key = f'music/{rel}'

    start = time.time()
    try:
        # Check if already uploaded (by size match — avoid re-uploading identical files)
        try:
            head = client.head_object(Bucket=BUCKET, Key=str(b2_key))
            if head['ContentLength'] == f.stat().st_size:
                skipped += 1
                continue
        except Exception:
            pass  # Object doesn't exist — upload it

        file_size = f.stat().st_size
        client.upload_file(
            str(f), BUCKET, str(b2_key),
            ExtraArgs={'ContentType': 'audio/mpeg'},
        )
        duration_ms = int((time.time() - start) * 1000)
        log_sync_operation(
            operation='upload',
            status='success',
            b2_key=str(b2_key),
            file_path=str(f),
            bytes_transferred=file_size,
            duration_ms=duration_ms,
        )
        size_mb = round(file_size / (1024*1024), 1)
        print(f'  ✓ {f.name} ({size_mb} MB)')
        uploaded += 1
    except Exception as e:
        log_sync_operation(
            operation='upload',
            status='failed',
            b2_key=str(b2_key),
            file_path=str(f),
            error=str(e)[:500],
        )
        print(f'  ✗ {f.name}: {e}')
        errors += 1

print(f'\nDone: {uploaded} uploaded, {skipped} already in vault, {errors} errors')
PYEOF