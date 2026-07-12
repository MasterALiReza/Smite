#!/bin/sh
set -e

SMITE_HTTP_PORT=${SMITE_HTTP_PORT:-80}
SMITE_HTTPS_PORT=${SMITE_HTTPS_PORT:-443}
SMITE_SSL_DOMAIN=${SMITE_SSL_DOMAIN:-REPLACE_DOMAIN}
PANEL_PORT=${PANEL_PORT:-8000}

# fallback to \$PANEL_PORT if upstream override not set
if [ -z "$SMITE_PANEL_UPSTREAM" ]; then
  SMITE_PANEL_UPSTREAM="http://127.0.0.1:${PANEL_PORT}"
fi

if [ "$SMITE_HTTPS_PORT" = "443" ]; then
  SMITE_HTTPS_REDIRECT_SUFFIX=""
else
  SMITE_HTTPS_REDIRECT_SUFFIX=":$SMITE_HTTPS_PORT"
fi

export SMITE_HTTP_PORT
export SMITE_HTTPS_PORT
export SMITE_PANEL_UPSTREAM
export SMITE_SSL_DOMAIN
export SMITE_HTTPS_REDIRECT_SUFFIX

TEMPLATE_PATH="/etc/nginx/templates/default.conf.template"
TARGET_PATH="/etc/nginx/conf.d/default.conf"

mkdir -p "$(dirname "$TARGET_PATH")"

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "Missing nginx template at $TEMPLATE_PATH" >&2
  exit 1
fi

# substitute placeholders with actual values, but leave literal tokens for the base entrypoint
envsubst '$SMITE_HTTP_PORT $SMITE_HTTPS_PORT $SMITE_HTTPS_REDIRECT_SUFFIX $SMITE_PANEL_UPSTREAM $SMITE_SSL_DOMAIN' < "$TEMPLATE_PATH" > "$TARGET_PATH"

exec nginx -g 'daemon off;'
