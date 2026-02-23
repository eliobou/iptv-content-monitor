# 📺 IPTV Monitor - Documentation

## Description

IPTV Monitor est un script Python qui surveille automatiquement votre playlist IPTV M3U, détecte les nouveautés (films, séries, chaînes TV) et vous envoie un résumé par email.

### Fonctionnalités principales

✅ Téléchargement automatique de la playlist IPTV  
✅ Détection intelligente des nouveautés  
✅ Élimination des doublons (même contenu en différentes qualités)  
✅ Catégorisation automatique : Films, Séries, Chaînes TV  
✅ Email HTML élégant et lisible  
✅ Support de multiples destinataires  
✅ Logs détaillés  
✅ Exécution planifiée via cron  

---

## Installation sur Raspberry Pi

### 1. Prérequis

Le script nécessite Python 3 (déjà installé sur Raspberry Pi OS).

```bash
# Vérifier la version de Python
python3 --version
```

### 2. Création du dossier de travail

```bash
# Créer le dossier
sudo mkdir -p /home/pi/update_iptv

# Donner les permissions à l'utilisateur pi
sudo chown -R pi:pi /home/pi/update_iptv

# Se déplacer dans le dossier
cd /home/pi/update_iptv
```

### 3. Installation des fichiers

Copiez les deux fichiers suivants dans `/home/pi/update_iptv/` :

- `iptv_monitor.py` - Le script principal
- `config.json` - Le fichier de configuration

```bash
# Rendre le script exécutable
chmod +x /home/pi/update_iptv/iptv_monitor.py
```

---

## Configuration

### 1. Configuration de Gmail

Pour utiliser Gmail comme serveur SMTP, vous devez créer un **mot de passe d'application** :

1. Accédez à votre compte Google : https://myaccount.google.com/
2. Allez dans "Sécurité"
3. Activez la "Validation en deux étapes" si ce n'est pas déjà fait
4. Cherchez "Mots de passe des applications"
5. Créez un nouveau mot de passe d'application pour "Mail"
6. Copiez le mot de passe généré (16 caractères)

### 2. Édition du fichier config.json

Éditez le fichier `/home/pi/update_iptv/config.json` :

```bash
nano /home/pi/update_iptv/config.json
```

Modifiez les paramètres suivants :

```json
{
    "iptv_url": "http://www.app02.pro:2103/get.php?username=VOTRE_USERNAME&password=VOTRE_PASSWORD&type=m3u_plus&output=ts",
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "votre_email@gmail.com",
    "smtp_password": "xxxx xxxx xxxx xxxx",
    "email_from": "votre_email@gmail.com",
    "email_to": [
        "destinataire1@example.com",
        "destinataire2@example.com",
        "destinataire3@example.com"
    ]
}
```

**Paramètres à modifier :**

