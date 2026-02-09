# 🚀 Guide de Démarrage Rapide - IPTV Monitor

## Installation en 5 minutes

### 1️⃣ Transférer les fichiers sur votre Raspberry Pi

**Option A - Via SCP (depuis votre ordinateur) :**
```bash
scp iptv_monitor.py config.json README.md install.sh pi@<IP_DU_RASPBERRY>:/home/pi/
```

**Option B - Via clé USB :**
1. Copiez les fichiers sur une clé USB
2. Insérez la clé dans le Raspberry Pi
3. Copiez les fichiers :
```bash
cp /media/pi/USB_NAME/* /home/pi/
```

**Option C - Téléchargement direct (si vous avez les fichiers en ligne) :**
```bash
cd /home/pi
wget <URL_des_fichiers>
```

### 2️⃣ Lancer l'installation automatique

```bash
cd /home/pi
sudo bash install.sh
```

### 3️⃣ Configurer vos paramètres

```bash
nano /home/pi/update_iptv/config.json
```

**Remplacez :**
- `USERNAME` et `PASSWORD` : vos identifiants IPTV
- `votre_email@gmail.com` : votre adresse Gmail
- `votre_mot_de_passe_application` : le mot de passe d'application Gmail
- La liste `email_to` : les destinataires des rapports

**Sauvegarde :** Ctrl+O, Entrée, puis Ctrl+X

### 4️⃣ Créer un mot de passe d'application Gmail

1. Allez sur https://myaccount.google.com/security
2. Activez la "Validation en deux étapes"
3. Cherchez "Mots de passe des applications"
4. Créez un mot de passe pour "Mail"
5. Copiez-le dans `config.json`

### 5️⃣ Test manuel

```bash
python3 /home/pi/update_iptv/iptv_monitor.py
```

**Premier lancement :** Aucun email (création de la référence)  
**Deuxième lancement :** Email si des nouveautés sont détectées

### 6️⃣ Configurer l'exécution automatique

```bash
crontab -e
```

Ajoutez cette ligne :
```
0 7 * * * /usr/bin/python3 /home/pi/update_iptv/iptv_monitor.py
```

**Sauvegarde :** Ctrl+O, Entrée, puis Ctrl+X

---

## ✅ C'est terminé !

Votre système va maintenant :
- Vérifier la playlist tous les jours à 7h00
- Vous envoyer un email uniquement s'il y a des nouveautés
- Garder des logs de toutes les opérations

---

## 📊 Vérifications

### Vérifier les logs
```bash
tail -f /home/pi/update_iptv/iptv_monitor.log
```

### Vérifier le cron
```bash
crontab -l
```

### Tester une exécution manuelle
```bash
python3 /home/pi/update_iptv/iptv_monitor.py
```

---

## 🆘 Problèmes courants

### "Permission denied"
```bash
sudo chmod +x /home/pi/update_iptv/iptv_monitor.py
```

### "No module named..."
Tous les modules utilisés sont standards avec Python 3.

### "Authentication failed" (Gmail)
- Vérifiez que vous utilisez un mot de passe d'application
- Vérifiez que la validation en deux étapes est activée

### Pas d'email reçu
- Vérifiez vos spams
- Vérifiez les logs : `tail /home/pi/update_iptv/iptv_monitor.log`

---

## 📚 Documentation complète

Pour plus de détails, consultez le README :
```bash
cat /home/pi/update_iptv/README.md
```

---

**Bon monitoring ! 🎬📺**
