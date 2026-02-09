#!/bin/bash
# Script d'installation automatique de IPTV Monitor
# Usage: sudo bash install.sh

set -e

echo "================================================"
echo "   Installation de IPTV Monitor"
echo "================================================"
echo ""

# Vérifier que le script est exécuté en tant que root ou avec sudo
if [ "$EUID" -ne 0 ]; then
    echo "❌ Erreur: Ce script doit être exécuté avec sudo"
    echo "Usage: sudo bash install.sh"
    exit 1
fi

# Définir les variables
INSTALL_DIR="/home/pi/update_iptv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "📁 Création du dossier d'installation..."
mkdir -p "$INSTALL_DIR"

echo "📋 Copie des fichiers..."
cp "$SCRIPT_DIR/iptv_monitor.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/config.json" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/README.md" "$INSTALL_DIR/"

echo "🔒 Configuration des permissions..."
chown -R pi:pi "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/iptv_monitor.py"
chmod 600 "$INSTALL_DIR/config.json"

echo ""
echo "✅ Installation terminée avec succès !"
echo ""
echo "📝 Prochaines étapes :"
echo ""
echo "1. Éditez le fichier de configuration :"
echo "   nano $INSTALL_DIR/config.json"
echo ""
echo "2. Configurez vos paramètres :"
echo "   - URL IPTV (username et password)"
echo "   - Paramètres Gmail (email et mot de passe d'application)"
echo "   - Liste des destinataires"
echo ""
echo "3. Testez le script :"
echo "   python3 $INSTALL_DIR/iptv_monitor.py"
echo ""
echo "4. Configurez le cron pour exécution automatique :"
echo "   crontab -e"
echo "   Ajoutez la ligne :"
echo "   0 7 * * * /usr/bin/python3 $INSTALL_DIR/iptv_monitor.py"
echo ""
echo "📚 Consultez le README pour plus d'informations :"
echo "   cat $INSTALL_DIR/README.md"
echo ""
echo "================================================"
