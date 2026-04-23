#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Setup inicial del VPS Ubuntu 22.04 para correos-cr-bridge
# Ejecutar como root: bash setup.sh
# ═══════════════════════════════════════════════════════════════════
set -e

echo "▶ Actualizando sistema..."
apt-get update
apt-get upgrade -y

echo "▶ Instalando paquetes básicos..."
apt-get install -y \
    curl \
    ufw \
    fail2ban \
    git \
    nginx \
    certbot \
    python3-certbot-nginx \
    ca-certificates

echo "▶ Instalando Docker..."
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi

echo "▶ Configurando firewall (UFW)..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "▶ Configurando fail2ban (protección SSH)..."
systemctl enable fail2ban
systemctl start fail2ban

echo "▶ Creando usuario de aplicación..."
if ! id -u correos >/dev/null 2>&1; then
    useradd -m -s /bin/bash correos
    usermod -aG docker correos
fi

echo "▶ Preparando directorio..."
mkdir -p /home/correos/correos-cr-bridge/logs
chown -R correos:correos /home/correos/correos-cr-bridge

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  Setup base completado."
echo "═══════════════════════════════════════════════════════════════════"
echo ""
echo "Pasos siguientes (manuales):"
echo "  1. Copiar el código a /home/correos/correos-cr-bridge/"
echo "  2. Crear .env desde .env.example"
echo "  3. Deshabilitar login SSH por password (ver deploy/harden-ssh.sh)"
echo "  4. cd /home/correos/correos-cr-bridge && docker compose up -d"
