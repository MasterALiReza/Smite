#!/bin/bash
# Install CLI tools globally

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Install smite (panel CLI)
sudo cp "$SCRIPT_DIR/smite.py" /usr/local/bin/smite
sudo chmod +x /usr/local/bin/smite
echo "Installed smite to /usr/local/bin/smite"

# Install smite-node (node CLI)
sudo cp "$SCRIPT_DIR/smite-node.py" /usr/local/bin/smite-node
sudo chmod +x /usr/local/bin/smite-node
echo "Installed smite-node to /usr/local/bin/smite-node"

# Make Python scripts executable
chmod +x /usr/local/bin/smite
chmod +x /usr/local/bin/smite-node

echo "CLI tools installed successfully!"

