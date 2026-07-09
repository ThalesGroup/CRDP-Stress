#!/usr/bin/env bash
# build_report.sh — convert the Markdown report to DOCX with pandoc (reporting host).
# PNG charts embed automatically via the Markdown image links given --resource-path.
#
# Usage: bash build_report.sh <results_dir>   (default: ./results)
set -euo pipefail
RESULTS="${1:-results}"
MD="$RESULTS/report.md"
OUT="$RESULTS/CRDP_Throughput_Report.docx"
REF="$(dirname "$0")/reference.docx"

[ -f "$MD" ] || { echo "ERROR: $MD not found (run gen_report.py first)" >&2; exit 1; }

ARGS=(--from=gfm --to=docx --toc --toc-depth=2 --resource-path="$RESULTS:$RESULTS/charts")
[ -f "$REF" ] && ARGS+=(--reference-doc="$REF")

pandoc "$MD" "${ARGS[@]}" -o "$OUT"
echo "wrote $OUT"
