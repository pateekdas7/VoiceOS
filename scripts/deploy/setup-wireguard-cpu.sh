#!/usr/bin/env bash
# ==============================================================================
# VoiceOS CPU Node — WireGuard + socat bridge setup
#
# Run as root on the CPU server (101.53.140.201).
# Sets up the WireGuard tunnel endpoint that the GPU node connects to via
# an autossh TCP tunnel (GPU:wg → socat UDP→TCP → autossh → CPU:51821 →
# socat TCP→UDP → CPU:wg UDP:51820).
#
# After running this script:
#   1. Print the CPU WireGuard public key (shown at the end).
#   2. Update the GPU's /etc/wireguard/wg0.conf [Peer] PublicKey with it.
#   3. Run: sudo wg set wg0 peer <GPU_PUBKEY> ... (or just restart wg-quick)
#   4. Update GPU wg-ssh-tunnel: change 101.53.138.85 → 101.53.140.201
#   5. Restart wg-ssh-tunnel on GPU.
# ==============================================================================
set -euo pipefail

GPU_WG_PUBKEY="Zhfa9bu9z7vGb/fvs0LIZpTkyUWtEGKZYcar2KAMDGU="
GPU_WG_IP="10.99.0.2"
CPU_WG_IP="10.99.0.1"
WG_IFACE="wg0"

echo "[1/6] Installing wireguard-tools and socat..."
apt-get update -qq
apt-get install -y wireguard-tools socat

echo "[2/6] Generating WireGuard keypair for CPU..."
mkdir -p /etc/wireguard
chmod 700 /etc/wireguard
PRIVATE_KEY=$(wg genkey)
PUBLIC_KEY=$(echo "$PRIVATE_KEY" | wg pubkey)
echo "CPU WireGuard private key written to /etc/wireguard/cpu_private.key"
echo "$PRIVATE_KEY" > /etc/wireguard/cpu_private.key
chmod 600 /etc/wireguard/cpu_private.key

echo "[3/6] Writing /etc/wireguard/wg0.conf..."
cat > /etc/wireguard/wg0.conf << EOF
[Interface]
PrivateKey = ${PRIVATE_KEY}
Address = ${CPU_WG_IP}/24
ListenPort = 51820
# Allow IP forwarding so k8s pods can reach GPU via WireGuard
PostUp   = iptables -t nat -A POSTROUTING -o ${WG_IFACE} -j MASQUERADE; \
           iptables -A FORWARD -i ${WG_IFACE} -j ACCEPT; \
           iptables -A FORWARD -o ${WG_IFACE} -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o ${WG_IFACE} -j MASQUERADE; \
           iptables -D FORWARD -i ${WG_IFACE} -j ACCEPT; \
           iptables -D FORWARD -o ${WG_IFACE} -j ACCEPT

[Peer]
# GPU node (NVIDIA L40, 69.19.140.92)
PublicKey = ${GPU_WG_PUBKEY}
AllowedIPs = ${GPU_WG_IP}/32
PersistentKeepalive = 25
# No Endpoint here — GPU initiates the tunnel via autossh, so packets arrive
# via the socat-TCP bridge on port 51821 → UDP wg0:51820.
EOF
chmod 600 /etc/wireguard/wg0.conf

echo "[4/6] Creating socat bridge service (TCP:51821 → UDP wg0:51820)..."
cat > /etc/systemd/system/wg-socat-cpu.service << 'EOF'
[Unit]
Description=WireGuard socat bridge CPU (TCP 51821 -> UDP 51820)
After=network.target
Before=wg-quick@wg0.service

[Service]
ExecStart=/usr/bin/socat TCP4-LISTEN:51821,fork,reuseaddr UDP:127.0.0.1:51820
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "[5/6] Enabling and starting services..."
systemctl daemon-reload
systemctl enable wg-socat-cpu.service
systemctl start wg-socat-cpu.service
systemctl enable wg-quick@wg0.service
wg-quick up wg0 2>/dev/null || true

echo "[6/6] Enabling IP forwarding (persist across reboots)..."
sysctl -w net.ipv4.ip_forward=1
grep -q "net.ipv4.ip_forward" /etc/sysctl.conf \
  && sed -i 's/#.*net.ipv4.ip_forward.*/net.ipv4.ip_forward=1/' /etc/sysctl.conf \
  || echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf

echo ""
echo "============================================================"
echo " CPU WireGuard PUBLIC KEY (give this to update GPU peer):"
echo "   ${PUBLIC_KEY}"
echo "============================================================"
echo ""
echo "NEXT STEPS:"
echo "  1. On GPU: update /etc/wireguard/wg0.conf [Peer] PublicKey = ${PUBLIC_KEY}"
echo "  2. On GPU: sudo systemctl restart wg-quick@wg0"
echo "  3. On GPU: edit /etc/systemd/system/wg-ssh-tunnel.service"
echo "             change 101.53.138.85 → 101.53.140.201"
echo "  4. On GPU: sudo systemctl daemon-reload && sudo systemctl restart wg-ssh-tunnel"
echo "  5. Add GPU SSH pub key to CPU root authorized_keys:"
echo "     echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFi6TVroOkzPyMCqtUihTKPa6c+gZKiFyxEOcpHSb+08 ubuntu@ywyhmeuua' >> /root/.ssh/authorized_keys"
echo "  6. Run: wg show  — should show GPU peer with recent handshake"
