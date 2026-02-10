#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPTV Monitor - Script de surveillance des mises à jour de playlist IPTV
Détecte les ajouts, suppressions et modifications, puis envoie un résumé par email
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

# Limite du nombre d'entrées par section dans l'email
EMAIL_MAX_PER_SECTION = 200

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
        name_match = re.search(r'tvg-name="([^"]+)"', self.raw_extinf)
        self.tvg_name = name_match.group(1) if name_match else ""

        group_match = re.search(r'group-title="([^"]+)"', self.raw_extinf)
        self.group_title = group_match.group(1) if group_match else ""

        display_match = re.search(r',([^,]+)$', self.raw_extinf)
        self.display_name = display_match.group(1).strip() if display_match else self.tvg_name

    def get_normalized_name(self):
        """Nom normalisé sans qualité ni langue pour détecter les doublons"""
        name = self.tvg_name
        name = re.sub(r'\b(SD|HD|FHD|4K|UHD|HDR|HEVC)\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\((FR|MULTI|EN|VF|VOSTFR|VO)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def get_quality(self):
        """Extrait la qualité vidéo"""
        quality_match = re.search(r'\b(SD|HD|FHD|4K|UHD)\b', self.tvg_name, re.IGNORECASE)
        return quality_match.group(1).upper() if quality_match else "SD"

    def get_content_type(self):
        """Détermine le type de contenu : TV, FILM ou SERIE"""
        if re.search(r'S\d{2}\s*E\d{2}', self.tvg_name, re.IGNORECASE):
            return "SERIE"
        if '/movie/' not in self.url and '/series/' not in self.url:
            return "TV"
        return "FILM"

    def get_serie_info(self):
        """Extrait le nom de la série et les infos saison/épisode"""
        if self.get_content_type() != "SERIE":
            return None, None, None
        episode_match = re.search(r'(S\d{2})\s*(E\d{2})', self.tvg_name, re.IGNORECASE)
        if episode_match:
            season = episode_match.group(1).upper()
            episode = episode_match.group(2).upper()
            serie_name = re.split(r'S\d{2}\s*E\d{2}', self.tvg_name, flags=re.IGNORECASE)[0]
            serie_name = re.sub(r'\((FR|MULTI|EN)\)\s*(SD|HD|FHD|4K)?\s*$', '', serie_name).strip()
            return serie_name, season, episode
        return None, None, None

    def get_unique_id(self):
        """
        Génère un ID stable basé uniquement sur le nom (pas l'URL).
        Cela permet de détecter une modification d'URL sur un même contenu.
        """
        if self.get_content_type() == "SERIE":
            serie_name, season, episode = self.get_serie_info()
            if serie_name:
                return hashlib.md5(f"{serie_name}_{season}_{episode}".encode()).hexdigest()
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
                self.email_to = config['email_to']
                logger.info("Configuration chargée avec succès")
        except Exception as e:
            logger.error(f"Erreur lors du chargement de la configuration: {e}")
            raise

    def download_playlist(self):
        """Télécharge la playlist M3U depuis l'URL"""
        try:
            logger.info(f"Téléchargement de la playlist depuis {self.iptv_url[:50]}...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            request = Request(self.iptv_url, headers=headers)
            with urlopen(request, timeout=30) as response:
                content = response.read().decode('utf-8')
            logger.info(f"Playlist téléchargée : {len(content)} caractères")
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
        logger.info(f"Parsing de {len(lines)} lignes...")
        i = 0
        count = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('#EXTINF:'):
                if i + 1 < len(lines):
                    url_line = lines[i + 1].strip()
                    if url_line and not url_line.startswith('#'):
                        entries.append(M3UEntry(line, url_line))
                        count += 1
                        if count % 10000 == 0:
                            logger.info(f"  ... {count} entrées parsées")
                        i += 2
                        continue
            i += 1
        logger.info(f"Parsing terminé : {len(entries)} entrées")
        return entries

    def save_playlist(self, content, filepath):
        """Sauvegarde le contenu dans un fichier"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Playlist sauvegardée : {filepath}")

    def load_previous_playlist(self):
        """Charge la playlist précédente si elle existe"""
        if PREVIOUS_LIST_FILE.exists():
            with open(PREVIOUS_LIST_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            return self.parse_m3u(content)
        return []

    def compare_playlists(self):
        """
        Compare les deux playlists et retourne les ajouts, suppressions et modifications.
        La clé de comparaison est l'ID unique (basé sur le nom).
        Une modification = même ID mais URL différente.
        """
        logger.info("Création des index de comparaison...")

        # Index : unique_id -> entry
        previous_index = {e.get_unique_id(): e for e in self.previous_entries}
        current_index  = {e.get_unique_id(): e for e in self.current_entries}

        previous_ids = set(previous_index.keys())
        current_ids  = set(current_index.keys())

        # Ajouts : présents dans current mais pas dans previous
        added_ids   = current_ids - previous_ids
        # Suppressions : présents dans previous mais pas dans current
        removed_ids = previous_ids - current_ids
        # Communs : présents dans les deux → vérifier si l'URL a changé
        common_ids  = current_ids & previous_ids

        added   = [current_index[uid] for uid in added_ids]
        removed = [previous_index[uid] for uid in removed_ids]

        modified = []
        for uid in common_ids:
            prev = previous_index[uid]
            curr = current_index[uid]
            if prev.url != curr.url:
                modified.append((prev, curr))  # (ancienne entrée, nouvelle entrée)

        logger.info(f"Résultat : {len(added)} ajout(s), {len(removed)} suppression(s), {len(modified)} modification(s)")
        return added, removed, modified

    def categorize_entries(self, entries):
        """Catégorise les entrées par type (TV, FILM, SERIE) en dédupliquant les qualités"""
        categorized = {'TV': [], 'FILM': [], 'SERIE': {}}

        for entry in entries:
            content_type = entry.get_content_type()

            if content_type == "TV":
                # Déduplique les chaînes TV (même nom, qualités différentes)
                norm = entry.get_normalized_name()
                existing = next((e for e in categorized['TV'] if e.get_normalized_name() == norm), None)
                if existing:
                    if self._compare_quality(entry.get_quality(), existing.get_quality()) > 0:
                        categorized['TV'].remove(existing)
                        categorized['TV'].append(entry)
                else:
                    categorized['TV'].append(entry)

            elif content_type == "FILM":
                norm = entry.get_normalized_name()
                existing = next((e for e in categorized['FILM'] if e.get_normalized_name() == norm), None)
                if existing:
                    if self._compare_quality(entry.get_quality(), existing.get_quality()) > 0:
                        categorized['FILM'].remove(existing)
                        categorized['FILM'].append(entry)
                else:
                    categorized['FILM'].append(entry)

            elif content_type == "SERIE":
                serie_name, season, episode = entry.get_serie_info()
                if serie_name:
                    if serie_name not in categorized['SERIE']:
                        categorized['SERIE'][serie_name] = []
                    categorized['SERIE'][serie_name].append((season, episode, entry))

        return categorized

    def categorize_modifications(self, modifications):
        """Catégorise les modifications par type"""
        categorized = {'TV': [], 'FILM': [], 'SERIE': {}}
        for prev, curr in modifications:
            content_type = curr.get_content_type()
            if content_type == "TV":
                categorized['TV'].append((prev, curr))
            elif content_type == "FILM":
                categorized['FILM'].append((prev, curr))
            elif content_type == "SERIE":
                serie_name, season, episode = curr.get_serie_info()
                if serie_name:
                    if serie_name not in categorized['SERIE']:
                        categorized['SERIE'][serie_name] = []
                    categorized['SERIE'][serie_name].append((season, episode, prev, curr))
        return categorized

    def _compare_quality(self, q1, q2):
        order = {'SD': 1, 'HD': 2, 'FHD': 3, '4K': 4, 'UHD': 5}
        return order.get(q1, 0) - order.get(q2, 0)

    # ─────────────────────────────────────────────────────────────────────────
    # Génération HTML
    # ─────────────────────────────────────────────────────────────────────────

    def _quality_badge(self, quality):
        return f'<span class="quality quality-{quality}">{quality}</span>'

    def _render_added_section(self, categorized, total_raw, limited):
        """Génère le HTML pour la section Ajouts"""
        html = ""
        total_films   = len(categorized['FILM'])
        total_series  = len(categorized['SERIE'])
        total_episodes= sum(len(v) for v in categorized['SERIE'].values())
        total_tv      = len(categorized['TV'])

        if total_films + total_series + total_tv == 0:
            return ""

        html += '<div class="section section-added">'
        html += '<h2>🆕 Ajouts</h2>'

        if limited:
            html += f'<div class="warning">⚠️ {total_raw} ajouts détectés au total. Seuls les {EMAIL_MAX_PER_SECTION} premiers de chaque catégorie sont affichés.</div>'

        html += f'''
        <div class="summary">
            <span class="summary-item">🎬 Films : <span class="count">{total_films}</span></span>
            <span class="summary-item">📺 Séries : <span class="count">{total_series}</span> ({total_episodes} épisodes)</span>
            <span class="summary-item">📡 Chaînes TV : <span class="count">{total_tv}</span></span>
        </div>'''

        if categorized['FILM']:
            html += '<h3>🎬 Films</h3><ul>'
            for e in sorted(categorized['FILM'], key=lambda x: x.display_name):
                html += f'<li>{e.display_name} {self._quality_badge(e.get_quality())}</li>'
            html += '</ul>'

        if categorized['SERIE']:
            html += '<h3>📺 Séries</h3>'
            for serie_name in sorted(categorized['SERIE'].keys()):
                eps = sorted(categorized['SERIE'][serie_name], key=lambda x: (x[0], x[1]))
                eps_list = ", ".join(f"{s}{e}" for s, e, _ in eps)
                html += f'<div class="serie-block"><strong>{serie_name}</strong><span class="serie-episodes"> → {eps_list}</span></div>'

        if categorized['TV']:
            html += '<h3>📡 Chaînes TV</h3><ul>'
            for e in sorted(categorized['TV'], key=lambda x: x.display_name):
                html += f'<li>{e.display_name} {self._quality_badge(e.get_quality())}</li>'
            html += '</ul>'

        html += '</div>'
        return html

    def _render_removed_section(self, categorized, total_raw, limited):
        """Génère le HTML pour la section Suppressions"""
        html = ""
        total_films   = len(categorized['FILM'])
        total_series  = len(categorized['SERIE'])
        total_episodes= sum(len(v) for v in categorized['SERIE'].values())
        total_tv      = len(categorized['TV'])

        if total_films + total_series + total_tv == 0:
            return ""

        html += '<div class="section section-removed">'
        html += '<h2>🗑️ Suppressions</h2>'

        if limited:
            html += f'<div class="warning">⚠️ {total_raw} suppressions détectées au total. Seuls les {EMAIL_MAX_PER_SECTION} premiers de chaque catégorie sont affichés.</div>'

        html += f'''
        <div class="summary">
            <span class="summary-item">🎬 Films : <span class="count">{total_films}</span></span>
            <span class="summary-item">📺 Séries : <span class="count">{total_series}</span> ({total_episodes} épisodes)</span>
            <span class="summary-item">📡 Chaînes TV : <span class="count">{total_tv}</span></span>
        </div>'''

        if categorized['FILM']:
            html += '<h3>🎬 Films supprimés</h3><ul>'
            for e in sorted(categorized['FILM'], key=lambda x: x.display_name):
                html += f'<li>{e.display_name} {self._quality_badge(e.get_quality())}</li>'
            html += '</ul>'

        if categorized['SERIE']:
            html += '<h3>📺 Épisodes supprimés</h3>'
            for serie_name in sorted(categorized['SERIE'].keys()):
                eps = sorted(categorized['SERIE'][serie_name], key=lambda x: (x[0], x[1]))
                eps_list = ", ".join(f"{s}{e}" for s, e, _ in eps)
                html += f'<div class="serie-block"><strong>{serie_name}</strong><span class="serie-episodes"> → {eps_list}</span></div>'

        if categorized['TV']:
            html += '<h3>📡 Chaînes TV supprimées</h3><ul>'
            for e in sorted(categorized['TV'], key=lambda x: x.display_name):
                html += f'<li>{e.display_name} {self._quality_badge(e.get_quality())}</li>'
            html += '</ul>'

        html += '</div>'
        return html

    def _render_modified_section(self, categorized, total_raw, limited):
        """Génère le HTML pour la section Modifications d'URL"""
        html = ""
        total_films  = len(categorized['FILM'])
        total_series = len(categorized['SERIE'])
        total_tv     = len(categorized['TV'])

        if total_films + total_series + total_tv == 0:
            return ""

        html += '<div class="section section-modified">'
        html += '<h2>🔄 Modifications d\'URL</h2>'
        html += '<p class="section-desc">Ces contenus existent toujours mais leur URL de streaming a changé. Pensez à mettre à jour votre lecteur.</p>'

        if limited:
            html += f'<div class="warning">⚠️ {total_raw} modifications détectées. Seules les {EMAIL_MAX_PER_SECTION} premières de chaque catégorie sont affichées.</div>'

        html += f'''
        <div class="summary">
            <span class="summary-item">🎬 Films : <span class="count">{total_films}</span></span>
            <span class="summary-item">📺 Séries : <span class="count">{total_series} séries</span></span>
            <span class="summary-item">📡 Chaînes TV : <span class="count">{total_tv}</span></span>
        </div>'''

        if categorized['FILM']:
            html += '<h3>🎬 Films</h3><ul>'
            for prev, curr in sorted(categorized['FILM'], key=lambda x: x[1].display_name):
                html += f'<li>{curr.display_name} {self._quality_badge(curr.get_quality())}</li>'
            html += '</ul>'

        if categorized['SERIE']:
            html += '<h3>📺 Séries</h3>'
            for serie_name in sorted(categorized['SERIE'].keys()):
                eps = sorted(categorized['SERIE'][serie_name], key=lambda x: (x[0], x[1]))
                eps_list = ", ".join(f"{s}{e}" for s, e, _, __ in eps)
                html += f'<div class="serie-block"><strong>{serie_name}</strong><span class="serie-episodes"> → {eps_list}</span></div>'

        if categorized['TV']:
            html += '<h3>📡 Chaînes TV</h3><ul>'
            for prev, curr in sorted(categorized['TV'], key=lambda x: x[1].display_name):
                html += f'<li>{curr.display_name} {self._quality_badge(curr.get_quality())}</li>'
            html += '</ul>'

        html += '</div>'
        return html

    def generate_html_email(self, added_cat, removed_cat, modified_cat,
                             total_added, total_removed, total_modified,
                             limited_added, limited_removed, limited_modified):
        """Génère le contenu HTML complet de l'email"""

        css = """
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; color: #333;
                   max-width: 860px; margin: 0 auto; padding: 20px; background: #f4f4f4; }
            .container { background: white; border-radius: 8px; padding: 30px;
                         box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            h1 { color: #2c3e50; border-bottom: 3px solid #3498db;
                 padding-bottom: 10px; margin-bottom: 20px; }
            h2 { color: white; padding: 10px 15px; border-radius: 5px;
                 margin-top: 30px; margin-bottom: 15px; }
            h3 { color: #555; margin-top: 20px; margin-bottom: 8px;
                 border-left: 3px solid #ccc; padding-left: 10px; }
            .section { margin-bottom: 30px; border-radius: 6px;
                       border: 1px solid #e0e0e0; padding: 15px 20px; }
            .section-added   h2 { background: #27ae60; }
            .section-removed h2 { background: #c0392b; }
            .section-modified h2 { background: #e67e22; }
            .section-desc { color: #7f8c8d; font-style: italic; margin: -10px 0 10px 0; }
            .summary { background: #f8f9fa; padding: 12px 15px; border-radius: 5px;
                       margin-bottom: 15px; }
            .summary-item { display: inline-block; margin-right: 20px; font-weight: bold; }
            .count { color: #3498db; font-size: 1.1em; }
            ul { list-style: none; padding-left: 0; }
            li { padding: 7px 12px; margin-bottom: 4px; background: #f9f9f9;
                 border-left: 3px solid #bdc3c7; border-radius: 3px; }
            .section-added   li { border-left-color: #27ae60; }
            .section-removed li { border-left-color: #c0392b; }
            .section-modified li { border-left-color: #e67e22; }
            .quality { display: inline-block; padding: 1px 7px; border-radius: 3px;
                       font-size: 0.8em; font-weight: bold; margin-left: 8px; }
            .quality-4K, .quality-UHD { background: #e74c3c; color: white; }
            .quality-FHD               { background: #f39c12; color: white; }
            .quality-HD                { background: #3498db; color: white; }
            .quality-SD                { background: #95a5a6; color: white; }
            .serie-block { padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
            .serie-block:last-child { border-bottom: none; }
            .serie-episodes { color: #7f8c8d; font-size: 0.9em; }
            .global-summary { display: flex; gap: 15px; margin-bottom: 20px; }
            .gs-card { flex: 1; text-align: center; padding: 15px; border-radius: 6px; }
            .gs-card.green  { background: #eafaf1; border: 1px solid #27ae60; }
            .gs-card.red    { background: #fdedec; border: 1px solid #c0392b; }
            .gs-card.orange { background: #fef9e7; border: 1px solid #e67e22; }
            .gs-card .nb { font-size: 2em; font-weight: bold; }
            .gs-card.green  .nb { color: #27ae60; }
            .gs-card.red    .nb { color: #c0392b; }
            .gs-card.orange .nb { color: #e67e22; }
            .gs-card .label { font-size: 0.9em; color: #555; }
            .warning { background: #fff3cd; border-left: 4px solid #f39c12;
                       padding: 10px 15px; margin-bottom: 15px; border-radius: 4px; font-size: 0.9em; }
            .no-change { text-align: center; padding: 30px; color: #7f8c8d; }
            .footer { margin-top: 30px; padding-top: 15px; border-top: 1px solid #ddd;
                      color: #aaa; font-size: 0.85em; text-align: center; }
        </style>
        """

        body_parts = []
        body_parts.append(f"""
        <!DOCTYPE html><html><head><meta charset="UTF-8">{css}</head><body>
        <div class="container">
            <h1>📺 Rapport IPTV</h1>
            <p>Généré le <strong>{datetime.now().strftime("%d/%m/%Y à %H:%M")}</strong></p>
            <div class="global-summary">
                <div class="gs-card green">
                    <div class="nb">{total_added}</div>
                    <div class="label">🆕 Ajout(s)</div>
                </div>
                <div class="gs-card red">
                    <div class="nb">{total_removed}</div>
                    <div class="label">🗑️ Suppression(s)</div>
                </div>
                <div class="gs-card orange">
                    <div class="nb">{total_modified}</div>
                    <div class="label">🔄 Modification(s)</div>
                </div>
            </div>
        """)

        if total_added + total_removed + total_modified == 0:
            body_parts.append('<div class="no-change"><p>✅ Aucun changement détecté.</p></div>')
        else:
            body_parts.append(self._render_added_section(added_cat, total_added, limited_added))
            body_parts.append(self._render_removed_section(removed_cat, total_removed, limited_removed))
            body_parts.append(self._render_modified_section(modified_cat, total_modified, limited_modified))

        body_parts.append("""
            <div class="footer">Rapport généré automatiquement par IPTV Monitor</div>
        </div></body></html>""")

        return "".join(body_parts)

    def send_email(self, html_content, total_added, total_removed, total_modified):
        """Envoie l'email HTML aux destinataires"""
        try:
            msg = MIMEMultipart('alternative')

            # Sujet dynamique avec résumé
            parts = []
            if total_added:   parts.append(f"+{total_added}")
            if total_removed: parts.append(f"-{total_removed}")
            if total_modified:parts.append(f"~{total_modified}")
            summary_str = " | ".join(parts) if parts else "Aucun changement"

            msg['Subject'] = f"📺 IPTV [{summary_str}] – {datetime.now().strftime('%d/%m/%Y')}"
            msg['From']    = self.email_from
            msg['To']      = ', '.join(self.email_to)

            msg.attach(MIMEText(html_content, 'html', 'utf-8'))

            logger.info(f"Connexion SMTP {self.smtp_server}:{self.smtp_port}...")
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.info(f"Email envoyé à {len(self.email_to)} destinataire(s)")
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de l'email: {e}")
            raise

    def _limit_entries(self, entries, label):
        """Limite une liste à EMAIL_MAX_PER_SECTION entrées"""
        if len(entries) > EMAIL_MAX_PER_SECTION:
            logger.warning(f"⚠️  {len(entries)} {label} – limitation à {EMAIL_MAX_PER_SECTION} pour l'email")
            return entries[:EMAIL_MAX_PER_SECTION], True
        return entries, False

    def _limit_modifications(self, modifications, label):
        """Limite une liste de modifications"""
        if len(modifications) > EMAIL_MAX_PER_SECTION:
            logger.warning(f"⚠️  {len(modifications)} {label} – limitation à {EMAIL_MAX_PER_SECTION} pour l'email")
            return modifications[:EMAIL_MAX_PER_SECTION], True
        return modifications, False

    def run(self):
        """Exécute le processus complet de surveillance"""
        try:
            logger.info("=== Démarrage de IPTV Monitor ===")

            current_content      = self.download_playlist()
            self.current_entries = self.parse_m3u(current_content)
            self.previous_entries= self.load_previous_playlist()

            # ── Première exécution ────────────────────────────────────────────
            if not self.previous_entries:
                logger.info("⚠️  Première exécution : création de la référence, aucun email envoyé")
                self.save_playlist(current_content, CURRENT_LIST_FILE)
                self.save_playlist(current_content, PREVIOUS_LIST_FILE)
                logger.info("=== Fin de IPTV Monitor ===")
                return

            # ── Comparaison ───────────────────────────────────────────────────
            added, removed, modified = self.compare_playlists()

            total_added    = len(added)
            total_removed  = len(removed)
            total_modified = len(modified)

            if total_added + total_removed + total_modified == 0:
                logger.info("Aucun changement détecté")
            else:
                # Limitation pour l'email
                added_for_email,    limited_added    = self._limit_entries(added,    "ajouts")
                removed_for_email,  limited_removed  = self._limit_entries(removed,  "suppressions")
                modified_for_email, limited_modified = self._limit_modifications(modified, "modifications")

                # Catégorisation
                logger.info("Catégorisation des changements...")
                added_cat    = self.categorize_entries(added_for_email)
                removed_cat  = self.categorize_entries(removed_for_email)
                modified_cat = self.categorize_modifications(modified_for_email)

                # Génération et envoi de l'email
                logger.info("Génération de l'email...")
                html = self.generate_html_email(
                    added_cat, removed_cat, modified_cat,
                    total_added, total_removed, total_modified,
                    limited_added, limited_removed, limited_modified
                )
                self.send_email(html, total_added, total_removed, total_modified)

            # ── Rotation des fichiers ─────────────────────────────────────────
            if CURRENT_LIST_FILE.exists():
                CURRENT_LIST_FILE.rename(PREVIOUS_LIST_FILE)
                logger.info("Ancienne playlist archivée")
            self.save_playlist(current_content, CURRENT_LIST_FILE)

            logger.info("=== Fin de IPTV Monitor ===")

        except Exception as e:
            logger.error(f"Erreur lors de l'exécution: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    monitor = IPTVMonitor()
    monitor.run()