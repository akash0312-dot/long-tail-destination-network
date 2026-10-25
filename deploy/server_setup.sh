#!/usr/bin/env bash
# One-time setup of an Ubuntu 24.04 server (Azure, Oracle Cloud, or any VM) for the live demo.
# Safe to run again. Usage on the server:  bash server_setup.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

sudo apt-get update -q
sudo apt-get install -y -q docker.io docker-compose-v2 openssl

# Rotate container logs (Docker keeps them forever by default): at most 3 x 10 MB per container
if [ ! -f /etc/docker/daemon.json ]; then
  echo '{"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "3"}}' |
    sudo tee /etc/docker/daemon.json >/dev/null
  sudo systemctl restart docker
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# Small servers (under 3.5 GB of RAM): add 2 GB of swap so image builds and first boot cannot run out of memory
if [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 3500000 ] && ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# Oracle's Ubuntu images reject every inbound port except 22 in iptables (on top of the cloud firewall): open 80
# and 443 for Caddy there and keep the rules across reboots. Other clouds (Azure) have no such rule; nothing to do.
if sudo iptables -S INPUT | grep -q -- "-j REJECT"; then
  sudo apt-get install -y -q iptables-persistent
  for port in 80 443; do
    sudo iptables -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null ||
      sudo iptables -I INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
  done
  sudo netfilter-persistent save
fi

# deploy/docker-compose.demo.yml uses `!override`, which needs Compose 2.24.4 or newer
version="$(docker compose version --short)"
if [ "$(printf '%s\n' 2.24.4 "$version" | sort -V | head -1)" != 2.24.4 ]; then
  echo "Docker Compose $version is too old (need 2.24.4+): install Docker from docs.docker.com/engine/install/ubuntu" >&2
  exit 1
fi
echo "server ready: $(uname -m), $(free -h | awk '/Mem/ {print $2}') RAM, Docker Compose $version"
