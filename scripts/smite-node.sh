#!/bin/bash
# Smite Node Installer - Smart Multi-Instance & Zero-Touch Auto-Discovery
# Supports interactive deployment & one-click automated join

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

progress() {
    echo -e "${GREEN}✓${NC} $1"
}

info() {
    echo -e "${CYAN}ℹ${NC} $1"
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

err() {
    echo -e "${RED}✗${NC} $1"
}

echo -e "${BOLD}${CYAN}=== Smite Node Smart Installer ===${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    err "Please run as root (use sudo)"
    exit 1
fi

# Enable Docker BuildKit for faster builds
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    info "Docker not found. Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh > /dev/null 2>&1
    rm get-docker.sh
    progress "Docker installed"
fi

# Export non-interactive git
export GIT_TERMINAL_PROMPT=0

# Ensure Docker Compose is available
ensure_docker_compose() {
    if docker compose version &> /dev/null; then
        return 0
    fi

    if command -v docker-compose &> /dev/null; then
        # Create CLI plugin symlinks so 'docker compose' works natively
        mkdir -p /usr/local/lib/docker/cli-plugins ~/.docker/cli-plugins /usr/lib/docker/cli-plugins
        local dc_bin="$(command -v docker-compose)"
        ln -sf "$dc_bin" /usr/local/lib/docker/cli-plugins/docker-compose 2>/dev/null || true
        ln -sf "$dc_bin" ~/.docker/cli-plugins/docker-compose 2>/dev/null || true
        ln -sf "$dc_bin" /usr/lib/docker/cli-plugins/docker-compose 2>/dev/null || true
        if docker compose version &> /dev/null; then
            return 0
        fi
    fi

    warn "Docker Compose not detected. Installing..."
    apt-get update -qq && (apt-get install -y docker-compose-plugin 2>/dev/null || apt-get install -y docker-compose 2>/dev/null) || true
    
    if ! docker compose version &> /dev/null && ! command -v docker-compose &> /dev/null; then
        local arch="x86_64"
        case "$(uname -m)" in
            aarch64|arm64) arch="aarch64" ;;
            armv7l|armhf)  arch="armv7" ;;
            *)             arch="x86_64" ;;
        esac
        mkdir -p /usr/local/lib/docker/cli-plugins
        curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${arch}" -o /usr/local/lib/docker/cli-plugins/docker-compose 2>/dev/null || true
        chmod +x /usr/local/lib/docker/cli-plugins/docker-compose 2>/dev/null || true
    fi
}
ensure_docker_compose

# Helper: Universal docker compose execution
run_compose() {
    if docker compose version &> /dev/null; then
        docker compose "$@"
    elif command -v docker-compose &> /dev/null; then
        docker-compose "$@"
    else
        err "Docker Compose is required but not found."
        exit 1
    fi
}

# -------------------------------------------------------------
# CLI Arguments Parsing (One-Click Auto Join Support)
# -------------------------------------------------------------
ARG_PANEL=""
ARG_TOKEN=""
ARG_ROLE=""
ARG_PORT=""
ARG_NAME=""
ARG_AUTO="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --panel)
            ARG_PANEL="$2"
            shift 2
            ;;
        --token)
            ARG_TOKEN="$2"
            shift 2
            ;;
        --role)
            ARG_ROLE="$2"
            shift 2
            ;;
        --port)
            ARG_PORT="$2"
            shift 2
            ;;
        --name)
            ARG_NAME="$2"
            shift 2
            ;;
        --auto)
            ARG_AUTO="true"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if [ -n "$ARG_PANEL" ] && [ -n "$ARG_TOKEN" ]; then
    ARG_AUTO="true"
fi

# -------------------------------------------------------------
# Helper: Detect Public IP Address of this machine
# -------------------------------------------------------------
detect_public_ip() {
    local ip=""
    for url in "https://api.ipify.org" "https://ifconfig.me" "https://icanhazip.com" "https://ident.me" "https://checkip.amazonaws.com"; do
        ip=$(curl -s --connect-timeout 3 "$url" 2>/dev/null | tr -d ' \n\r' || true)
        if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "$ip"
            return 0
        fi
    done
    
    # Fallback to local default route IP
    ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -n 1 || true)
    if [ -n "$ip" ]; then
        echo "$ip"
        return 0
    fi
    
    echo "127.0.0.1"
}

