#!/bin/bash
set -e

# =============================================================================
# Network Connectivity Health Check
# Runs before the application starts to diagnose DNS / TCP issues
# with the remote database (e.g. Supabase on WSL2/Docker).
# =============================================================================

echo ""
echo "=========================================="
echo "  Network Connectivity Diagnostics"
echo "=========================================="

# Extract DB host and port from DATABASE_URL using shell parameter expansion
# Supports both postgresql://user:pass@host:port/db and postgresql+asyncpg://
DB_URL="${DATABASE_URL:-}"
if [ -z "$DB_URL" ]; then
    echo "  [WARN] DATABASE_URL is not set — skipping network checks"
else
    # Strip scheme
    DB_HOST="${DB_URL#*@}"
    DB_HOST="${DB_HOST%%:*}"
    # Handle case where URL might not have @ (no credentials)
    if [ "$DB_HOST" = "$DB_URL" ]; then
        DB_HOST="${DB_URL#*://}"
        DB_HOST="${DB_HOST%%:*}"
    fi

    DB_PORT="${DB_URL##*:}"
    DB_PORT="${DB_PORT%%/*}"
    # Validate port is a number; default to 5432
    if ! [[ "$DB_PORT" =~ ^[0-9]+$ ]]; then
        DB_PORT=5432
    fi

    echo "  Target:     $DB_HOST:$DB_PORT"
    echo ""

    # ---------------------------------------------------------------
    # 1. DNS Resolution Test
    # ---------------------------------------------------------------
    echo "  [1/3] DNS Resolution..."
    DNS_RESOLVED=""
    if command -v nslookup &> /dev/null; then
        DNS_OUTPUT=$(nslookup "$DB_HOST" 2>&1 || true)
        echo "    nslookup: $(echo "$DNS_OUTPUT" | grep -i "address" | head -5 | tr '\n' '; ')"
        if echo "$DNS_OUTPUT" | grep -qi "can't find\|NXDOMAIN\|SERVFAIL\|server failed\|connection timed out\|no response"; then
            echo "    [WARN] nslookup reported an issue"
            DNS_RESOLVED="FAIL"
        else
            DNS_RESOLVED="OK"
        fi
    fi

    if command -v getent &> /dev/null; then
        GETENT_OUTPUT=$(getent hosts "$DB_HOST" 2>&1 || true)
        echo "    getent:   $GETENT_OUTPUT"
        if [ -n "$GETENT_OUTPUT" ] && ! echo "$GETENT_OUTPUT" | grep -qi "not found\|unknown host"; then
            DNS_RESOLVED="OK"
        elif [ "$DNS_RESOLVED" != "OK" ]; then
            DNS_RESOLVED="FAIL"
        fi
    fi

    if command -v host &> /dev/null; then
        HOST_OUTPUT=$(host "$DB_HOST" 2>&1 || true)
        echo "    host:     $(echo "$HOST_OUTPUT" | head -3 | tr '\n' '; ')"
        if echo "$HOST_OUTPUT" | grep -qi "has address\|has IPv6"; then
            DNS_RESOLVED="OK"
        elif [ "$DNS_RESOLVED" != "OK" ]; then
            DNS_RESOLVED="FAIL"
        fi
    fi

    if command -v dig &> /dev/null; then
        DIG_OUTPUT=$(dig "$DB_HOST" +short 2>&1 || true)
        echo "    dig:      $DIG_OUTPUT"
        if [ -n "$DIG_OUTPUT" ] && ! echo "$DIG_OUTPUT" | grep -qi "connection timed out\|no servers could be reached"; then
            DNS_RESOLVED="OK"
        elif [ "$DNS_RESOLVED" != "OK" ]; then
            DNS_RESOLVED="FAIL"
        fi
    fi

    if [ "$DNS_RESOLVED" = "FAIL" ]; then
        echo "    [WARN] DNS resolution failed on all tools — container may not resolve external hostnames."
        echo "    This is often a WSL2/Docker DNS issue. The app will still attempt to connect."
    else
        echo "    [OK] DNS resolution succeeded"
    fi
    echo ""

    # ---------------------------------------------------------------
    # 2. TCP Connectivity Test
    # ---------------------------------------------------------------
    echo "  [2/3] TCP Connectivity..."
    TCP_OK=false
    if command -v nc &> /dev/null; then
    if nc -zv -w 10 "$DB_HOST" "$DB_PORT" 2>&1; then
        echo "    [OK] nc — $DB_HOST:$DB_PORT is reachable"
        TCP_OK=true
    else
        echo "    [WARN] nc — $DB_HOST:$DB_PORT is NOT reachable (timeout or refused)"
    fi || true
    fi

    if command -v curl &> /dev/null; then
        # Use curl to test both TCP and SSL handshake
        CURL_EXIT=0
        CURL_OUTPUT=$(curl -v --connect-timeout 10 --max-time 15 \
            "postgresql://$DB_HOST:$DB_PORT" 2>&1) || CURL_EXIT=$?
        if echo "$CURL_OUTPUT" | grep -qi "Connected to\|SSL connection\|TLS handshake\|200 OK\|Recv failure: Connection reset"; then
            echo "    curl connect result: $(echo "$CURL_OUTPUT" | grep -i "Connected to\|SSL connection\|TLS\|error:" | head -3 | tr '\n' '; ')"
        fi
        if [ $CURL_EXIT -eq 0 ]; then
            echo "    [OK] curl — connection succeeded"
            TCP_OK=true
        else
            echo "    curl — connection test completed (exit code: $CURL_EXIT)"
        fi
    fi

    if [ "$TCP_OK" = true ]; then
        echo "    [OK] TCP connectivity to $DB_HOST:$DB_PORT confirmed"
    else
        echo "    [WARN] TCP connectivity to $DB_HOST:$DB_PORT could not be confirmed."
        echo "    The app will attempt to connect anyway."
    fi
    echo ""

    # ---------------------------------------------------------------
    # 3. Network Config Summary
    # ---------------------------------------------------------------
    echo "  [3/3] Network Configuration..."
    echo "    Default route:"
    ip route show default 2>/dev/null || route -n 2>/dev/null || netstat -rn 2>/dev/null || echo "    (not available)"
    echo "    DNS config:"
    cat /etc/resolv.conf 2>/dev/null | grep -v "^#" | grep -v "^$" | sed 's/^/    /' || echo "    (not available)"
    echo ""
fi

echo "=========================================="
echo "  Starting application..."
echo "=========================================="
echo ""

# ── Run database migrations before starting ──
echo "Running database migrations..."
alembic upgrade head
if [ $? -ne 0 ]; then
    echo "ERROR: Database migration failed. Exiting."
    exit 1
fi
echo "Migrations complete."
echo ""

# Execute the main command (e.g. uvicorn)
exec "$@"
