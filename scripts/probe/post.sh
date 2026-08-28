#!/bin/sh
# usage: post.sh <path> <json>
. "$(dirname "$0")/env.sh"
p="$1"; body="$2"
out=$(curl -s -u "$RZP_ID:$RZP_SECRET" -H "Content-Type: application/json" -X POST -d "$body" -w "\n__HTTP__%{http_code}" "https://api.razorpay.com$p")
code=$(printf '%s' "$out" | sed -n 's/.*__HTTP__//p')
echo "POST $p  ->  HTTP $code"
printf '%s' "$out" | sed 's/__HTTP__[0-9]*$//'
echo
