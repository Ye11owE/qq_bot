#!/bin/bash
# QQ Bot Alert Notification Module
# Supports: DingTalk / Feishu / WeCom / Bark / PushPlus / ServerChan / Webhook
# Configure via environment variables in $QQBOT_DIR/.env.notify

BOT_DIR="${QQBOT_DIR:-/opt/qqbot}"
NOTIFY_CONFIG="${QQBOT_NOTIFY_CONFIG:-$BOT_DIR/.env.notify}"
NOTIFY_LOG="${QQBOT_NOTIFY_LOG:-$BOT_DIR/notify.log}"
COOLDOWN_FILE="/tmp/qqbot_notify_cooldown"
NOTIFY_COOLDOWN=1800  # Same alert won't repeat within 30 minutes

# Load config
if [ -f "$NOTIFY_CONFIG" ]; then
    source "$NOTIFY_CONFIG"
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$NOTIFY_LOG"
}

# Rate limit: prevent same alert spamming
check_notify_cooldown() {
    local alert_type="$1"
    local key_file="/tmp/qqbot_notify_${alert_type}_ts"
    local last_ts
    last_ts=$(cat "$key_file" 2>/dev/null || echo 0)
    local now
    now=$(date +%s)
    if [ $(( now - last_ts )) -lt "$NOTIFY_COOLDOWN" ]; then
        return 1  # Still in cooldown
    fi
    return 0
}

record_notify_cooldown() {
    local key_file="/tmp/qqbot_notify_${1}_ts"
    date +%s > "$key_file"
}

url_encode_path_segment() {
    python3 -c 'from urllib.parse import quote; import sys; print(quote(sys.argv[1], safe=""))' "$1"
}

send_dingtalk() {
    local title="$1" content="$2"
    [ -z "$DINGTALK_WEBHOOK" ] && return 1
    local payload
    payload=$(python3 -c 'import json,sys; print(json.dumps({"msgtype":"markdown","markdown":{"title":sys.argv[1],"text":sys.argv[2]}}))' "$title" "$content") || return 1
    curl -fsS -X POST "$DINGTALK_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --max-time 10 > /dev/null 2>&1
}

send_feishu() {
    local title="$1" content="$2"
    [ -z "$FEISHU_WEBHOOK" ] && return 1
    local payload
    payload=$(python3 -c 'import json,sys; print(json.dumps({"msg_type":"interactive","card":{"header":{"title":{"tag":"plain_text","content":sys.argv[1]},"template":"red"},"elements":[{"tag":"markdown","content":sys.argv[2]}]}}))' "$title" "$content") || return 1
    curl -fsS -X POST "$FEISHU_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --max-time 10 > /dev/null 2>&1
}

send_wecom() {
    local title="$1" content="$2"
    [ -z "$WECOM_WEBHOOK" ] && return 1
    local payload
    payload=$(python3 -c 'import json,sys; print(json.dumps({"msgtype":"markdown","markdown":{"content":"## " + sys.argv[1] + "\\n" + sys.argv[2]}}))' "$title" "$content") || return 1
    curl -fsS -X POST "$WECOM_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --max-time 10 > /dev/null 2>&1
}

send_bark() {
    local title="$1" content="$2"
    [ -z "$BARK_URL" ] && return 1
    curl -fsS "${BARK_URL}/$(url_encode_path_segment "$title")/$(url_encode_path_segment "$content")?sound=alarm" \
        --max-time 10 > /dev/null 2>&1
}

send_pushplus() {
    local title="$1" content="$2"
    [ -z "$PUSHPLUS_TOKEN" ] && return 1
    local payload
    payload=$(python3 -c 'import json,sys; print(json.dumps({"token":sys.argv[1],"title":sys.argv[2],"content":sys.argv[3],"template":"markdown"}))' "$PUSHPLUS_TOKEN" "$title" "$content") || return 1
    curl -fsS -X POST "https://www.pushplus.plus/send" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --max-time 10 > /dev/null 2>&1
}

send_serverchan() {
    local title="$1" content="$2"
    [ -z "$SERVERCHAN_KEY" ] && return 1
    curl -fsS -X POST "https://sctapi.ftqq.com/${SERVERCHAN_KEY}.send" \
        --data-urlencode "title=$title" --data-urlencode "desp=$content" \
        --max-time 10 > /dev/null 2>&1
}

send_webhook() {
    local title="$1" content="$2"
    [ -z "$CUSTOM_WEBHOOK" ] && return 1
    local payload
    payload=$(python3 -c 'import json,sys; print(json.dumps({"alert_type":"qqbot","title":sys.argv[1],"content":sys.argv[2],"timestamp":sys.argv[3],"hostname":sys.argv[4]}))' "$title" "$content" "$(date -Iseconds)" "$(hostname)") || return 1
    curl -fsS -X POST "$CUSTOM_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --max-time 10 > /dev/null 2>&1
}

# Main send function: tries all configured channels
send_alert() {
    local alert_type="$1"   # e.g. RISK_CONTROL, CAPTCHA, OFFLINE, RECOVERED
    local title="$2"
    local content="$3"

    # Check cooldown
    if ! check_notify_cooldown "$alert_type"; then
        log "SKIP: $alert_type (cooldown active)"
        return 0
    fi

    local sent=0

    send_dingtalk "$title" "$content" && sent=$((sent+1))
    send_feishu "$title" "$content" && sent=$((sent+1))
    send_wecom "$title" "$content" && sent=$((sent+1))
    send_bark "$title" "$content" && sent=$((sent+1))
    send_pushplus "$title" "$content" && sent=$((sent+1))
    send_serverchan "$title" "$content" && sent=$((sent+1))
    send_webhook "$title" "$content" && sent=$((sent+1))

    if [ "$sent" -gt 0 ]; then
        record_notify_cooldown "$alert_type"
        log "SENT: $alert_type via $sent channel(s)"
    else
        log "WARN: $alert_type — no notification channel configured or all failed"
    fi
}

# Allow direct invocation: notify.sh TYPE TITLE CONTENT
if [ $# -ge 3 ]; then
    send_alert "$1" "$2" "$3"
fi
