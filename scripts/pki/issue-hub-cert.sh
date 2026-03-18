#!/usr/bin/env bash
# issue-hub-cert.sh — Issue a client certificate for the hub (cloud controller).
#
# Usage: ./issue-hub-cert.sh [CA_DIR] [OUT_DIR]
#   CA_DIR defaults to ./ca
#   OUT_DIR defaults to ./certs/hub-control
#
# The hub cert has CN=hub-control and is authorized for all robot topics
# via broker ACL: user hub-control → topic readwrite aroc/robot/#

set -euo pipefail

CA_DIR="${1:-./ca}"
OUT_DIR="${2:-./certs/hub-control}"
CN="hub-control"
CERT_DAYS=365

if [ ! -f "$CA_DIR/ca.key" ] || [ ! -f "$CA_DIR/ca.crt" ]; then
    echo "ERROR: CA not found at $CA_DIR — run init-ca.sh first"
    exit 1
fi

mkdir -p "$OUT_DIR"

if [ -f "$OUT_DIR/$CN.crt" ]; then
    echo "WARNING: Hub certificate already exists at $OUT_DIR/$CN.crt"
    exit 1
fi

openssl ecparam -genkey -name prime256v1 -noout -out "$OUT_DIR/$CN.key"
chmod 600 "$OUT_DIR/$CN.key"

openssl req -new \
    -key "$OUT_DIR/$CN.key" \
    -out "$OUT_DIR/$CN.csr" \
    -subj "/CN=$CN/O=AROC Hub"

cat > "$OUT_DIR/extensions.cnf" << EOF
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = DNS:$CN
EOF

openssl x509 -req \
    -in "$OUT_DIR/$CN.csr" \
    -CA "$CA_DIR/ca.crt" \
    -CAkey "$CA_DIR/ca.key" \
    -CAcreateserial \
    -out "$OUT_DIR/$CN.crt" \
    -days "$CERT_DAYS" \
    -sha256 \
    -extfile "$OUT_DIR/extensions.cnf"

cp "$CA_DIR/ca.crt" "$OUT_DIR/ca.crt"
rm -f "$OUT_DIR/$CN.csr" "$OUT_DIR/extensions.cnf"

echo "Hub certificate issued:"
echo "  $OUT_DIR/$CN.crt  (client cert, CN=$CN)"
echo "  $OUT_DIR/$CN.key  (private key)"
echo "  $OUT_DIR/ca.crt   (CA cert)"
openssl x509 -in "$OUT_DIR/$CN.crt" -noout -subject -dates -serial
