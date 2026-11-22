#!/bin/bash
# QQ Bot Watchdog v4.0 - Optimized for stability & anti-risk-control
# Changes: fix backup/cleanup indentation, add message heartbeat check,
#          smarter restart backoff, reduce restart frequency to avoid triggering risk control

BOT_DIR="${QQBOT_DIR:-/opt/qqbot}"
PYTHON_BIN="${QQBOT_PYTHON:-/opt/qqbot_venv/bin/python3}"
NAPCAT_SERVICE="${QQBOT_NAPCAT_SERVICE:-qqbot-napcat.service}"
NONEBOT_SERVICE="${QQBOT_NONEBOT_SERVICE:-qqbot-nonebot.service}"
QQ_CONFIG_DIR="${QQBOT_CONFIG_DIR:-/root/.config/QQ}"
LOG="${BOT_DIR}/watchdog.log"
CHECK_INTERVAL=180              # 3min check (faster detection, but smarter restart)
MAX_RESTART_ATTEMPTS=2          # Reduced from 3 → fewer restarts = less risk
RESTART_COOLDOWN=1800           # 30min cooldown after max attempts (was 10min)
CAPTCHA_COOLDOWN=3600           # 60min for captcha (was 30min, give more time)
RISK_CONTROL_COOLDOWN=7200      # 2hr cooldown for "安全风险" messages
LOG_MAX_SIZE=5242880            # 5MB log rotation
send_mail_alert() {
    "$PYTHON_BIN" "$BOT_DIR/mail_alert.py" "$@"
}
COOLDOWN_FILE="/tmp/qqbot_watchdog_cooldown"
FAIL_COUNTER_FILE="/tmp/qqbot_watchdog_fail_count"
LAST_MSG_TS_FILE="/tmp/qqbot_last_msg_ts"
CRASH_CLEAN_MARKER="/tmp/qqbot_crash_clean_ts"
AUTH_BACKUP_MARKER="/tmp/qqbot_auth_backup_ts"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
    local log_size
    log_size=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
    if [ "$log_size" -gt "$LOG_MAX_SIZE" ]; then
        tail -c 1048576 "$LOG" > "${LOG}.tmp"
        mv "${LOG}.tmp" "$LOG"
        log "INFO: Log rotated (was ${log_size} bytes)"
    fi
}

get_fail_count() { cat "$FAIL_COUNTER_FILE" 2>/dev/null || echo 0; }
set_fail_count() { echo "$1" > "$FAIL_COUNTER_FILE"; }

update_last_msg_ts() {
    # Extract latest message timestamp from NapCat logs
    local ts
    ts=$(journalctl -u "$NAPCAT_SERVICE" --since "5 minutes ago" --no-pager 2>/dev/null \
         | grep -E "接收 <-|发送 ->" | tail -1 \
         | grep -oP '^\S+ \K\d{2}:\d{2}:\d{2}' || true)
    if [ -n "$ts" ]; then
        date -d "$(date +%Y-%m-%d) $ts" +%s > "$LAST_MSG_TS_FILE" 2>/dev/null
    fi
}

