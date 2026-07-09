#!/usr/bin/env bash
# build_report.sh — convert the Markdown report to DOCX with pandoc (reporting host).
# Runs pandoc from INSIDE the results dir so the relative charts/*.png image links
# resolve directly (portable across OSes / pandoc path-separator quirks).
#
# Usage: bash build_report.sh <results_dir>   (default: results)
set -euo pipefail
RESULTS="${1:-results}"
REF="$(cd "$(dirname "$0")" && pwd)/reference.docx"
OUT="CRDP_Throughput_Report.docx"

[ -f "$RESULTS/report.md" ] || { echo "ERROR: $RESULTS/report.md not found (run gen_report.py first)" >&2; exit 1; }

cd "$RESULTS"
ARGS=(--from=gfm --to=docx --toc --toc-depth=2)
[ -f "$REF" ] && ARGS+=(--reference-doc="$REF")

pandoc report.md "${ARGS[@]}" -o "$OUT"
echo "wrote $RESULTS/$OUT"