# -------------------------------------------------------------
# Helper: Detect Country Code of this machine (GeoIP)
# -------------------------------------------------------------
detect_country_code() {
    local cc=""
    for url in "http://ip-api.com/line?fields=countryCode" "https://ifconfig.co/country-iso" "https://ipapi.co/country/" "https://api.country.is"; do
        cc=$(curl -s --connect-timeout 2 "$url" 2>/dev/null | tr -d ' \n\r' || true)
        if [[ "$cc" =~ ^[A-Za-z]{2}$ ]]; then
            echo "$cc" | tr '[:lower:]' '[:upper:]'
            return 0
        fi
    done
    echo ""
}

# -------------------------------------------------------------
# Helper: Check if a network port is already in use on the host
# -------------------------------------------------------------
is_port_in_use() {
    local port=$1
    if command -v ss &> /dev/null; then
        ss -tuln | grep -qE "[: ]${port}\b" && return 0
    elif command -v netstat &> /dev/null; then
        netstat -tuln | grep -qE "[: ]${port}\b" && return 0
    elif command -v lsof &> /dev/null; then
        lsof -i ":${port}" &> /dev/null && return 0
    fi
    return 1
}

# -------------------------------------------------------------
# Helper: Find the next available free port starting from a base
# -------------------------------------------------------------
find_next_free_port() {
    local candidate=${1:-8888}
    while is_port_in_use "$candidate"; do
        candidate=$((candidate + 1))
    done
    echo "$candidate"
}

# -------------------------------------------------------------
# Helper: Scan for existing Smite Node installations
# -------------------------------------------------------------
EXISTING_DIRS=()
EXISTING_CONTAINERS=()
EXISTING_PORTS=()
EXISTING_ROLES=()
EXISTING_NAMES=()

