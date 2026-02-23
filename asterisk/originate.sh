#!/bin/bash
#
# Verbindet den AudioSocket-Bot mit der Konferenz.
#
# Ausfuehrung (waehrend sip_bridge.py laeuft):
#   sudo bash asterisk/originate.sh [port] [conference]
#
# Der Bot muss vorher gestartet sein:
#   python3 sip_bridge.py --port 9092
#
PORT="${1:-9092}"
CONF="${2:-testconf}"

# UUID aus der sip_bridge.py Ausgabe lesen, oder eine neue generieren
UUID="${3:-$(cat /proc/sys/kernel/random/uuid)}"

echo "Originating AudioSocket channel..."
echo "  UUID: $UUID"
echo "  Port: $PORT"
echo "  Conference: $CONF"
echo ""

asterisk -rx "channel originate AudioSocket/$UUID/127.0.0.1:$PORT application ConfBridge $CONF"
RC=$?

if [ $RC -eq 0 ]; then
    echo "OK — Bot sollte jetzt in der Konferenz sein."
else
    echo "FEHLER (exit code $RC)"
    echo ""
    echo "Ist Asterisk gestartet?"
    echo "  systemctl status asterisk"
    echo ""
    echo "Ist das AudioSocket-Modul geladen?"
    echo "  asterisk -rx 'module show like audiosocket'"
fi
