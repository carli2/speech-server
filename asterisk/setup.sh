#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== SIP-Peers hinzufuegen ==="
if grep -q '^\[piper\]' /etc/asterisk/sip.conf 2>/dev/null; then
    echo "  [piper] existiert bereits in sip.conf, ueberspringe."
else
    cat "$SCRIPT_DIR/sip_peers.conf" >> /etc/asterisk/sip.conf
    echo "  sip_peers.conf -> /etc/asterisk/sip.conf"
fi

echo "=== Dialplan hinzufuegen ==="
if grep -q '^\[conf\]' /etc/asterisk/extensions.conf 2>/dev/null; then
    echo "  [conf] existiert bereits in extensions.conf, ueberspringe."
else
    cat "$SCRIPT_DIR/extensions.conf" >> /etc/asterisk/extensions.conf
    echo "  extensions.conf -> /etc/asterisk/extensions.conf"
fi

echo "=== AMI aktivieren ==="
# Enable AMI in [general] if not already enabled
if grep -q '^enabled.*=.*yes' /etc/asterisk/manager.conf 2>/dev/null; then
    echo "  AMI bereits aktiviert."
else
    sed -i 's/^;*\s*enabled\s*=.*/enabled = yes/' /etc/asterisk/manager.conf 2>/dev/null || true
    sed -i 's/^;*\s*bindaddr\s*=.*/bindaddr = 127.0.0.1/' /etc/asterisk/manager.conf 2>/dev/null || true
    echo "  AMI enabled in manager.conf"
fi

# Add AMI user
if grep -q '^\[piper\]' /etc/asterisk/manager.conf 2>/dev/null; then
    echo "  AMI-User [piper] existiert bereits."
else
    cat "$SCRIPT_DIR/manager.conf" >> /etc/asterisk/manager.conf
    echo "  AMI-User [piper] -> /etc/asterisk/manager.conf"
fi

echo "=== Asterisk starten/reloaden ==="
if systemctl is-active --quiet asterisk; then
    asterisk -rx "sip reload"
    asterisk -rx "dialplan reload"
    asterisk -rx "manager reload"
    echo "  Asterisk reloaded."
else
    systemctl start asterisk
    sleep 2
    echo "  Asterisk gestartet."
fi

echo ""
echo "=== Fertig ==="
echo ""
echo "Bridge starten (alles automatisch, kein sudo noetig):"
echo "  python3 sip_bridge.py --port 9092"
echo ""
echo "Softphone: testphone / test123 @ 127.0.0.1:5060, Extension 800"
