#!/usr/bin/env bash
# issue-robot-cert.sh — Issue a client certificate for a robot.
#
# Usage: ./issue-robot-cert.sh <robot_id> [CA_DIR] [OUT_DIR]
#   robot_id: Must match ^[a-zA-Z0-9._-]+$ (same as validate_robot_id)
#   CA_DIR defaults to ./ca
#   OUT_DIR defaults to ./certs/<robot_id>
#
# Creates:
#   OUT_DIR/<robot_id>.key  — EC P-256 private key
#   OUT_DIR/<robot_id>.crt  — Client cert signed by CA (CN=robot_id, 1-year)
#   OUT_DIR/ca.crt          — Copy of CA cert (for convenience)
#
# For deployment, copy as:
#   <robot_id>.crt → /certs/client.crt
#   <robot_id>.key → /certs/client.key
#   ca.crt         → /certs/ca.crt

set -euo pipefail

ROBOT_ID="${1:?Usage: $0 <robot_id> [CA_DIR] [OUT_DIR]}"
CA_DIR="${2:-./ca}"
OUT_DIR="${3:-./certs/$ROBOT_ID}"
CERT_DAYS=365

# Validate robot_id format
if ! echo "$ROBOT_ID" | grep -qE '^[a-zA-Z0-9._-]+$'; then
    echo "ERROR: Invalid robot_id '$ROBOT_ID' — must match ^[a-zA-Z0-9._-]+$"
    exit 1
fi

# Verify CA exists
if [ ! -f "$CA_DIR/ca.key" ] || [ ! -f "$CA_DIR/ca.crt" ]; then
    echo "ERROR: CA not found at $CA_DIR — run init-ca.sh first"
    exit 1
fi

mkdir -p "$OUT_DIR"

if [ -f "$OUT_DIR/$ROBOT_ID.crt" ]; then
    echo "WARNING: Certificate already exists at $OUT_DIR/$ROBOT_ID.crt"
    echo "Use rotate-cert.sh to issue a replacement."
    exit 1
fi

# Generate EC P-256 private key
openssl ecparam -genkey -name prime256v1 -noout -out "$OUT_DIR/$ROBOT_ID.key"
chmod 600 "$OUT_DIR/$ROBOT_ID.key"

# Create CSR
openssl req -new \
    -key "$OUT_DIR/$ROBOT_ID.key" \
    -out "$OUT_DIR/$ROBOT_ID.csr" \
    -subj "/CN=$ROBOT_ID/O=AROC Robot"

# Create extensions file for SAN
cat > "$OUT_DIR/extensions.cnf" << EOF
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = DNS:$ROBOT_ID
EOF

# Sign with CA
openssl x509 -req \
    -in "$OUT_DIR/$ROBOT_ID.csr" \
    -CA "$CA_DIR/ca.crt" \
    -CAkey "$CA_DIR/ca.key" \
    -CAcreateserial \
    -out "$OUT_DIR/$ROBOT_ID.crt" \
    -days "$CERT_DAYS" \
    -sha256 \
    -extfile "$OUT_DIR/extensions.cnf"

# Copy CA cert for convenience
cp "$CA_DIR/ca.crt" "$OUT_DIR/ca.crt"

# Cleanup temp files
rm -f "$OUT_DIR/$ROBOT_ID.csr" "$OUT_DIR/extensions.cnf"

echo "Robot certificate issued:"
echo "  $OUT_DIR/$ROBOT_ID.crt  (client cert, CN=$ROBOT_ID)"
echo "  $OUT_DIR/$ROBOT_ID.key  (private key)"
echo "  $OUT_DIR/ca.crt         (CA cert)"
echo ""
echo "Deploy to robot as:"
echo "  cp $OUT_DIR/$ROBOT_ID.crt /certs/client.crt"
echo "  cp $OUT_DIR/$ROBOT_ID.key /certs/client.key"
echo "  cp $OUT_DIR/ca.crt        /certs/ca.crt"
echo ""
openssl x509 -in "$OUT_DIR/$ROBOT_ID.crt" -noout -subject -dates -serial
