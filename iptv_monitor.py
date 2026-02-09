#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPTV Monitor - Script de surveillance des mises à jour de playlist IPTV
Détecte les nouveautés et envoie un résumé par email
"""

import re
import json
import hashlib
import logging
import smtplib
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import sys

# Configuration des chemins
BASE_DIR = Path("/home/pi/update_iptv")
CURRENT_LIST_FILE = BASE_DIR / "current_playlist.m3u"
PREVIOUS_LIST_FILE = BASE_DIR / "previous_playlist.m3u"
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "iptv_monitor.log"

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class M3UEntry:
    """Représente une entrée dans un fichier M3U"""
    
    def __init__(self, extinf_line, url_line):
        self.raw_extinf = extinf_line
        self.url = url_line.strip()
        self.parse_extinf()
        
    def parse_extinf(self):
        """Parse la ligne EXTINF pour extraire les informations"""
        # Extraction du tvg-name
        name_match = re.search(r'tvg-name="([^"]+)"', self.raw_extinf)
        self.tvg_name = name_match.group(1) if name_match else ""
        
        # Extraction du group-title
        group_match = re.search(r'group-title="([^"]+)"', self.raw_extinf)
        self.group_title = group_match.group(1) if group_match else ""
        
        # Extraction du nom affiché (après la dernière virgule)
        display_match = re.search(r',([^,]+)$', self.raw_extinf)
        self.display_name = display_match.group(1).strip() if display_match else self.tvg_name
        
    def get_normalized_name(self):
        """
        Retourne le nom normalisé sans qualité ni langue pour détecter les doublons
        Exemple: "Film (MULTI) FHD 2025" -> "Film 2025"
        """
        name = self.tvg_name
        # Supprime les qualités (SD, HD, FHD, 4K, UHD, HDR, etc.)
        name = re.sub(r'\b(SD|HD|FHD|4K|UHD|HDR|HEVC)\b', '', name, flags=re.IGNORECASE)
        # Supprime les langues (FR, MULTI, EN, etc.)
        name = re.sub(r'\((FR|MULTI|EN|VF|VOSTFR|VO)\)', '', name, flags=re.IGNORECASE)
        # Supprime les espaces multiples
        name = re.sub(r'\s+', ' ', name).strip()
        return name
    
    def get_quality(self):
        """Extrait la qualité vidéo"""
        quality_match = re.search(r'\b(SD|HD|FHD|4K|UHD)\b', self.tvg_name, re.IGNORECASE)
        return quality_match.group(1).upper() if quality_match else "SD"
    
    def get_content_type(self):
        """Détermine le type de contenu: TV, FILM ou SERIE"""
        # Détection des séries (présence de SXX EXX ou S01 E01)
        if re.search(r'S\d{2}\s*E\d{2}', self.tvg_name, re.IGNORECASE):
            return "SERIE"
        
        # Détection de la TV (URL sans /movie/ ni /series/)
        if '/movie/' not in self.url and '/series/' not in self.url:
            return "TV"
        
        # Par défaut, c'est un film
        return "FILM"
    
    def get_serie_info(self):
        """Extrait le nom de la série et les infos saison/épisode"""
        if self.get_content_type() != "SERIE":
            return None, None, None
            
        # Recherche du pattern SXX EXX
        episode_match = re.search(r'(S\d{2})\s*(E\d{2})', self.tvg_name, re.IGNORECASE)
        if episode_match:
            season = episode_match.group(1).upper()
            episode = episode_match.group(2).upper()
            # Nom de la série = tout avant SXX EXX
            serie_name = re.split(r'S\d{2}\s*E\d{2}', self.tvg_name, flags=re.IGNORECASE)[0]
            serie_name = re.sub(r'\((FR|MULTI|EN)\)\s*(SD|HD|FHD|4K)?\s*$', '', serie_name).strip()
            return serie_name, season, episode
        
        return None, None, None
    
    def get_unique_id(self):
        """Génère un ID unique pour cette entrée"""
        # Pour les séries, on utilise le nom + saison + épisode
        if self.get_content_type() == "SERIE":
            serie_name, season, episode = self.get_serie_info()
            if serie_name:
                return hashlib.md5(f"{serie_name}_{season}_{episode}".encode()).hexdigest()
        
        # Pour les films et TV, on utilise le nom normalisé
        normalized = self.get_normalized_name()
        return hashlib.md5(normalized.encode()).hexdigest()


class IPTVMonitor:
    """Classe principale pour surveiller les changements IPTV"""
    
    def __init__(self):
        self.load_config()
        self.current_entries = []
        self.previous_entries = []
        
    def load_config(self):
        """Charge la configuration depuis le fichier JSON"""
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.iptv_url = config['iptv_url']
                self.smtp_server = config['smtp_server']
                self.smtp_port = config['smtp_port']
                self.smtp_user = config['smtp_user']
                self.smtp_password = config['smtp_password']
                self.email_from = config['email_from']
                self.email_to = config['email_to']  # Liste d'emails
                logger.info("Configuration chargée avec succès")
        except Exception as e:
            logger.error(f"Erreur lors du chargement de la configuration: {e}")
            raise
    
    def download_playlist(self):
        """Télécharge la playlist M3U depuis l'URL"""
        try:
            logger.info(f"Téléchargement de la playlist depuis {self.iptv_url[:50]}...")
            
            # Créer une requête avec un User-Agent pour éviter l'erreur HTTP 461
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            request = Request(self.iptv_url, headers=headers)
            
            with urlopen(request, timeout=30) as response:
                content = response.read().decode('utf-8')
            logger.info(f"Playlist téléchargée: {len(content)} caractères")
            return content
        except HTTPError as e:
            logger.error(f"Erreur HTTP lors du téléchargement: {e.code} - {e.reason}")
            raise
        except URLError as e:
            logger.error(f"Erreur lors du téléchargement: {e}")
            raise
    
    def parse_m3u(self, content):
        """Parse le contenu M3U et retourne une liste d'entrées"""
        entries = []
        lines = content.split('\n')
        total_lines = len(lines)
        
        logger.info(f"Parsing de {total_lines} lignes...")
        
        i = 0
        entry_count = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('#EXTINF:'):
                # La ligne suivante devrait être l'URL
                if i + 1 < len(lines):
                    url_line = lines[i + 1].strip()
                    if url_line and not url_line.startswith('#'):
                        entry = M3UEntry(line, url_line)
                        entries.append(entry)
                        entry_count += 1
                        
                        # Log de progression tous les 1000 entrées
                        if entry_count % 1000 == 0:
                            logger.info(f"Progression: {entry_count} entrées parsées...")
                        
                        i += 2
                        continue
            i += 1
        
        logger.info(f"Parsing terminé: {len(entries)} entrées")
        return entries
    
    def save_playlist(self, content, filepath):
        """Sauvegarde le contenu dans un fichier"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Playlist sauvegardée dans {filepath}")
    
    def load_previous_playlist(self):
        """Charge la playlist précédente si elle existe"""
        if PREVIOUS_LIST_FILE.exists():
            with open(PREVIOUS_LIST_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            return self.parse_m3u(content)
        return []
    
    def find_new_entries(self):
        """Compare les playlists et trouve les nouvelles entrées"""
        # Créer des sets d'IDs uniques
        previous_ids = {entry.get_unique_id() for entry in self.previous_entries}
        
        new_entries = []
        for entry in self.current_entries:
            if entry.get_unique_id() not in previous_ids:
                new_entries.append(entry)
        
        logger.info(f"Trouvé {len(new_entries)} nouvelles entrées")
        return new_entries
    
    def categorize_entries(self, entries):
        """Catégorise les entrées par type (TV, FILMS, SERIES)"""
        categorized = {
            'TV': [],
            'FILM': [],
            'SERIE': {}  # Dictionnaire avec nom_serie: liste d'épisodes
        }
        
        for entry in entries:
            content_type = entry.get_content_type()
            
            if content_type == "TV":
                categorized['TV'].append(entry)
            elif content_type == "FILM":
                # Vérifier si ce film n'est pas déjà présent (même nom, qualité différente)
                normalized_name = entry.get_normalized_name()
                already_added = False
                for existing in categorized['FILM']:
                    if existing.get_normalized_name() == normalized_name:
                        already_added = True
                        # Garde la meilleure qualité
                        if self._compare_quality(entry.get_quality(), existing.get_quality()) > 0:
                            categorized['FILM'].remove(existing)
                            categorized['FILM'].append(entry)
                        break
                if not already_added:
                    categorized['FILM'].append(entry)
            elif content_type == "SERIE":
                serie_name, season, episode = entry.get_serie_info()
                if serie_name:
                    if serie_name not in categorized['SERIE']:
                        categorized['SERIE'][serie_name] = []
                    categorized['SERIE'][serie_name].append((season, episode, entry))
        
        return categorized
    
    def _compare_quality(self, q1, q2):
        """Compare deux qualités. Retourne 1 si q1 > q2, -1 si q1 < q2, 0 si égal"""
        quality_order = {'SD': 1, 'HD': 2, 'FHD': 3, '4K': 4, 'UHD': 5}
        return quality_order.get(q1, 0) - quality_order.get(q2, 0)
    
    def generate_html_email(self, categorized, total_new=None, limited=False):
        """Génère le contenu HTML de l'email"""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: #f4f4f4;
                }
                .container {
                    background-color: white;
                    border-radius: 8px;
                    padding: 30px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }
                h1 {
                    color: #2c3e50;
                    border-bottom: 3px solid #3498db;
                    padding-bottom: 10px;
                    margin-bottom: 20px;
                }
                h2 {
                    color: #34495e;
                    margin-top: 30px;
                    margin-bottom: 15px;
                    border-left: 4px solid #3498db;
                    padding-left: 10px;
                }
                h3 {
                    color: #555;
                    margin-top: 20px;
                    margin-bottom: 10px;
                }
                .summary {
                    background-color: #ecf0f1;
                    padding: 15px;
                    border-radius: 5px;
                    margin-bottom: 20px;
                }
                .warning {
                    background-color: #fff3cd;
                    border-left: 4px solid #f39c12;
                    padding: 15px;
                    margin-bottom: 20px;
                    border-radius: 5px;
                }
                .summary-item {
                    display: inline-block;
                    margin-right: 20px;
                    font-weight: bold;
                }
                .count {
                    color: #3498db;
                    font-size: 1.2em;
                }
                ul {
                    list-style-type: none;
                    padding-left: 0;
                }
                li {
                    padding: 8px;
                    margin-bottom: 5px;
                    background-color: #f9f9f9;
                    border-left: 3px solid #3498db;
                    padding-left: 15px;
                }
                .quality {
                    display: inline-block;
                    padding: 2px 8px;
                    border-radius: 3px;
                    font-size: 0.85em;
                    font-weight: bold;
                    margin-left: 8px;
                }
                .quality-4K, .quality-UHD {
                    background-color: #e74c3c;
                    color: white;
                }
                .quality-FHD {
                    background-color: #f39c12;
                    color: white;
                }
                .quality-HD {
                    background-color: #3498db;
                    color: white;
                }
                .quality-SD {
                    background-color: #95a5a6;
                    color: white;
                }
                .serie-episodes {
                    color: #7f8c8d;
                    font-size: 0.9em;
                    margin-left: 10px;
                }
                .footer {
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid #ddd;
                    color: #7f8c8d;
                    font-size: 0.9em;
                    text-align: center;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📺 Nouveautés IPTV</h1>
                <p>Rapport généré le {date}</p>
        """.format(date=datetime.now().strftime("%d/%m/%Y à %H:%M"))
        
        # Avertissement si liste limitée
        if limited and total_new:
            html += f"""
                <div class="warning">
                    <strong>⚠️ Attention :</strong> {total_new} nouveautés détectées au total.
                    Pour des raisons de taille d'email, seules les 500 premières sont affichées ci-dessous.
                </div>
            """
        
        # Résumé
        total_films = len(categorized['FILM'])
        total_series = len(categorized['SERIE'])
        total_episodes = sum(len(eps) for eps in categorized['SERIE'].values())
        total_tv = len(categorized['TV'])
        
        display_total = total_new if total_new else (total_films + total_episodes + total_tv)
        
        html += f"""
                <div class="summary">
                    <div class="summary-item">📊 Total: <span class="count">{display_total}</span></div>
                    <div class="summary-item">🎬 Films: <span class="count">{total_films}</span></div>
                    <div class="summary-item">📺 Séries: <span class="count">{total_series}</span> ({total_episodes} épisodes)</div>
                    <div class="summary-item">📡 Chaînes TV: <span class="count">{total_tv}</span></div>
                </div>
        """
        
        # Films
        if categorized['FILM']:
            html += "<h2>🎬 Nouveaux Films</h2><ul>"
            for entry in sorted(categorized['FILM'], key=lambda x: x.display_name):
                quality = entry.get_quality()
                html += f'<li>{entry.display_name}<span class="quality quality-{quality}">{quality}</span></li>'
            html += "</ul>"
        
        # Séries
        if categorized['SERIE']:
            html += "<h2>📺 Nouvelles Séries / Épisodes</h2>"
            for serie_name in sorted(categorized['SERIE'].keys()):
                episodes = categorized['SERIE'][serie_name]
                episodes_sorted = sorted(episodes, key=lambda x: (x[0], x[1]))  # Tri par saison puis épisode
                episodes_list = [f"{s}{e}" for s, e, _ in episodes_sorted]
                html += f"<h3>{serie_name}</h3>"
                html += f'<p class="serie-episodes">Nouveaux épisodes: {", ".join(episodes_list)}</p>'
        
        # Chaînes TV
        if categorized['TV']:
            html += "<h2>📡 Nouvelles Chaînes TV</h2><ul>"
            for entry in sorted(categorized['TV'], key=lambda x: x.display_name):
                quality = entry.get_quality()
                html += f'<li>{entry.display_name}<span class="quality quality-{quality}">{quality}</span></li>'
            html += "</ul>"
        
        html += """
                <div class="footer">
                    <p>Ce rapport a été généré automatiquement par IPTV Monitor</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    
    def send_email(self, html_content):
        """Envoie l'email HTML aux destinataires"""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"📺 Nouveautés IPTV - {datetime.now().strftime('%d/%m/%Y')}"
            msg['From'] = self.email_from
            msg['To'] = ', '.join(self.email_to)
            
            # Attache le contenu HTML
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)
            
            # Connexion au serveur SMTP
            logger.info(f"Connexion au serveur SMTP {self.smtp_server}:{self.smtp_port}")
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email envoyé avec succès à {len(self.email_to)} destinataire(s)")
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de l'email: {e}")
            raise
    
    def run(self):
        """Exécute le processus complet de surveillance"""
        try:
            logger.info("=== Démarrage de IPTV Monitor ===")
            
            # Télécharge la playlist actuelle
            current_content = self.download_playlist()
            
            # Parse la playlist actuelle
            self.current_entries = self.parse_m3u(current_content)
            
            # Charge la playlist précédente
            self.previous_entries = self.load_previous_playlist()
            
            # Première exécution : pas d'email
            if not self.previous_entries:
                logger.info("⚠️  Première exécution : création de la référence, aucun email envoyé")
                # Sauvegarde la playlist comme référence (current et previous identiques)
                self.save_playlist(current_content, CURRENT_LIST_FILE)
                self.save_playlist(current_content, PREVIOUS_LIST_FILE)
                logger.info("=== Fin de IPTV Monitor ===")
                return
            
            # Trouve les nouveautés
            new_entries = self.find_new_entries()
            
            if new_entries:
                logger.info(f"✓ {len(new_entries)} nouveautés détectées")
                
                # Si trop de nouveautés, on limite pour l'email
                if len(new_entries) > 500:
                    logger.warning(f"⚠️  {len(new_entries)} nouveautés trouvées, limitation à 500 pour l'email")
                    logger.info("💡 Astuce: C'est probablement la première comparaison ou une mise à jour massive")
                    # Prendre les 500 premières entrées seulement
                    new_entries_for_email = new_entries[:500]
                    limited = True
                else:
                    new_entries_for_email = new_entries
                    limited = False
                
                # Catégorise les nouveautés
                logger.info("Catégorisation des nouveautés...")
                categorized = self.categorize_entries(new_entries_for_email)
                
                # Génère l'email HTML
                logger.info("Génération de l'email...")
                html_content = self.generate_html_email(categorized, len(new_entries), limited)
                
                # Envoie l'email
                self.send_email(html_content)
            else:
                logger.info("Aucune nouveauté détectée")
            
            # Archive l'ancienne version
            if CURRENT_LIST_FILE.exists():
                CURRENT_LIST_FILE.rename(PREVIOUS_LIST_FILE)
                logger.info("Ancienne playlist archivée")
            
            # Sauvegarde la nouvelle version
            self.save_playlist(current_content, CURRENT_LIST_FILE)
            
            logger.info("=== Fin de IPTV Monitor ===")
            
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    # Crée le dossier de base si nécessaire
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Lance le moniteur
    monitor = IPTVMonitor()
    monitor.run()
