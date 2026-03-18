#!/usr/bin/env bash
# init-ca.sh — Initialize a private Certificate Authority for AROC MQTT mTLS.
#
# Usage: ./init-ca.sh [CA_DIR]
#   CA_DIR defaults to ./ca
#
# Creates:
#   CA_DIR/ca.key     — EC P-256 private key (chmod 600)
#   CA_DIR/ca.crt     — Self-signed CA cert (10-year validity)
#   CA_DIR/serial      — OpenSSL serial file
#   CA_DIR/index.txt   — OpenSSL index (for revocation)
#   CA_DIR/crl/        — CRL output directory
#
# The CA key must be kept offline (air-gapped USB or vault).
# Only ca.crt is distributed to robots and brokers.

set -euo pipefail

CA_DIR="${1:-./ca}"
CA_DAYS=3650
CA_SUBJECT="/C=DE/O=AROC/CN=AROC MQTT CA"

if [ -f "$CA_DIR/ca.key" ]; then
    echo "ERROR: CA already exists at $CA_DIR/ca.key — refusing to overwrite."
    echo "Delete $CA_DIR manually if you want to reinitialize."
    exit 1
fi

mkdir -p "$CA_DIR/crl" "$CA_DIR/newcerts"
touch "$CA_DIR/index.txt"
echo "01" > "$CA_DIR/serial"
echo "01" > "$CA_DIR/crlnumber"

# Generate EC P-256 private key
openssl ecparam -genkey -name prime256v1 -noout -out "$CA_DIR/ca.key"
chmod 600 "$CA_DIR/ca.key"

# Self-signed CA certificate
openssl req -new -x509 \
    -key "$CA_DIR/ca.key" \
    -out "$CA_DIR/ca.crt" \
    -days "$CA_DAYS" \
    -subj "$CA_SUBJECT" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"

# Generate initial empty CRL
cat > "$CA_DIR/openssl.cnf" << 'CONF'
[ ca ]
default_ca = CA_default

[ CA_default ]
dir               = CA_DIR_PLACEHOLDER
database          = $dir/index.txt
serial            = $dir/serial
crlnumber         = $dir/crlnumber
new_certs_dir     = $dir/newcerts
certificate       = $dir/ca.crt
private_key       = $dir/ca.key
default_md        = sha256
default_days      = 365
default_crl_days  = 30
policy            = policy_loose
unique_subject    = no

[ policy_loose ]
countryName            = optional
stateOrProvinceName    = optional
organizationName       = optional
commonName             = supplied
CONF

# Replace placeholder with actual path (portable sed)
sed -i "s|CA_DIR_PLACEHOLDER|$(cd "$CA_DIR" && pwd)|g" "$CA_DIR/openssl.cnf"

openssl ca -gencrl \
    -config "$CA_DIR/openssl.cnf" \
    -out "$CA_DIR/crl/ca.crl" \
    -batch 2>/dev/null || true

echo "CA initialized at $CA_DIR"
echo "  ca.crt  — distribute to robots and brokers"
echo "  ca.key  — KEEP OFFLINE (never on robots!)"
openssl x509 -in "$CA_DIR/ca.crt" -noout -subject -dates