- `iptv_url` : Remplacez USERNAME et PASSWORD par vos identifiants IPTV
- `smtp_user` : Votre adresse Gmail
- `smtp_password` : Le mot de passe d'application Gmail (16 caractères)
- `email_from` : L'adresse d'envoi (généralement la même que smtp_user)
- `email_to` : Liste des destinataires (ajoutez autant d'emails que nécessaire)

**Sauvegarde :** Ctrl+O, Entrée, puis Ctrl+X

---

## Test manuel

Avant de configurer le cron, testez le script manuellement :

```bash
cd /home/pi/update_iptv
python3 iptv_monitor.py
```

**Lors du premier lancement :**
- Le script télécharge la playlist
- Aucun email n'est envoyé (pas de version précédente pour comparaison)
- Un fichier `current_playlist.m3u` est créé

**Lors du deuxième lancement :**
- Le script compare avec la version précédente
- Si des nouveautés sont détectées, un email est envoyé

**Vérification des logs :**

```bash
cat /home/pi/update_iptv/iptv_monitor.log
```

---

## Configuration de l'exécution automatique (cron)

### 1. Éditer le crontab

```bash
crontab -e
```

Si c'est la première fois, choisissez l'éditeur (nano recommandé : option 1).

### 2. Ajouter la tâche planifiée

Ajoutez cette ligne à la fin du fichier :

```cron
0 7 * * * /usr/bin/python3 /home/pi/update_iptv/iptv_monitor.py >> /home/pi/update_iptv/cron.log 2>&1
```

**Explication :**
- `0 7 * * *` : Tous les jours à 7h00 du matin
- `/usr/bin/python3` : Chemin complet vers Python 3
- `/home/pi/update_iptv/iptv_monitor.py` : Chemin du script
- `>> /home/pi/update_iptv/cron.log 2>&1` : Redirige les sorties vers un fichier log

**Sauvegarde :** Ctrl+O, Entrée, puis Ctrl+X

### 3. Vérifier que le cron est bien configuré

```bash
crontab -l
```

Vous devriez voir votre ligne de configuration.

### 4. Autres exemples de planification

```cron
# Tous les jours à 7h00
0 7 * * * /usr/bin/python3 /home/pi/update_iptv/iptv_monitor.py

# Tous les jours à 8h30
30 8 * * * /usr/bin/python3 /home/pi/update_iptv/iptv_monitor.py

# Deux fois par jour (7h et 19h)
0 7,19 * * * /usr/bin/python3 /home/pi/update_iptv/iptv_monitor.py

# Toutes les 6 heures
0 */6 * * * /usr/bin/python3 /home/pi/update_iptv/iptv_monitor.py
```

---

## Structure des fichiers

Après installation et première exécution, le dossier contiendra :

```
/home/pi/update_iptv/
├── iptv_monitor.py          # Script principal
├── config.json              # Configuration (IMPORTANT: contient vos mots de passe)
├── current_playlist.m3u     # Playlist actuelle
├── previous_playlist.m3u    # Playlist précédente (pour comparaison)
├── iptv_monitor.log         # Logs du script
└── cron.log                 # Logs des exécutions cron (optionnel)
```

---

## Fonctionnement détaillé

### Détection des nouveautés

Le script compare la playlist actuelle avec la précédente en utilisant un système d'identifiants uniques :

1. **Pour les films :** Nom normalisé (sans qualité ni langue)
   - Exemple : "Film (MULTI) FHD 2025" et "Film (FR) HD 2025" = même film

2. **Pour les séries :** Nom + Saison + Épisode
   - Exemple : "Dr House S01 E01" est unique

3. **Pour les chaînes TV :** Nom de la chaîne normalisé

### Gestion des doublons de qualité

Si le même film existe en plusieurs qualités (SD, HD, FHD, 4K), le script :
- Ne le compte qu'une seule fois
- Affiche automatiquement la meilleure qualité dans l'email

### Catégorisation automatique

Le script catégorise automatiquement :

- **Films** : URLs contenant `/movie/`
- **Séries** : URLs contenant `/series/` ou nom avec pattern `SXX EXX`
- **Chaînes TV** : Tout le reste

---

## Email de rapport

L'email généré contient :

📊 **Résumé :** Nombre total de films, séries et chaînes TV
🎬 **Films :** Liste avec qualité (badge coloré)
📺 **Séries :** Groupées par nom avec liste des nouveaux épisodes
📡 **Chaînes TV :** Liste avec qualité

**Design :** Email HTML responsive, lisible sur mobile et desktop

---

## Consultation des logs

### Logs du script

```bash
# Voir les dernières lignes
tail -n 50 /home/pi/update_iptv/iptv_monitor.log

# Suivre en temps réel
tail -f /home/pi/update_iptv/iptv_monitor.log

# Voir tout le fichier
cat /home/pi/update_iptv/iptv_monitor.log
```

### Logs du cron

```bash
# Logs système du cron
grep CRON /var/log/syslog | tail -n 20

# Logs de votre script (si configuré)
tail -n 50 /home/pi/update_iptv/cron.log
```

---

## Dépannage

### Le script ne s'exécute pas

```bash
# Vérifier les permissions
ls -l /home/pi/update_iptv/iptv_monitor.py

# Tester manuellement
python3 /home/pi/update_iptv/iptv_monitor.py
```

### Pas d'email reçu

1. Vérifiez les logs :
   ```bash
   tail -n 50 /home/pi/update_iptv/iptv_monitor.log
   ```

2. Vérifiez votre configuration Gmail :
   - Mot de passe d'application correct
   - Validation en deux étapes activée

3. Vérifiez votre dossier spam

4. Testez la connexion SMTP :
   ```bash
   python3 -c "import smtplib; s=smtplib.SMTP('smtp.gmail.com',587); s.starttls(); print('OK')"
   ```

### Erreur "No such file or directory"

Vérifiez que tous les chemins sont corrects :
```bash
ls -la /home/pi/update_iptv/
```

### Le cron ne s'exécute pas

```bash
# Vérifier que le service cron est actif
sudo systemctl status cron

# Démarrer le service cron si nécessaire
sudo systemctl start cron

# Activer le service au démarrage
sudo systemctl enable cron
```

---

## Sécurité

⚠️ **IMPORTANT :** Le fichier `config.json` contient vos mots de passe !

### Protéger le fichier de configuration

```bash
# Restreindre les permissions (lecture/écriture pour pi uniquement)
chmod 600 /home/pi/update_iptv/config.json

# Vérifier
ls -l /home/pi/update_iptv/config.json
```

### Sauvegarde

```bash
# Créer une sauvegarde du dossier
cp -r /home/pi/update_iptv /home/pi/update_iptv_backup

# Ou créer une archive
tar -czf update_iptv_backup.tar.gz /home/pi/update_iptv
```

---

## Désinstallation

```bash
# Supprimer la tâche cron
crontab -e
# Supprimez la ligne correspondante

# Supprimer le dossier
rm -rf /home/pi/update_iptv
```

---

## Support et personnalisation

### Modifier l'heure d'exécution

Éditez votre crontab :
```bash
crontab -e
```

### Ajouter des destinataires

Éditez `config.json` et ajoutez des emails dans la liste `email_to`.

### Changer le style de l'email

Le HTML de l'email se trouve dans la méthode `generate_html_email()` du script `iptv_monitor.py`.

---

## Exemple de sortie

### Log d'exécution réussie

```
2025-02-09 07:00:01 - INFO - === Démarrage de IPTV Monitor ===
2025-02-09 07:00:01 - INFO - Configuration chargée avec succès
2025-02-09 07:00:02 - INFO - Téléchargement de la playlist depuis http://www.app02.pro...
2025-02-09 07:00:05 - INFO - Playlist téléchargée: 1234567 caractères
2025-02-09 07:00:05 - INFO - Parsed 5432 entrées
2025-02-09 07:00:06 - INFO - Parsed 5398 entrées
2025-02-09 07:00:06 - INFO - Trouvé 25 nouvelles entrées
2025-02-09 07:00:06 - INFO - ✓ 25 nouveautés détectées
2025-02-09 07:00:07 - INFO - Connexion au serveur SMTP smtp.gmail.com:587
2025-02-09 07:00:09 - INFO - Email envoyé avec succès à 3 destinataire(s)
2025-02-09 07:00:09 - INFO - Ancienne playlist archivée
2025-02-09 07:00:09 - INFO - Playlist sauvegardée dans /home/pi/update_iptv/current_playlist.m3u
2025-02-09 07:00:09 - INFO - === Fin de IPTV Monitor ===
```

---

## Licence

Ce script est fourni tel quel, sans garantie. Vous êtes libre de le modifier selon vos besoins.

---

**Version :** 1.0  
**Date :** Février 2025  
**Auteur :** Claude (Anthropic)
