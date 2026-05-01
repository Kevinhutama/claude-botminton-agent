#!/usr/bin/env bash
set -e

SIDECAR="http://localhost:8081"

echo "Sending OTP to your Telegram account..."
curl -s -X POST "$SIDECAR/auth/send-code" \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool

read -rp $'\nEnter the OTP code you received: ' code

response=$(curl -s -X POST "$SIDECAR/auth/verify-code" \
  -H "Content-Type: application/json" \
  -d "{\"code\": \"$code\"}")

echo "$response" | python3 -m json.tool

if echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('needs_2fa') else 1)" 2>/dev/null; then
  read -rsp $'\n2FA is enabled. Enter your password: ' password
  echo
  curl -s -X POST "$SIDECAR/auth/verify-2fa" \
    -H "Content-Type: application/json" \
    -d "{\"password\": \"$password\"}" | python3 -m json.tool
fi

echo -e "\nVerifying authentication..."
curl -s "$SIDECAR/health" | python3 -m json.tool
