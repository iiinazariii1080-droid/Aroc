#!/usr/bin/env bash
# revoke-cert.sh — Revoke a certificate and regenerate the CRL.
#
# Usage: ./revoke-cert.sh <cert_file> [CA_DIR]
#   cert_file: Path to the PEM certificate to revoke
#   CA_DIR defaults to ./ca
#
# After revocation, deploy the updated CRL to the broker:
#   scp CA_DIR/crl/ca.crl broker:/etc/mosquitto/certs/ca.crl

set -euo pipefail

CERT_FILE="${1:?Usage: $0 <cert_file> [CA_DIR]}"
CA_DIR="${2:-./ca}"

if [ ! -f "$CERT_FILE" ]; then
    echo "ERROR: Certificate file not found: $CERT_FILE"
    exit 1
fi

if [ ! -f "$CA_DIR/openssl.cnf" ]; then
    echo "ERROR: CA config not found at $CA_DIR/openssl.cnf — run init-ca.sh first"
    exit 1
fi

echo "Revoking certificate:"
openssl x509 -in "$CERT_FILE" -noout -subject -serial

openssl ca \
    -config "$CA_DIR/openssl.cnf" \
    -revoke "$CERT_FILE" \
    -batch

# Regenerate CRL
openssl ca -gencrl \
    -config "$CA_DIR/openssl.cnf" \
    -out "$CA_DIR/crl/ca.crl"

echo ""
echo "Certificate revoked. Updated CRL at: $CA_DIR/crl/ca.crl"
echo "Deploy to broker: scp $CA_DIR/crl/ca.crl broker:/etc/mosquitto/certs/ca.crl"
echo "Then reload broker: mosquitto -s reload (or restart)"