check_qq_login_status() {
    local recent_logs
    recent_logs=$(journalctl -u "$NAPCAT_SERVICE" --since "${CHECK_INTERVAL} seconds ago" --no-pager 2>/dev/null)

    # Priority 1: Risk control / security restriction → longest cooldown
    local risk_count
    risk_count=$(echo "$recent_logs" | grep -ciE "安全风险|功能使用受限|账号异常|freeze|restrict")
    if [ "$risk_count" -gt 0 ]; then
        echo "RISK_CONTROL"
        return 3
    fi

    # Priority 2: Captcha / verification required
    local captcha_count
    captcha_count=$(echo "$recent_logs" | grep -ciE "需要验证码|proofWaterUrl|needCaptcha|sms-verify-login")
    if [ "$captcha_count" -gt 0 ]; then
        echo "CAPTCHA_REQUIRED"
        return 2
    fi

    # Priority 3: New device verification
    local new_device
    new_device=$(echo "$recent_logs" | grep -ciE "needNewDevice|新设备验证|设备锁")
    if [ "$new_device" -gt 0 ]; then
        echo "NEW_DEVICE_VERIFY"
        return 2
    fi

    # Priority 4: Login errors
    local login_errors
    login_errors=$(echo "$recent_logs" | grep -ciE "Login Error|快速登录错误|登录态已失效|ErrCode")
    if [ "$login_errors" -gt 0 ]; then
        echo "LOGIN_ERROR"
        return 1
    fi

    # Priority 5: WebSocket offline without reconnection
    local ws_connected offline_events
    ws_connected=$(journalctl -u "$NONEBOT_SERVICE" --since "5 minutes ago" --no-pager 2>/dev/null | grep -c "Bot.*connected")
    offline_events=$(journalctl -u "$NONEBOT_SERVICE" --since "${CHECK_INTERVAL} seconds ago" --no-pager 2>/dev/null | grep -ciE "bot_offline|WebSocket.*closed")
    if [ "$offline_events" -gt 0 ] && [ "$ws_connected" -eq 0 ]; then
        echo "OFFLINE"
        return 1
    fi

    echo "OK"
    return 0
}

do_restart() {
    log "ACTION: Restarting $NAPCAT_SERVICE..."
    systemctl restart "$NAPCAT_SERVICE"
    # Wait longer for QQ to fully initialize (NTQQ is slow on low-mem servers)
    sleep 120
    if systemctl is-active --quiet "$NAPCAT_SERVICE"; then
        sleep 15
        local status
        status=$(check_qq_login_status)
        if [ "$status" = "OK" ]; then
            log "RECOVERED: QQ login successful after restart"
            send_mail_alert "RECOVERED" "✅ QQ Bot 已恢复" "QQ 登录成功，服务已恢复正常\n时间: $(date +%H:%M:%S)"
            set_fail_count 0
            return 0
        else
            log "WARNING: Service restarted but QQ still not logged in (status=$status)"
            return 1
        fi
    else
        log "CRITICAL: $NAPCAT_SERVICE failed to restart!"
        send_mail_alert "CRITICAL" "❌ QQ Bot 重启失败" "$NAPCAT_SERVICE 无法启动\n需要手动干预！\n时间: $(date +%H:%M:%S)"
        return 1
    fi
}

enter_cooldown() {
    local duration="$1"
    local reason="$2"
    local cooldown_end=$(( $(date +%s) + duration ))
    echo "$cooldown_end" > "$COOLDOWN_FILE"
    log "ALERT: ${reason}. Entering cooldown ${duration}s."
    send_mail_alert "COOLDOWN" "🚨 QQ Bot 告警: ${reason}" "状态: ${reason}\n冷却时间: ${duration}秒\n时间: $(date +%H:%M:%S)"
}

