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

if ! command -v uv >/dev/null 2>&1; then
    printf 'Error: uv is required but was not found in PATH.\n' >&2
    exit 127
fi

# Keep reports relative to the directory from which the script was invoked.
# The CLI changes into the project directory below, so capture this first.
invocation_dir="$(pwd -P)"
export TRADINGAGENTS_RESULTS_DIR="${TRADINGAGENTS_RESULTS_DIR:-$invocation_dir}"
export https_proxy=http://127.0.0.1:7890 \
    http_proxy=http://127.0.0.1:7890 \
    all_proxy=socks5://127.0.0.1:7890

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ticker="$1"
analysis_date="${2:-$(date +%F)}"
analysts="${3:-market,news,fundamentals,sentiment}"

if [[ ! "$analysis_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    printf 'Error: DATE must use YYYY-MM-DD format.\n' >&2
    exit 2
fi

cd "$project_dir"
exec uv run --project "$project_dir" tradingagents \
    --ticker "$ticker" \
    --date "$analysis_date" \
    --analysts "$analysts" \
    --save \
    --no-show
