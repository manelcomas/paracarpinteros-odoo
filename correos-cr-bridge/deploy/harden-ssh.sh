#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Endurece SSH: deshabilita login por password y login directo root.
# Ejecutar SOLO después de verificar que tu clave SSH funciona.
# Probar desde otra terminal: ssh root@TU_IP — debe entrar sin pedir password.
# ═══════════════════════════════════════════════════════════════════
set -e

SSHD=/etc/ssh/sshd_config

echo "▶ Backup de sshd_config..."
cp $SSHD ${SSHD}.bak.$(date +%Y%m%d-%H%M%S)

echo "▶ Deshabilitando login por password..."
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' $SSHD
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' $SSHD
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' $SSHD

echo "▶ Reiniciando SSH..."
systemctl restart sshd

echo ""
echo "✅ SSH endurecido."
echo "   - Login por password: DESHABILITADO"
echo "   - Login root: solo con clave SSH"
echo "   - NO cierres esta sesión aún. Abre otra terminal y prueba:"
echo "     ssh root@$(curl -s ifconfig.me)"
