#!/usr/bin/env bash
# =============================================================================
# AI Record Label — Test SMS Script
# =============================================================================
# Sends a test SMS via Twilio to verify the messaging pipeline is working.
# Uses curl to call the Twilio REST API directly.
#
# Usage:
#   ./test_sms.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load environment
ENV_FILE="$CONFIG_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Error: .env file not found at $ENV_FILE${NC}"
    echo "Run setup.sh first."
    exit 1
fi

set -a
source "$ENV_FILE"
set +a

# Verify required vars
for var in TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_PHONE_NUMBER ARTIST_PHONE_NUMBER; do
    if [ -z "${!var:-}" ]; then
        echo -e "${RED}Error: $var is not set in .env${NC}"
        exit 1
    fi
done

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Twilio SMS Test${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo "  From: $TWILIO_PHONE_NUMBER"
echo "  To:   $ARTIST_PHONE_NUMBER"
echo ""

# Send the test message
TEST_MESSAGE="[AI Record Label] System test — your label is online. If you got this, SMS is working. Reply 'hey' to test inbound."

echo -e "${YELLOW}Sending test SMS...${NC}"

RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID/Messages.json" \
    -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
    --data-urlencode "To=$ARTIST_PHONE_NUMBER" \
    --data-urlencode "From=$TWILIO_PHONE_NUMBER" \
    --data-urlencode "Body=$TEST_MESSAGE")

# Split response body and HTTP status code
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "201" ]; then
    # Extract SID from response for tracking
    MSG_SID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sid','unknown'))" 2>/dev/null || echo "unknown")
    echo -e "${GREEN}✓ SMS sent successfully${NC}"
    echo "  Message SID: $MSG_SID"
    echo "  Status: queued for delivery"
    echo ""
    echo "  Check your phone for the test message."
    echo "  Reply 'hey' to test inbound webhook routing."
elif [ "$HTTP_CODE" = "401" ]; then
    echo -e "${RED}✗ Authentication failed (401)${NC}"
    echo "  Check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env"
elif [ "$HTTP_CODE" = "400" ]; then
    ERROR_MSG=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message','unknown error'))" 2>/dev/null || echo "$BODY")
    echo -e "${RED}✗ Bad request (400): $ERROR_MSG${NC}"
    echo "  Check TWILIO_PHONE_NUMBER and ARTIST_PHONE_NUMBER format (+1XXXXXXXXXX)"
else
    echo -e "${RED}✗ Unexpected response (HTTP $HTTP_CODE)${NC}"
    echo "  Response: $BODY"
fi
