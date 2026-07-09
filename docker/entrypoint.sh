#!/bin/bash
set -e

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mailpush"
CONFIG_FILE="${MAILPUSH_CONFIG:-$CONFIG_DIR/config.json}"

echo "MailPush Docker Container"
echo "========================="
echo "Config dir:  $CONFIG_DIR"
echo "Config file: $CONFIG_FILE"
echo ""

# If no config exists, create default
if [ ! -f "$CONFIG_FILE" ]; then
    echo "No config found. Creating default config..."
    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_FILE" << 'CFGEOF'
{
  "accounts": [],
  "delivery_targets": [],
  "deliveries": {},
  "routes": [],
  "processing": {
    "summary": true,
    "translate": false,
    "attachment_info": true,
    "body_max_chars": 4000,
    "merge_batch": false,
    "merge_interval": 30
  },
  "filters": {
    "allow_senders": [],
    "block_senders": [],
    "allow_keywords": [],
    "block_keywords": [],
    "account_rules": {}
  },
  "smtp_reply_from": "",
  "api_token": "",
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  }
}
CFGEOF
    chmod 600 "$CONFIG_FILE"
    echo "Default config created. Edit and restart to add accounts."
    echo ""
    echo "Mount your config:"
    echo "  docker cp config.json mailpush:$CONFIG_FILE"
    echo "  docker restart mailpush"
fi

echo "Starting MailPush server..."
exec python -m mailpush serve --host 0.0.0.0 --port 8080
