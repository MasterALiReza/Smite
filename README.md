# Smite - Tunneling Control Panel

<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/SmiteD.png"/>
    <source media="(prefers-color-scheme: light)" srcset="assets/SmiteL.png"/>
    <img src="assets/SmiteL.png" alt="Smite Logo" width="200"/>
  </picture>
  
  **Enterprise-grade tunnel management built on GOST, Backhaul, Rathole, Chisel, and FRP, featuring zero-touch dual-node auto-registration, real-time live ping/latency tracking, intelligent GeoIP naming with country flags, and open-source freedom.**
  
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
  [![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
  [![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688.svg)](https://fastapi.tiangolo.com/)
  [![React](https://img.shields.io/badge/React-18+-61DAFB.svg)](https://reactjs.org/)
  [![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-3178C6.svg)](https://www.typescriptlang.org/)
  [![Docker](https://img.shields.io/badge/Docker-24.0+-2496ED.svg)](https://www.docker.com/)
  [![Nginx](https://img.shields.io/badge/Nginx-1.25+-009639.svg)](https://www.nginx.com/)
  [![SQLite](https://img.shields.io/badge/SQLite-3.42+-003B57.svg)](https://www.sqlite.org/)
</div>

---

## 🚀 Features

- **⚡ Zero-Touch One-Click Node Join**: Generate a secure 1-click install command from the WebUI. Automatically configures certificates, downloads CA, detects IP, and registers with reverse health verification.
- **📍 Intelligent GeoIP Auto-Naming & Flags**: Automatically resolves node locations and assigns sequential names (e.g. `DE Node 1`, `TR Node 1`, `US Node 1`) with crisp SVG country flags and bilingual display (English / Persian `نود آلمان ۱`).
- **📶 Live Real-Time Latency & Ping (ms)**: Dynamic real-time round-trip latency tracking with color-coded animated pulsing badges (🟢 `< 80ms`, 🟡 `80–180ms`, 🔴 `> 180ms`).
- **✏️ In-Panel Node Name Editing**: Customize and edit node display names directly within the panel table without modifying server configs.
- **🛡️ Multiple High-Performance Cores**: Complete support for **GOST**, **Backhaul**, **Rathole**, **Chisel**, and **FRP** over TCP, UDP, WebSocket, gRPC, and TCPMux.
- **🔒 Rathole Multi-Tunnel Isolation**: Isolated dynamic core control port allocation (`25000–50000`) ensuring seamless parallel tunnels between the same server pair without port collisions. Supports **Noise Protocol** keypairs and WebSocket transports.
- **🔄 Multi-Instance Node CLI (`smite-node`)**: Full multi-instance node management on a single VPS (`smite-node update all`, `status`, `restart`, `logs`, `uninstall`).
- **🤖 Telegram Bot Integration**: Real-time panel statistics, tunnel status alerts, and automated database backups directly to Telegram.

---

## 📋 Prerequisites

- Linux Server (Ubuntu 20.04+, Debian 11+, CentOS 8+, AlmaLinux)
- Docker & Docker Compose installed
- For domestic/Iran servers, install Docker using the mirror:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/manageitir/docker/main/install-ubuntu.sh | sh
  ```

---

## 🔧 Panel Installation

### 1-Click Quick Install

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/MasterALiReza/Smite/main/scripts/install.sh)"
```

<details>
<summary><strong>Manual Install</strong></summary>

1. Clone the repository:
```bash
git clone https://github.com/MasterALiReza/Smite.git /opt/smite
cd /opt/smite
```

2. Copy environment file and configure:
```bash
cp .env.example .env
# Edit .env with your secret keys and credentials
```

3. Install CLI tools:
```bash
sudo bash cli/install_cli.sh
```

4. Start panel services:
```bash
docker compose up -d
```

5. Create admin user:
```bash
smite admin create
```

6. Access the web interface at `http://YOUR_SERVER_IP:8000`

</details>

---

## 🖥️ Node Installation

### ⚡ Method 1: Zero-Touch 1-Click Join (Recommended)

1. Open your Smite WebUI and navigate to **Iran Nodes** or **Foreign Nodes**.
2. Click **⚡ Auto Join Command**.
3. Copy the generated 1-click command and run it in the terminal of your node server:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/MasterALiReza/Smite/main/scripts/smite-node.sh | sudo bash -s -- --auto --panel "http://YOUR_PANEL_IP:8000" --token "YOUR_JOIN_TOKEN" --role "foreign"
   ```
4. The node will automatically download CA certs, allocate free ports, identify its GeoIP country, register itself with the panel, and appear live in your table!

---

### 💻 Method 2: Interactive Installer

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/MasterALiReza/Smite/main/scripts/smite-node.sh)"
```

---

## 🛠️ CLI Management Tools

### Panel CLI (`smite`)

```bash
smite status            # Show system status and running containers
smite update            # Update panel to latest version
smite restart           # Restart panel services
smite logs              # View panel logs
smite edit              # Edit docker-compose.yml
smite edit-env          # Edit .env configuration file
smite admin create      # Create a new admin account
smite admin update      # Reset admin password
```

### Node CLI (`smite-node`)

The Node CLI natively manages single and multi-instance deployments on the same VPS:

```bash
smite-node status            # Show status table of all installed node instances
smite-node update [all|N]    # Safely update core adapters and recreate containers
smite-node restart [all|N]   # Restart node instance (N = instance index or 'all')
smite-node logs [N] [-f]     # View or live-stream node logs
smite-node edit [N]          # Edit docker-compose.yml for instance N
smite-node edit-env [N]      # Edit .env file for instance N
smite-node uninstall [N]     # Safely uninstall a specific node instance with volume cleanup
```

---

## 📖 Supported Tunnel Cores

| Core | Transport Protocols | Reverse Tunnel | Encryption / Security | Features |
| :--- | :--- | :---: | :---: | :--- |
| **GOST** | TCP, UDP, WS, WSS, gRPC, TCPMux | ✅ | TLS, Yamux, Cloudflare CDN | Dual-Node reverse routing, CDN SNI spoofing |
| **Backhaul** | TCP, UDP, WS, WSMux, TCPMux | ✅ | Sniffer, Keepalive | UDP-over-TCP, low overhead, port-forwarding |
| **Rathole** | TCP, WS, WSS, Noise | ✅ | Noise Protocol (`Noise_KK_25519_ChaChaPoly_BLAKE2s`) | Auto-isolated core ports, high throughput |
| **Chisel** | HTTP, WS, WSS | ✅ | SSH / TLS | High-performance TCP reverse tunneling |
| **FRP** | TCP, UDP, KCP, QUIC, WS | ✅ | TLS, Token Auth | Multi-port mapping, IPv6 over IPv4 |

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 💰 Donations

If you find Smite useful and want to support its development, consider making a donation:

### Cryptocurrency Donations

- **Bitcoin (BTC)**: `bc1q637gahjssmv9g3903j88tn6uyy0w2pwuvsp5k0`
- **Ethereum (ETH)**: `0x5B2eE8970E3B233F79D8c765E75f0705278098a0`
- **Tron (TRX)**: `TSAsosG9oHMAjAr3JxPQStj32uAgAUmMp3`
- **USDT (BEP20)**: `0x5B2eE8970E3B233F79D8c765E75f0705278098a0`
- **TON**: `UQA-95WAUn_8pig7rsA9mqnuM5juEswKONSlu-jkbUBUhku6`

---

<div align="center">
  
  **Made with ❤️ by [MasterALiReza](https://github.com/MasterALiReza)**
  
  *Securing the digital world, one line of code at a time!*
  
</div>
