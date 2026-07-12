#!/bin/bash
# Setup Let's Encrypt SSL certificates for Smite Panel

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

DOMAIN=$1
EMAIL=$2

if [ -z "$DOMAIN" ]; then
    echo -e "${RED}Error: Domain is required${NC}"
    exit 1
fi

if [ -z "$EMAIL" ]; then
    echo -e "${RED}Error: Email is required for Let's Encrypt${NC}"
    exit 1
fi

echo "Setting up SSL certificate for domain: $DOMAIN"
echo "Email: $EMAIL"
echo ""
echo "IMPORTANT: Make sure your domain DNS points to this server's IP address!"
echo "Press Enter to continue or Ctrl+C to cancel..."
read

# Install certbot if not present
if ! command -v certbot &> /dev/null; then
    echo "Installing certbot..."
    apt-get update -qq
    apt-get install -y certbot > /dev/null 2>&1
fi

# Create directory for ACME challenge
mkdir -p /var/www/certbot

# Stop nginx temporarily if running (for standalone mode)
NGINX_RUNNING=false
if docker ps | grep -q smite-nginx; then
    NGINX_RUNNING=true
    echo "Stopping nginx temporarily for certificate generation..."
    docker stop smite-nginx > /dev/null 2>&1 || true
fi

# Wait a moment for port 80 to be free
sleep 2

# Generate certificate using standalone mode
echo "Generating SSL certificate (this may take a minute)..."
certbot certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    -d "$DOMAIN" || {
    echo -e "${YELLOW}Warning: Certificate generation failed.${NC}"
    echo "Common issues:"
    echo "  - DNS not pointing to this server"
    echo "  - Port 80 not accessible from internet"
    echo "  - Firewall blocking port 80"
    echo ""
    echo "You can manually set up SSL later or use a different method."
    
    # Restart nginx if it was running
    if [ "$NGINX_RUNNING" = true ]; then
        docker start smite-nginx > /dev/null 2>&1 || true
    fi
    exit 0
}

# Update nginx config with actual domain
if [ -f "nginx/nginx.conf" ]; then
    sed -i "s/REPLACE_DOMAIN/$DOMAIN/g" nginx/nginx.conf
    echo -e "${GREEN}✓ Nginx config updated with domain${NC}"
fi

# Set up auto-renewal cron job
echo "Setting up certificate auto-renewal..."
(crontab -l 2>/dev/null | grep -v "certbot renew" | grep -v "smite-nginx"; echo "0 3 * * * certbot renew --quiet --deploy-hook 'docker restart smite-nginx'") | crontab -

# Restart nginx if it was running
if [ "$NGINX_RUNNING" = true ]; then
    echo "Restarting nginx..."
    docker start smite-nginx > /dev/null 2>&1 || true
fi

echo -e "${GREEN}✓ SSL certificate setup complete!${NC}"
echo "Certificate will auto-renew via cron job."

