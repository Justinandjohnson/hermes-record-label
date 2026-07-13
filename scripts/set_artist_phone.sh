#!/usr/bin/env bash
# Usage: ./scripts/set_artist_phone.sh +15551234567
# Updates the artist phone number across all Hermes agent profiles.

set -euo pipefail

NEW_NUMBER="${1:?Usage: $0 +1XXXXXXXXXX}"
PROFILES="$HOME/.hermes/profiles"

if [[ ! "$NEW_NUMBER" =~ ^\+1[0-9]{10}$ ]]; then
  echo "⚠  Number should be E.164 format (e.g. +15551234567)"
  read -p "Continue anyway? [y/N] " -n 1 -r
  echo
  [[ $REPLY =~ ^[Yy]$ ]] || exit 1
fi

for profile in a_and_r manager creative_director bandcamp; do
  ENV_FILE="$PROFILES/$profile/.env"
  if [[ -f "$ENV_FILE" ]]; then
    sed -i '' "s|^ARTIST_PHONE=.*|ARTIST_PHONE=$NEW_NUMBER|" "$ENV_FILE"
    sed -i '' "s|^SMS_ALLOWED_USERS=.*|SMS_ALLOWED_USERS=$NEW_NUMBER|" "$ENV_FILE"
    sed -i '' "s|^SMS_HOME_CHANNEL=.*|SMS_HOME_CHANNEL=$NEW_NUMBER|" "$ENV_FILE"
    echo "✓ $profile → $NEW_NUMBER"
  fi
done

echo "Done. Restart gateways to pick up the change:"
echo "  hermes --profile a_and_r gateway restart"
echo "  hermes --profile manager gateway restart"
echo "  hermes --profile creative_director gateway restart"
echo "  hermes --profile bandcamp gateway restart"
