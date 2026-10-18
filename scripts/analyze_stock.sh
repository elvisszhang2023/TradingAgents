#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/analyze_stock.sh TICKER [DATE] [ANALYSTS]

Examples:
  scripts/analyze_stock.sh TSLA
  scripts/analyze_stock.sh 0700.HK 2026-10-16
  scripts/analyze_stock.sh NBIS 2026-10-16 market,news,fundamentals

DATE defaults to today. ANALYSTS defaults to
market,news,fundamentals,sentiment.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if (( $# < 1 || $# > 3 )); then
    usage >&2
    exit 2
fi

if ! command -v tradingagents >/dev/null 2>&1; then
    printf 'Error: tradingagents is not installed in the active virtual environment.\n' >&2
    exit 127
fi

# Keep reports relative to the directory from which the script was invoked.
# The CLI changes into the project directory below, so capture this first.
invocation_dir="$(pwd -P)"
export TRADINGAGENTS_RESULTS_DIR="${TRADINGAGENTS_RESULTS_DIR:-$invocation_dir}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export https_proxy=http://127.0.0.1:7890 \
    http_proxy=http://127.0.0.1:7890 \
    HTTP_PROXY=http://127.0.0.1:7890 \
    HTTPS_PROXY=http://127.0.0.1:7890 \
    ALL_PROXY=http://127.0.0.1:7890

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ticker="$1"
analysis_date="${2:-$(date +%F)}"
analysts="${3:-market,news,fundamentals,sentiment}"
runtime_dir="$project_dir/.tmp"
mkdir -p "$runtime_dir"
export TRADINGAGENTS_CACHE_DIR="${TRADINGAGENTS_CACHE_DIR:-$runtime_dir/cache}"
export TRADINGAGENTS_MEMORY_LOG_PATH="${TRADINGAGENTS_MEMORY_LOG_PATH:-$runtime_dir/trading_memory.md}"

if [[ ! "$analysis_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    printf 'Error: DATE must use YYYY-MM-DD format.\n' >&2
    exit 2
fi

cd "$project_dir"
exec tradingagents \
    --ticker "$ticker" \
    --date "$analysis_date" \
    --analysts "$analysts" \
    --save \
    --no-show