scan_existing_nodes() {
    EXISTING_DIRS=()
    EXISTING_CONTAINERS=()
    EXISTING_PORTS=()
    EXISTING_ROLES=()
    EXISTING_NAMES=()

    local found_dirs=()
    if [ -d "/opt/smite-node" ]; then
        found_dirs+=("/opt/smite-node")
    fi
    for d in /opt/smite-node-[0-9]*; do
        if [ -d "$d" ]; then
            found_dirs+=("$d")
        fi
    done
    if [ -d "/usr/local/node" ]; then
        found_dirs+=("/usr/local/node")
    fi

    for d in "${found_dirs[@]}"; do
        if [ -f "$d/docker-compose.yml" ] || [ -f "$d/.env" ]; then
            EXISTING_DIRS+=("$d")
            
            local name="node"
            local port="8888"
            local role="unknown"
            if [ -f "$d/.env" ]; then
                name=$(grep -E '^NODE_NAME=' "$d/.env" | cut -d '=' -f2- | tr -d '"'\'' ' || echo "node")
                port=$(grep -E '^NODE_API_PORT=' "$d/.env" | cut -d '=' -f2- | tr -d '"'\'' ' || echo "8888")
                role=$(grep -E '^NODE_ROLE=' "$d/.env" | cut -d '=' -f2- | tr -d '"'\'' ' || echo "unknown")
            fi
            
            local cname="smite-node"
            if [ -f "$d/docker-compose.yml" ]; then
                local found_cname=$(grep -E 'container_name:' "$d/docker-compose.yml" | awk '{print $2}' | tr -d '"'\'' ' || true)
                if [ -n "$found_cname" ]; then
                    cname="$found_cname"
                fi
            fi
            
            EXISTING_NAMES+=("${name:-node}")
            EXISTING_PORTS+=("${port:-8888}")
            EXISTING_ROLES+=("${role:-unknown}")
            EXISTING_CONTAINERS+=("$cname")
        fi
    done
}

# -------------------------------------------------------------
# Helper: Find the next available instance index and directory
# -------------------------------------------------------------
get_next_instance_info() {
    if [ ! -d "/opt/smite-node" ]; then
        echo "1 /opt/smite-node smite-node smite-node-data"
        return
    fi

    local i=2
    while [ -d "/opt/smite-node-$i" ]; do
        i=$((i + 1))
    done
    echo "$i /opt/smite-node-$i smite-node-$i smite-node-${i}-data"
}

# -------------------------------------------------------------
# Function: Generate docker-compose.yml for a given instance
# -------------------------------------------------------------
create_compose_file() {
    local target_dir=$1
    local c_name=$2
    local v_name=$3
    local port_var='${NODE_API_PORT:-8888}'
    local name_var='${NODE_NAME:-node-1}'
    local ca_var='${PANEL_CA_PATH:-/etc/smite-node/certs/ca.crt}'
    local addr_var='${PANEL_ADDRESS}'
    local pport_var='${PANEL_API_PORT:-8000}'
    local ver_var='${SMITE_VERSION:-latest}'

    cat > "$target_dir/docker-compose.yml" << EOF
# Smite Node - Docker Compose Configuration (${c_name})
# Usage: cd ${target_dir} && docker compose up -d

services:
  ${c_name}:
    image: ghcr.io/masteralireza/smite-node:${ver_var}
    build:
      context: .
      dockerfile: Dockerfile
    container_name: ${c_name}
    network_mode: host
    cap_add:
      - NET_ADMIN
      - SYS_MODULE
    devices:
      - /dev/net/tun:/dev/net/tun
    volumes:
      - ./app:/app/app
      - ./main.py:/app/main.py
      - ./certs:/etc/smite-node/certs:ro
      - ./config:/etc/smite-node
      - ${v_name}:/var/lib/smite-node
    env_file:
      - .env
    environment:
      - NODE_API_PORT=${port_var}
      - NODE_NAME=${name_var}
      - PANEL_CA_PATH=${ca_var}
      - PANEL_ADDRESS=${addr_var}
      - PANEL_API_PORT=${pport_var}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:${port_var}/api/agent/status')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    security_opt:
      - no-new-privileges:true

volumes:
  ${v_name}:
    driver: local
    name: ${v_name}
EOF
}

# -------------------------------------------------------------
# Function: Install CLI tool
# -------------------------------------------------------------
install_cli_tool() {
    local install_dir=$1
    info "Installing / updating CLI tool..."
    if [ -f "/opt/smite/cli/smite-node.py" ]; then
        cp /opt/smite/cli/smite-node.py /usr/local/bin/smite-node
        chmod +x /usr/local/bin/smite-node
    elif [ -f "$install_dir/cli/smite-node.py" ]; then
        cp "$install_dir/cli/smite-node.py" /usr/local/bin/smite-node
        chmod +x /usr/local/bin/smite-node
    else
        CLI_BRANCH="main"
        if [ "${SMITE_VERSION:-latest}" = "next" ]; then
            CLI_BRANCH="next"
        fi
        curl -fsSL "https://raw.githubusercontent.com/MasterALiReza/Smite/${CLI_BRANCH}/cli/smite-node.py" -o /usr/local/bin/smite-node || true
        chmod +x /usr/local/bin/smite-node || true
    fi
    progress "CLI tool installed to /usr/local/bin/smite-node"
}

# -------------------------------------------------------------
# Function: Apply System & Kernel Optimizations
# -------------------------------------------------------------
apply_kernel_optimizations() {
    info "Applying network & kernel optimizations for stable tunnels..."
    if [ -f "/etc/sysctl.conf" ]; then
        if [ ! -f "/etc/sysctl.conf.smite-backup" ]; then
            cp /etc/sysctl.conf /etc/sysctl.conf.smite-backup
        fi
        
        if ! grep -q "# Smite Network Optimizations" /etc/sysctl.conf; then
            cat >> /etc/sysctl.conf << 'EOF'

# Smite Network Optimizations
net.core.somaxconn = 65535
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.ip_local_port_range = 10000 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 30
net.ipv4.tcp_keepalive_time = 60
net.ipv4.tcp_keepalive_intvl = 10
net.ipv4.tcp_keepalive_probes = 6
net.ipv4.tcp_slow_start_after_idle = 0
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv4.udp_mem = 3145728 4194304 16777216
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
            sysctl -p > /dev/null 2>&1 || true
            progress "Network optimizations applied"
        else
            progress "Network optimizations already active"
        fi
    fi

    if [ -f "/etc/security/limits.conf" ]; then
        if ! grep -q "# Smite File Descriptor Limits" /etc/security/limits.conf; then
            cat >> /etc/security/limits.conf << 'EOF'

# Smite File Descriptor Limits
* soft nofile 65535
* hard nofile 65535
root soft nofile 65535
root hard nofile 65535
EOF
            progress "File descriptor limits increased"
        fi
        ulimit -n 65535 2>/dev/null || true
    fi

    if modprobe -n tcp_bbr 2>/dev/null; then
        if ! grep -q "tcp_bbr" /etc/modules-load.d/*.conf 2>/dev/null && ! grep -q "tcp_bbr" /etc/modules 2>/dev/null; then
            echo "tcp_bbr" | tee -a /etc/modules-load.d/smite.conf > /dev/null 2>&1 || echo "tcp_bbr" >> /etc/modules 2>/dev/null || true
            modprobe tcp_bbr 2>/dev/null || true
            sysctl -w net.ipv4.tcp_congestion_control=bbr > /dev/null 2>&1 || true
            sysctl -w net.core.default_qdisc=fq > /dev/null 2>&1 || true
            progress "BBR congestion control enabled"
        fi
    fi
}

# -------------------------------------------------------------
# Function: Auto-Register Node in Panel
# -------------------------------------------------------------
auto_register_in_panel() {
    local p_addr=$1
    local n_name=$2
    local n_ip=$3
    local n_port=$4
    local n_role=$5
    local n_token=$6
    local n_cc=$7

    if [ -z "$n_token" ] || [ -z "$p_addr" ]; then
        return 0
    fi

    info "Sending Zero-Touch Auto-Registration request to Panel (${p_addr})..."
    
    # Strip protocol if present in panel address
    local clean_addr=$(echo "$p_addr" | sed -e 's|^https\?://||' -e 's|/$||')
    local proto="http"
    if [[ "$p_addr" =~ ^https:// ]]; then
        proto="https"
    fi

    local reg_url="${proto}://${clean_addr}/api/nodes/auto-register"
    local json_payload="{\"name\":\"${n_name}\",\"ip_address\":\"${n_ip}\",\"api_port\":${n_port},\"role\":\"${n_role}\",\"registration_token\":\"${n_token}\",\"metadata\":{\"country_code\":\"${n_cc}\"}}"

    local resp=$(curl -s -X POST "$reg_url" \
        -H "Content-Type: application/json" \
        -d "$json_payload" 2>/dev/null || true)

    if echo "$resp" | grep -q '"fingerprint"'; then
        progress "Node successfully auto-registered and connected in Panel!"
    else
        # Try fallback URL without /api prefix
        local fallback_url="${proto}://${clean_addr}/nodes/auto-register"
        local resp2=$(curl -s -X POST "$fallback_url" \
            -H "Content-Type: application/json" \
            -d "$json_payload" 2>/dev/null || true)
        if echo "$resp2" | grep -q '"fingerprint"'; then
            progress "Node successfully auto-registered and connected in Panel!"
        else
            warn "Auto-registration response: $resp"
            info "You can also manually verify the server in the Panel UI under Foreign Nodes / Iran Nodes."
        fi
    fi
}

# -------------------------------------------------------------
# Function: One-Click Automated Deployment Engine
# -------------------------------------------------------------
deploy_auto_one_click() {
    info "Initiating One-Click Automated Node Join..."
    
    read -r next_idx next_dir next_cname next_vname <<< "$(get_next_instance_info)"
    
    local target_dir="$next_dir"
    local c_name="$next_cname"
    local v_name="$next_vname"
    local instance_idx="$next_idx"

    local panel_addr="$ARG_PANEL"
    local reg_token="$ARG_TOKEN"
    local node_role="${ARG_ROLE:-foreign}"
    
    # Auto-detect free port
    local node_port="$ARG_PORT"
    if [ -z "$node_port" ]; then
        node_port=$(find_next_free_port 8888)
    fi

    # Auto-detect public IP
    local public_ip=$(detect_public_ip)
    
    # Node name
    local node_name="$ARG_NAME"
    if [ -z "$node_name" ]; then
        local hostname_clean=$(hostname -s 2>/dev/null || echo "srv")
        node_name="${hostname_clean}-node-${instance_idx}"
    fi

    echo -e "  - Target Directory: ${CYAN}${target_dir}${NC}"
    echo -e "  - Container Name:   ${CYAN}${c_name}${NC}"
    echo -e "  - Public IP:        ${CYAN}${public_ip}${NC}"
    echo -e "  - Node API Port:    ${CYAN}${node_port}${NC}"
    echo -e "  - Node Role:        ${CYAN}${node_role}${NC}"
    echo -e "  - Panel Address:    ${CYAN}${panel_addr}${NC}"
    echo ""

    mkdir -p "$target_dir/certs" "$target_dir/config"

    # Download CA certificate from panel automatically
    info "Downloading CA certificate from Panel..."
    local ca_endpoint="panel/ca/server"
    if [ "$node_role" = "iran" ]; then
        ca_endpoint="panel/ca"
    fi

    local clean_addr=$(echo "$panel_addr" | sed -e 's|^https\?://||' -e 's|/$||')
    local proto="http"
    if [[ "$panel_addr" =~ ^https:// ]]; then
        proto="https"
    fi

    if ! curl -fsSL "${proto}://${clean_addr}/api/${ca_endpoint}" -o "$target_dir/certs/ca.crt" 2>/dev/null; then
        curl -fsSL "${proto}://${clean_addr}/${ca_endpoint}" -o "$target_dir/certs/ca.crt" 2>/dev/null || true
    fi

    if [ ! -s "$target_dir/certs/ca.crt" ]; then
        warn "Could not download CA certificate directly from panel API. Creating placeholder..."
        touch "$target_dir/certs/ca.crt"
    else
        progress "CA certificate downloaded"
    fi

    # Extract port from panel_addr if specified
    local panel_api_port="8000"
    if [[ "$clean_addr" =~ :([0-9]+)$ ]]; then
        panel_api_port="${BASH_REMATCH[1]}"
    fi

    # Write .env file
    cat > "$target_dir/.env" << EOF
NODE_API_PORT=$node_port
NODE_NAME=$node_name
NODE_ROLE=$node_role
SMITE_VERSION=${SMITE_VERSION:-latest}

PANEL_CA_PATH=/etc/smite-node/certs/ca.crt
PANEL_ADDRESS=$panel_addr
PANEL_API_PORT=$panel_api_port
EOF
    progress "Configuration written to ${target_dir}/.env"

    # Create docker-compose.yml
    create_compose_file "$target_dir" "$c_name" "$v_name"

    # Download node code from GitHub
    GIT_BRANCH=""
    if [ "${SMITE_VERSION:-latest}" = "next" ]; then
        GIT_BRANCH="-b next"
    fi

    TEMP_DIR=$(mktemp -d)
    if GIT_TERMINAL_PROMPT=0 git clone --depth 1 $GIT_BRANCH https://github.com/MasterALiReza/Smite.git "$TEMP_DIR" 2>/dev/null; then
        cp -r "$TEMP_DIR/node"/* "$target_dir/" 2>/dev/null || true
        rm -rf "$TEMP_DIR"
    else
        rm -rf "$TEMP_DIR"
        local branch="${GIT_BRANCH#-b }"
        [ -z "$branch" ] && branch="main"
        if curl -fsSL "https://github.com/MasterALiReza/Smite/archive/refs/heads/${branch}.tar.gz" -o "/tmp/smite-${branch}.tar.gz" 2>/dev/null; then
            tar -xzf "/tmp/smite-${branch}.tar.gz" -C /tmp 2>/dev/null || true
            cp -r /tmp/Smite-${branch}/node/* "$target_dir/" 2>/dev/null || true
            rm -rf "/tmp/smite-${branch}.tar.gz" "/tmp/Smite-${branch}" 2>/dev/null || true
        fi
    fi

    # Re-apply compose file
    create_compose_file "$target_dir" "$c_name" "$v_name"

    # Apply optimizations
    apply_kernel_optimizations

    # Pull or build image
    if [ -z "${SMITE_VERSION}" ]; then
        export SMITE_VERSION=latest
    fi

    if ! docker pull "ghcr.io/masteralireza/smite-node:${SMITE_VERSION}" 2>/dev/null; then
        (cd "$target_dir" && run_compose build 2>&1 || true)
    fi

    # Start the container
    info "Starting node container (${c_name})..."
    (cd "$target_dir" && run_compose up -d)

    # Install CLI tool
    install_cli_tool "$target_dir"

    # Wait for node to be alive
    sleep 3

    # Auto-detect location
    local detected_cc=$(detect_country_code)
    [ -n "$detected_cc" ] && info "Detected server location: ${detected_cc}"

    # Auto-register in Panel
    auto_register_in_panel "$panel_addr" "$node_name" "$public_ip" "$node_port" "$node_role" "$reg_token" "$detected_cc"

    echo ""
    echo -e "${GREEN}${BOLD}================================================================${NC}"
    echo -e "${GREEN}${BOLD}🎉 Smite Node (${c_name}) Installed & Registered Successfully!${NC}"
    echo -e "${GREEN}${BOLD}================================================================${NC}"
    echo -e "  - Public IP:      ${CYAN}${public_ip}${NC}"
    echo -e "  - Node API Port:  ${CYAN}${node_port}${NC}"
    echo -e "  - Container:      ${CYAN}${c_name}${NC}"
    echo -e "  - Directory:      ${CYAN}${target_dir}${NC}"
    echo -e "  - Status:         ${GREEN}Connected in Panel${NC}"
    echo ""
    echo -e "Manage node instances with: ${BOLD}smite-node status${NC}"
    echo ""
    exit 0
}

# -------------------------------------------------------------
# Function: Interactive Node Configuration & Deployment
# -------------------------------------------------------------
deploy_node_instance() {
    local target_dir=$1
    local c_name=$2
    local v_name=$3
    local instance_idx=$4

    echo ""
    echo -e "${BOLD}=== Node Configuration (${c_name}) ===${NC}"
    echo -e "Installation path: ${CYAN}${target_dir}${NC}"
    echo ""

    # 1. Panel Address
    read -p "Panel address (host:port, e.g., 1.2.3.4:8000 or panel.domain.com:443): " PANEL_ADDRESS
    while [ -z "$PANEL_ADDRESS" ]; do
        err "Panel address is required!"
        read -p "Panel address (host:port): " PANEL_ADDRESS
    done

    # 2. Panel Port
    read -p "Panel API port (default: 8000): " PANEL_API_PORT
    PANEL_API_PORT=${PANEL_API_PORT:-8000}

    # 3. Node API Port (Auto-detected free port)
    local suggested_port=$(find_next_free_port 8888)
    if is_port_in_use 8888 && [ "$suggested_port" -ne 8888 ]; then
        info "Port 8888 is currently in use. Suggested free port: ${BOLD}${suggested_port}${NC}"
    fi

    read -p "Node API port (default: ${suggested_port}): " NODE_API_PORT
    NODE_API_PORT=${NODE_API_PORT:-$suggested_port}

    while is_port_in_use "$NODE_API_PORT"; do
        warn "Port $NODE_API_PORT is already in use on this server!"
        local auto_free=$(find_next_free_port "$((NODE_API_PORT + 1))")
        read -p "Please choose an available port (suggested: ${auto_free}): " NODE_API_PORT
        NODE_API_PORT=${NODE_API_PORT:-$auto_free}
    done

    # 4. Node Name
    local default_name="node-${instance_idx}"
    read -p "Node name (default: ${default_name}): " NODE_NAME
    NODE_NAME=${NODE_NAME:-$default_name}

    # 5. Server Role
    echo ""
    echo -e "${BOLD}=== Server Role ===${NC}"
    echo "1) Iran Server (runs tunnel clients, connects to foreign servers)"
    echo "2) Foreign Server (runs tunnel servers, accepts connections from Iran)"
    read -p "Enter choice [1 or 2] (default: 1): " ROLE_CHOICE
    ROLE_CHOICE=${ROLE_CHOICE:-1}

    if [ "$ROLE_CHOICE" = "2" ]; then
        NODE_ROLE="foreign"
        echo -e "${GREEN}✓ Selected: Foreign Server${NC}"
        CA_SOURCE="Panel > Foreign Servers > View CA Certificate"
    else
        NODE_ROLE="iran"
        echo -e "${GREEN}✓ Selected: Iran Server${NC}"
        CA_SOURCE="Panel > Iran Nodes > View CA Certificate"
    fi

    # 6. CA Certificate Paste
    echo ""
    echo -e "${BOLD}=== CA Certificate ===${NC}"
    echo -e "Paste the CA certificate from the panel (${CYAN}${CA_SOURCE}${NC}):"
    echo "Press Enter after pasting, then press Enter again on an empty line to finish:"
    echo ""
    PANEL_CA_CONTENT=""
    has_content=false
    while IFS= read -r line; do
        if [ -z "$line" ]; then
            if [ "$has_content" = true ]; then
                break
            fi
            continue
        else
            has_content=true
            PANEL_CA_CONTENT="${PANEL_CA_CONTENT}${line}\n"
        fi
    done

    if [ -z "$PANEL_CA_CONTENT" ]; then
        err "CA certificate is required to authenticate with the panel."
        exit 1
    fi

    mkdir -p "$target_dir/certs" "$target_dir/config"
    echo -e "$PANEL_CA_CONTENT" > "$target_dir/certs/ca.crt"
    if [ ! -s "$target_dir/certs/ca.crt" ]; then
        err "Failed to save CA certificate!"
        exit 1
    fi
    progress "CA certificate saved to ${target_dir}/certs/ca.crt"

    cat > "$target_dir/.env" << EOF
NODE_API_PORT=$NODE_API_PORT
NODE_NAME=$NODE_NAME
NODE_ROLE=$NODE_ROLE
SMITE_VERSION=${SMITE_VERSION:-latest}

PANEL_CA_PATH=/etc/smite-node/certs/ca.crt
PANEL_ADDRESS=$PANEL_ADDRESS
PANEL_API_PORT=$PANEL_API_PORT
EOF
    progress "Configuration saved to ${target_dir}/.env"

    create_compose_file "$target_dir" "$c_name" "$v_name"
    progress "Docker Compose created for container: ${c_name}"

    GIT_BRANCH=""
    if [ "${SMITE_VERSION:-latest}" = "next" ]; then
        GIT_BRANCH="-b next"
    fi

    info "Downloading latest node code from GitHub..."
    TEMP_DIR=$(mktemp -d)
    if GIT_TERMINAL_PROMPT=0 git clone --depth 1 $GIT_BRANCH https://github.com/MasterALiReza/Smite.git "$TEMP_DIR" 2>/dev/null; then
        cp -r "$TEMP_DIR/node"/* "$target_dir/" 2>/dev/null || true
        rm -rf "$TEMP_DIR"
    else
        rm -rf "$TEMP_DIR"
        local branch="${GIT_BRANCH#-b }"
        [ -z "$branch" ] && branch="main"
        if curl -fsSL "https://github.com/MasterALiReza/Smite/archive/refs/heads/${branch}.tar.gz" -o "/tmp/smite-${branch}.tar.gz" 2>/dev/null; then
            tar -xzf "/tmp/smite-${branch}.tar.gz" -C /tmp 2>/dev/null || true
            cp -r /tmp/Smite-${branch}/node/* "$target_dir/" 2>/dev/null || true
            rm -rf "/tmp/smite-${branch}.tar.gz" "/tmp/Smite-${branch}" 2>/dev/null || true
        fi
    fi

    create_compose_file "$target_dir" "$c_name" "$v_name"
    apply_kernel_optimizations

    echo ""
    info "Pulling Docker image..."
    if [ -z "${SMITE_VERSION}" ]; then
        export SMITE_VERSION=latest
    fi

    if ! docker pull "ghcr.io/masteralireza/smite-node:${SMITE_VERSION}" 2>/dev/null; then
        (cd "$target_dir" && run_compose build 2>&1 || true)
    fi

    info "Starting node container (${c_name})..."
    (cd "$target_dir" && run_compose up -d)

    install_cli_tool "$target_dir"

    sleep 3
    if docker ps --format '{{.Names}}' | grep -q "^${c_name}$"; then
        echo ""
        echo -e "${GREEN}${BOLD}✅ Smite Node (${c_name}) installed & running successfully!${NC}"
        echo -e "  - Directory:      ${CYAN}${target_dir}${NC}"
        echo -e "  - Container:      ${CYAN}${c_name}${NC}"
        echo -e "  - Node API Port:  ${CYAN}${NODE_API_PORT}${NC}"
        echo -e "  - Role:           ${CYAN}${NODE_ROLE}${NC}"
        echo ""
        echo -e "Manage nodes with: ${BOLD}smite-node status${NC}"
        echo ""
    else
        err "Installation completed, but container ${c_name} is not running."
        echo "Check logs with: cd ${target_dir} && smite-node logs"
        exit 1
    fi
}

# -------------------------------------------------------------
# Function: Update Existing Node Safely
# -------------------------------------------------------------
update_existing_node() {
    local target_dir=$1
    local c_name=$2

    echo ""
    info "Safely updating node in ${target_dir} (${c_name})..."

    if [ ! -d "$target_dir" ]; then
        err "Directory $target_dir does not exist."
        return
    fi

    local ver=${SMITE_VERSION:-latest}
    docker pull "ghcr.io/masteralireza/smite-node:${ver}" 2>/dev/null || true

    (
        cd "$target_dir"
        run_compose pull 2>/dev/null || true
        run_compose up -d --force-recreate
    )

    install_cli_tool "$target_dir"
    progress "Node ${c_name} successfully updated!"
}

# -------------------------------------------------------------
# MAIN DISPATCHER
# -------------------------------------------------------------
scan_existing_nodes

# Check if One-Click automated join is triggered via flags
if [ "$ARG_AUTO" = "true" ]; then
    deploy_auto_one_click
fi

if [ ${#EXISTING_DIRS[@]} -eq 0 ]; then
    # No existing node found -> Standard fresh install (Node 1)
    deploy_node_instance "/opt/smite-node" "smite-node" "smite-node-data" "1"
else
    # Existing nodes found -> Display intelligent management menu
    echo -e "${YELLOW}${BOLD}Existing Smite Node(s) detected on this server:${NC}"
    echo "--------------------------------------------------------------------------------"
    printf "%-4s | %-16s | %-14s | %-6s | %-8s | %-12s\n" "No." "Container" "Name" "Port" "Role" "Status"
    echo "--------------------------------------------------------------------------------"
    
    for i in "${!EXISTING_DIRS[@]}"; do
        num=$((i + 1))
        cname="${EXISTING_CONTAINERS[$i]}"
        nname="${EXISTING_NAMES[$i]}"
        port="${EXISTING_PORTS[$i]}"
        role="${EXISTING_ROLES[$i]}"
        
        status="stopped"
        if docker ps --format '{{.Names}}' | grep -q "^${cname}$"; then
            status="running"
        fi

        printf "%-4s | %-16s | %-14s | %-6s | %-8s | %-12s\n" "[$num]" "$cname" "$nname" "$port" "$role" "$status"
    done
    echo "--------------------------------------------------------------------------------"
    echo ""

    read -r next_idx next_dir next_cname next_vname <<< "$(get_next_instance_info)"

    echo -e "${BOLD}Please choose an action:${NC}"
    echo -e "  1) ${GREEN}Add a new parallel Node${NC} (e.g., ${next_cname} in ${next_dir}) [Recommended]"
    echo -e "  2) ${CYAN}Update an existing Node${NC} (pull latest image & restart safely)"
    echo -e "  3) ${YELLOW}Reinstall / Overwrite an existing Node${NC} (with safe backup)"
    echo -e "  4) Cancel / Exit"
    echo ""
    read -p "Enter choice [1-4] (default: 1): " ACTION_CHOICE
    ACTION_CHOICE=${ACTION_CHOICE:-1}

    case "$ACTION_CHOICE" in
        1)
            deploy_node_instance "$next_dir" "$next_cname" "$next_vname" "$next_idx"
            ;;
        2)
            if [ ${#EXISTING_DIRS[@]} -eq 1 ]; then
                update_existing_node "${EXISTING_DIRS[0]}" "${EXISTING_CONTAINERS[0]}"
            else
                echo ""
                read -p "Enter node number to update [1-${#EXISTING_DIRS[@]}] (or 'all'): " UPDATE_TARGET
                if [ "$UPDATE_TARGET" = "all" ]; then
                    for idx in "${!EXISTING_DIRS[@]}"; do
                        update_existing_node "${EXISTING_DIRS[$idx]}" "${EXISTING_CONTAINERS[$idx]}"
                    done
                else
                    target_idx=$((UPDATE_TARGET - 1))
                    if [ -n "${EXISTING_DIRS[$target_idx]}" ]; then
                        update_existing_node "${EXISTING_DIRS[$target_idx]}" "${EXISTING_CONTAINERS[$target_idx]}"
                    else
                        err "Invalid node number."
                        exit 1
                    fi
                fi
            fi
            ;;
        3)
            target_idx=0
            if [ ${#EXISTING_DIRS[@]} -gt 1 ]; then
                read -p "Enter node number to reinstall [1-${#EXISTING_DIRS[@]}]: " REINSTALL_NUM
                target_idx=$((REINSTALL_NUM - 1))
            fi

            target_d="${EXISTING_DIRS[$target_idx]}"
            target_c="${EXISTING_CONTAINERS[$target_idx]}"
            
            if [ -z "$target_d" ]; then
                err "Invalid node selected."
                exit 1
            fi

            echo ""
            warn "You are about to REINSTALL ${target_c} (${target_d})!"
            read -p "Type 'YES' to confirm: " CONFIRM_REINSTALL
            if [ "$CONFIRM_REINSTALL" != "YES" ]; then
                info "Reinstall cancelled."
                exit 0
            fi

            BACKUP_PATH="${target_d}.bak.$(date +%Y%m%d_%H%M%S)"
            info "Creating safe backup at: ${BACKUP_PATH}"
            cp -r "$target_d" "$BACKUP_PATH"
            progress "Backup created"

            (cd "$target_d" && run_compose down 2>/dev/null || true)
            docker stop "$target_c" 2>/dev/null || true
            docker rm -f "$target_c" 2>/dev/null || true

            inst_num=$(echo "$target_d" | grep -oE '[0-9]+$' || echo "1")
            vname="${target_c}-data"
            [ "$target_c" = "smite-node" ] && vname="smite-node-data"

            deploy_node_instance "$target_d" "$target_c" "$vname" "$inst_num"
            ;;
        4)
            info "Operation cancelled by user."
            exit 0
            ;;
        *)
            err "Invalid choice. Exiting."
            exit 1
            ;;
    esac
fi
