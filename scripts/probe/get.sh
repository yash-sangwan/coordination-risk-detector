#!/bin/sh
# usage: get.sh <path> [label]
. "$(dirname "$0")/env.sh"
p="$1"; lbl="${2:-$1}"
out=$(curl -s -u "$RZP_ID:$RZP_SECRET" -w "\n__HTTP__%{http_code}" "https://api.razorpay.com$p")
code=$(printf '%s' "$out" | sed -n 's/.*__HTTP__//p')
body=$(printf '%s' "$out" | sed 's/\n*__HTTP__[0-9]*$//' | sed '$ s/__HTTP__[0-9]*$//')
echo "───────────────────────────────────────────"
echo "GET $p   ->  HTTP $code"
echo "$body" | head -c 1200
echo
