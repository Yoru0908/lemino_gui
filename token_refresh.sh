#!/bin/bash
# Lemino token refresher - runs on M1 Mac
# Reads token from Chrome's localStorage and pushes to Homeserver
#
# Setup:
#   chmod +x token_refresh.sh
#   crontab -e → */30 * * * * /path/to/token_refresh.sh
#
# Prerequisites:
#   - Chrome open with Lemino logged in
#   - Chrome setting: View > Developer > Allow JavaScript from Apple Events
#   - SSH key auth to Homeserver

REMOTE_HOST="homesever"  # SSH alias (注意: 用户 SSH config 中拼写如此)
REMOTE_TOKEN_PATH="~/lemino/.token"
LOCAL_LOG="/tmp/lemino_token_refresh.log"

# Read token from Chrome
TOKEN=$(osascript -e '
tell application "Google Chrome"
    repeat with w in windows
        repeat with t in tabs of w
            if URL of t contains "lemino.docomo.ne.jp" then
                set tokenVal to execute t javascript "localStorage.getItem('"'"'X-Service-Token'"'"')"
                return tokenVal
            end if
        end repeat
    end repeat
end tell
' 2>/dev/null)

if [ -z "$TOKEN" ] || [ "$TOKEN" = "missing value" ]; then
    echo "$(date): FAIL - no token from Chrome" >> "$LOCAL_LOG"
    exit 1
fi

# Push to Homeserver
echo "{\"x-service-token\": \"$TOKEN\"}" | ssh "$REMOTE_HOST" "cat > $REMOTE_TOKEN_PATH" 2>/dev/null

if [ $? -eq 0 ]; then
    echo "$(date): OK - token ${TOKEN:0:8}... pushed to $REMOTE_HOST" >> "$LOCAL_LOG"
else
    echo "$(date): FAIL - SSH push failed" >> "$LOCAL_LOG"
    exit 1
fi
