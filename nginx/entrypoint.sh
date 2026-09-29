#!/bin/sh
set -e

SMITE_HTTP_PORT=${SMITE_HTTP_PORT:-80}
SMITE_HTTPS_PORT=${SMITE_HTTPS_PORT:-443}
SMITE_SSL_DOMAIN=${SMITE_SSL_DOMAIN:-REPLACE_DOMAIN}
PANEL_PORT=${PANEL_PORT:-8000}

# fallback to $PANEL_PORT if upstream override not set
if [ -z "$SMITE_PANEL_UPSTREAM" ]; then
  SMITE_PANEL_UPSTREAM="http://127.0.0.1:${PANEL_PORT}"
fi

if [ "$SMITE_HTTPS_PORT" = "443" ]; then
  SMITE_HTTPS_REDIRECT_SUFFIX=""
else
  SMITE_HTTPS_REDIRECT_SUFFIX=":$SMITE_HTTPS_PORT"
fi

# Locate SSL Certificates with graceful bootstrap fallback
LE_CERT="/etc/letsencrypt/live/${SMITE_SSL_DOMAIN}/fullchain.pem"
LE_KEY="/etc/letsencrypt/live/${SMITE_SSL_DOMAIN}/privkey.pem"
CUSTOM_CERT="/etc/ssl/smite/server.crt"
CUSTOM_KEY="/etc/ssl/smite/server.key"
BOOTSTRAP_DIR="/tmp/smite-bootstrap-ssl"
BOOTSTRAP_CERT="${BOOTSTRAP_DIR}/server.crt"
BOOTSTRAP_KEY="${BOOTSTRAP_DIR}/server.key"

if [ -f "$LE_CERT" ] && [ -f "$LE_KEY" ]; then
  SMITE_CERT_FILE="$LE_CERT"
  SMITE_KEY_FILE="$LE_KEY"
elif [ -f "$CUSTOM_CERT" ] && [ -f "$CUSTOM_KEY" ]; then
  SMITE_CERT_FILE="$CUSTOM_CERT"
  SMITE_KEY_FILE="$CUSTOM_KEY"
else
  mkdir -p "$BOOTSTRAP_DIR"
  if [ ! -f "$BOOTSTRAP_CERT" ] || [ ! -f "$BOOTSTRAP_KEY" ]; then
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
      -keyout "$BOOTSTRAP_KEY" -out "$BOOTSTRAP_CERT" \
      -subj "/CN=smite-bootstrap" >/dev/null 2>&1 || true
  fi
  SMITE_CERT_FILE="$BOOTSTRAP_CERT"
  SMITE_KEY_FILE="$BOOTSTRAP_KEY"
fi

export SMITE_HTTP_PORT
export SMITE_HTTPS_PORT
export SMITE_PANEL_UPSTREAM
export SMITE_SSL_DOMAIN
export SMITE_HTTPS_REDIRECT_SUFFIX
export SMITE_CERT_FILE
export SMITE_KEY_FILE

TEMPLATE_PATH="/etc/nginx/templates/default.conf.template"
TARGET_PATH="/etc/nginx/conf.d/default.conf"

mkdir -p "$(dirname "$TARGET_PATH")"

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "Missing nginx template at $TEMPLATE_PATH" >&2
  exit 1
fi

# substitute placeholders with actual values, but leave literal tokens for the base entrypoint
envsubst '$SMITE_HTTP_PORT $SMITE_HTTPS_PORT $SMITE_HTTPS_REDIRECT_SUFFIX $SMITE_PANEL_UPSTREAM $SMITE_SSL_DOMAIN $SMITE_CERT_FILE $SMITE_KEY_FILE' < "$TEMPLATE_PATH" > "$TARGET_PATH"

exec nginx -g 'daemon off;'