# === Periodic maintenance tasks (run independently of main loop) ===
do_maintenance() {
    local now_ts
    now_ts=$(date +%s)

    # Clean crash files older than 7 days (every 6 hours)
    local last_clean
    last_clean=$(cat "$CRASH_CLEAN_MARKER" 2>/dev/null || echo 0)
    if [ $(( now_ts - last_clean )) -ge 21600 ]; then
        find "$QQ_CONFIG_DIR/crash_files/" -type f -mmin +10080 -delete 2>/dev/null
        echo "$now_ts" > "$CRASH_CLEAN_MARKER"
        log "MAINT: Crash files cleaned (older than 7 days)"
    fi

    # Backup auth tokens (every 12 hours)
    local last_auth_backup
    last_auth_backup=$(cat "$AUTH_BACKUP_MARKER" 2>/dev/null || echo 0)
    if [ $(( now_ts - last_auth_backup )) -ge 43200 ]; then
        local BACKUP_DIR="$BOT_DIR/auth_backups/$(date +%Y%m%d_%H%M%S)"
        mkdir -p "$BACKUP_DIR"
        cp -r "$QQ_CONFIG_DIR/auth/"* "$BACKUP_DIR/" 2>/dev/null
        cp -r "$QQ_CONFIG_DIR/NapCat/device.json" "$BACKUP_DIR/" 2>/dev/null
        echo "$now_ts" > "$AUTH_BACKUP_MARKER"
        log "MAINT: Auth token backed up to $BACKUP_DIR"
        # Keep only last 5 backups
        ls -dt "$BOT_DIR"/auth_backups/*/ 2>/dev/null | tail -n +6 | xargs rm -rf 2>/dev/null
    fi
}

# === Main Loop ===
log "Watchdog v4.0 started (interval=${CHECK_INTERVAL}s, max_attempts=${MAX_RESTART_ATTEMPTS}, restart_cooldown=${RESTART_COOLDOWN}s, captcha_cooldown=${CAPTCHA_COOLDOWN}s, risk_cooldown=${RISK_CONTROL_COOLDOWN}s)"
set_fail_count 0

while true; do
    # Run maintenance tasks regardless of cooldown state
    do_maintenance

    # Check cooldown
    if [ -f "$COOLDOWN_FILE" ]; then
        cooldown_end=$(cat "$COOLDOWN_FILE" 2>/dev/null)
        now=$(date +%s)
        if [ "$now" -lt "$cooldown_end" ]; then
            remaining=$(( cooldown_end - now ))
            if [ $(( remaining % 300 )) -lt "$CHECK_INTERVAL" ]; then
                log "COOLDOWN: Waiting ${remaining}s before next restart attempt"
            fi
            sleep "$CHECK_INTERVAL"
            continue
        else
            rm -f "$COOLDOWN_FILE"
            set_fail_count 0
            log "INFO: Cooldown expired, resuming normal monitoring"
        fi
    fi

    # Update message activity timestamp
    update_last_msg_ts

    # Check service is running
    if ! systemctl is-active --quiet "$NAPCAT_SERVICE"; then
        log "ALERT: $NAPCAT_SERVICE is DOWN"
        send_mail_alert "SERVICE_DOWN" "🔴 QQ Bot 服务宕机" "$NAPCAT_SERVICE 已停止运行\n正在尝试自动恢复...\n时间: $(date +%H:%M:%S)"
        do_restart
        sleep "$CHECK_INTERVAL"
        continue
    fi

    # Check actual QQ login status
    status=$(check_qq_login_status)
    if [ "$status" = "OK" ]; then
        old_count=$(get_fail_count)
        if [ "$old_count" -gt 0 ]; then
            log "INFO: QQ login restored to OK (was failing for $old_count cycles)"
        fi
        set_fail_count 0
        sleep "$CHECK_INTERVAL"
        continue
    fi

    # Risk control detected → longest cooldown, NEVER auto-restart
    if [ "$status" = "RISK_CONTROL" ]; then
        log "PROBLEM: QQ triggered risk control — manual intervention needed, will NOT auto-restart"
        enter_cooldown "$RISK_CONTROL_COOLDOWN" "$status"
        set_fail_count 0
        sleep "$CHECK_INTERVAL"
        continue
    fi

    # Captcha or new device verification → long cooldown, do NOT restart
    if [ "$status" = "CAPTCHA_REQUIRED" ] || [ "$status" = "NEW_DEVICE_VERIFY" ]; then
        log "PROBLEM: QQ login status=$status — manual verification needed, will NOT auto-restart"
        enter_cooldown "$CAPTCHA_COOLDOWN" "$status"
        set_fail_count 0
        sleep "$CHECK_INTERVAL"
        continue
    fi

    # Login problem detected (LOGIN_ERROR / OFFLINE)
    consecutive_failures=$(get_fail_count)
    consecutive_failures=$((consecutive_failures + 1))
    set_fail_count "$consecutive_failures"
    log "PROBLEM: QQ login status=$status (failures=$consecutive_failures/$MAX_RESTART_ATTEMPTS)"

    if [ "$consecutive_failures" -ge "$MAX_RESTART_ATTEMPTS" ]; then
        enter_cooldown "$RESTART_COOLDOWN" "Max restart attempts reached ($MAX_RESTART_ATTEMPTS). Manual intervention likely required"
        sleep "$CHECK_INTERVAL"
        continue
    fi

    do_restart
    sleep "$CHECK_INTERVAL"
done
