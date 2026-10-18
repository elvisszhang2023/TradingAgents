#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$project_dir/.venv/bin/activate"
runtime_dir="$project_dir/.tmp"
pid_file="$runtime_dir/stock-http.pid"
log_file="$runtime_dir/stock-http.log"
host="${STOCK_HTTP_HOST:-127.0.0.1}"
port="${STOCK_HTTP_PORT:-8765}"

mkdir -p "$runtime_dir"
if [[ -f "$pid_file" ]]; then
    pid="$(<"$pid_file")"
    if kill -0 "$pid" 2>/dev/null && ps -p "$pid" -o command= | grep -F "stock_http_server.py" >/dev/null; then
        echo "stock-analysis HTTP service is already running (PID $pid)"
        exit 0
    fi
    rm -f "$pid_file"
fi

cd "$project_dir"
nohup python3 scripts/stock_http_server.py --host "$host" --port "$port" \
    >>"$log_file" 2>&1 &
pid="$!"
echo "$pid" >"$pid_file"

for _ in {1..30}; do
    if curl --noproxy '*' --silent --show-error --fail --max-time 1 "http://$host:$port/health" >/dev/null; then
        echo "stock-analysis HTTP service started on http://$host:$port (PID $pid)"
        exit 0
    fi
    sleep 0.2
done

echo "failed to start stock-analysis HTTP service; see $log_file" >&2
exit 1
