#!/usr/bin/env bash
# rotate-cert.sh — Issue a new certificate for an existing robot (same CN, new serial).
#
# Usage: ./rotate-cert.sh <robot_id> [CA_DIR] [OUT_DIR]
#
# The old cert is backed up as <robot_id>.crt.bak before replacement.
# Both old and new certs are valid until the old one is explicitly revoked.

set -euo pipefail

ROBOT_ID="${1:?Usage: $0 <robot_id> [CA_DIR] [OUT_DIR]}"
CA_DIR="${2:-./ca}"
OUT_DIR="${3:-./certs/$ROBOT_ID}"
CERT_DAYS=365

if ! echo "$ROBOT_ID" | grep -qE '^[a-zA-Z0-9._-]+$'; then
    echo "ERROR: Invalid robot_id '$ROBOT_ID'"
    exit 1
fi

if [ ! -f "$CA_DIR/ca.key" ]; then
    echo "ERROR: CA not found at $CA_DIR"
    exit 1
fi

# Backup existing cert if present
if [ -f "$OUT_DIR/$ROBOT_ID.crt" ]; then
    cp "$OUT_DIR/$ROBOT_ID.crt" "$OUT_DIR/$ROBOT_ID.crt.bak"
    echo "Old cert backed up to $OUT_DIR/$ROBOT_ID.crt.bak"
fi

# Generate new key + cert (same CN)
openssl ecparam -genkey -name prime256v1 -noout -out "$OUT_DIR/$ROBOT_ID.key.new"
chmod 600 "$OUT_DIR/$ROBOT_ID.key.new"

openssl req -new \
    -key "$OUT_DIR/$ROBOT_ID.key.new" \
    -out "$OUT_DIR/$ROBOT_ID.csr" \
    -subj "/CN=$ROBOT_ID/O=AROC Robot"

cat > "$OUT_DIR/extensions.cnf" << EOF
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = DNS:$ROBOT_ID
EOF

openssl x509 -req \
    -in "$OUT_DIR/$ROBOT_ID.csr" \
    -CA "$CA_DIR/ca.crt" \
    -CAkey "$CA_DIR/ca.key" \
    -CAcreateserial \
    -out "$OUT_DIR/$ROBOT_ID.crt.new" \
    -days "$CERT_DAYS" \
    -sha256 \
    -extfile "$OUT_DIR/extensions.cnf"

# Atomic swap
mv "$OUT_DIR/$ROBOT_ID.key.new" "$OUT_DIR/$ROBOT_ID.key"
mv "$OUT_DIR/$ROBOT_ID.crt.new" "$OUT_DIR/$ROBOT_ID.crt"

rm -f "$OUT_DIR/$ROBOT_ID.csr" "$OUT_DIR/extensions.cnf"

echo "Certificate rotated for $ROBOT_ID:"
openssl x509 -in "$OUT_DIR/$ROBOT_ID.crt" -noout -subject -dates -serial
echo ""
echo "Deploy to robot and restart mqtt-bridge + mqtt-telemetry."
echo "Optionally revoke the old cert: ./revoke-cert.sh $OUT_DIR/$ROBOT_ID.crt.bak"
