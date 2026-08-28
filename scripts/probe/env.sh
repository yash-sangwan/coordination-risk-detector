# Safe loader: parses .env without echoing values. Source this.
_envfile="${ENVFILE:-.env}"
_get(){ sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$_envfile" | tr -d '\r' | sed 's/^"//; s/"$//' | head -1; }
RZP_ID="$(_get 'Test API Key')";   [ -z "$RZP_ID" ]     && RZP_ID="$(_get 'RAZORPAY_KEY_ID')"
RZP_SECRET="$(_get 'Test Key Secret')"; [ -z "$RZP_SECRET" ] && RZP_SECRET="$(_get 'RAZORPAY_KEY_SECRET')"
export RZP_ID RZP_SECRET
