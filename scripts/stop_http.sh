#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$project_dir/.venv/bin/activate"
pid_file="$project_dir/.tmp/stock-http.pid"
host="${STOCK_HTTP_HOST:-127.0.0.1}"
port="${STOCK_HTTP_PORT:-8765}"

is_stock_server_pid() {
    local candidate_pid="$1"
    if ps -p "$candidate_pid" -o command= 2>/dev/null | grep -F "stock_http_server.py" >/dev/null; then
        return 0
    fi
    lsof -a -p "$candidate_pid" -d cwd 2>/dev/null | grep -F "$project_dir" >/dev/null \
        && lsof -a -p "$candidate_pid" 2>/dev/null | grep -F "$project_dir/.tmp/stock-http.log" >/dev/null
}

if [[ ! -f "$pid_file" ]]; then
    pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -z "$pid" ]]; then
        echo "stock-analysis HTTP service is not running"
        exit 0
    fi
    if ! is_stock_server_pid "$pid"; then
        echo "PID $pid is listening on $host:$port but is not stock_http_server.py; refusing to stop it" >&2
        exit 1
    fi
    echo "$pid" >"$pid_file"
fi

pid="$(<"$pid_file")"
if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    echo "stock-analysis HTTP service is not running"
    exit 0
fi

if ! is_stock_server_pid "$pid"; then
    echo "PID file does not refer to stock_http_server.py; refusing to stop PID $pid" >&2
    exit 1
fi

kill "$pid"
for _ in {1..25}; do
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pid_file"
        echo "stock-analysis HTTP service stopped"
        exit 0
    fi
    sleep 0.2
done

echo "service did not stop within 5 seconds; PID file preserved" >&2
exit 1
