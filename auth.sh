#!/usr/bin/env bash

SIDECAR="http://localhost:8081"

print_json() {
  if [ -z "$1" ]; then
    echo "(empty response — sidecar may be unavailable)"
  else
    echo "$1" | python3 -m json.tool 2>/dev/null || echo "$1"
  fi
}

echo "==> Sending OTP to your Telegram account..."
send_resp=$(curl -s -X POST "$SIDECAR/auth/send-code" \
  -H "Content-Type: application/json" \
  -d '{}')
print_json "$send_resp"

# Check for flood wait / error
if echo "$send_resp" | grep -qi "flood\|error\|detail\|fail"; then
  echo ""
  echo "Note: If Telegram rate-limited the request, use the code you already received."
fi

echo ""
read -rp "Enter the OTP code you received: " code

echo ""
echo "==> Verifying OTP..."
verify_resp=$(curl -s -X POST "$SIDECAR/auth/verify-code" \
  -H "Content-Type: application/json" \
  -d "{\"code\": \"$code\"}")
print_json "$verify_resp"

# Handle 2FA
if echo "$verify_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('needs_2fa') else 1)" 2>/dev/null; then
  echo ""
  read -rsp "2FA is enabled. Enter your password: " password
  echo ""
  echo "==> Verifying 2FA..."
  twofa_resp=$(curl -s -X POST "$SIDECAR/auth/verify-2fa" \
    -H "Content-Type: application/json" \
    -d "{\"password\": \"$password\"}")
  print_json "$twofa_resp"
fi

echo ""
echo "==> Checking authentication status..."
health=$(curl -s "$SIDECAR/health")
print_json "$health"

if echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('authorized') else 1)" 2>/dev/null; then
  echo ""
  echo "✅ Authentication successful! Your bot is ready to use."
else
  echo ""
  echo "❌ Not authorized yet. Check the response above."
fi
