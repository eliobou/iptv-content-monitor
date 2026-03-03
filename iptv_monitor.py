#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPTV Monitor - Monitors IPTV playlist updates
Detects additions and removals, then sends an email summary.
Uses a local SQLite DB to distinguish real new content from playlist rebuilds.
"""
import re
import json
import sqlite3
import hashlib
import logging
import smtplib
from datetime import datetime, date, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import sys

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR            = Path(__file__).parent
CURRENT_LIST_FILE   = BASE_DIR / "current_playlist.m3u"
PREVIOUS_LIST_FILE  = BASE_DIR / "previous_playlist.m3u"
CONFIG_FILE         = BASE_DIR / "config.json"
LOG_FILE            = BASE_DIR / "iptv_monitor.log"
DB_FILE             = BASE_DIR / "iptv_history.db"

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Arabic detection helper
# ─────────────────────────────────────────────────────────────────────────────
# Unicode Arabic block: U+0600–U+06FF
_ARABIC_RE = re.compile(r'[\u0600-\u06FF]')

def _contains_arabic(text: str) -> bool:
    """Returns True if the string contains at least one Arabic character."""
    return bool(_ARABIC_RE.search(text))


# ─────────────────────────────────────────────────────────────────────────────
# M3U Entry
# ─────────────────────────────────────────────────────────────────────────────
class M3UEntry:
    """Represents a single entry in an M3U playlist file."""

    def __init__(self, extinf_line, url_line):
        self.raw_extinf = extinf_line
        self.url = url_line.strip()
        self._parse_extinf()

    def _parse_extinf(self):
        name_match = re.search(r'tvg-name="([^"]+)"', self.raw_extinf)
        self.tvg_name = name_match.group(1) if name_match else ""

        group_match = re.search(r'group-title="([^"]+)"', self.raw_extinf)
        self.group_title = group_match.group(1) if group_match else ""

        display_match = re.search(r',([^,]+)$', self.raw_extinf)
        self.display_name = display_match.group(1).strip() if display_match else self.tvg_name

    def has_arabic_name(self) -> bool:
        """Returns True if the entry's name contains Arabic characters."""
        return _contains_arabic(self.tvg_name) or _contains_arabic(self.display_name)

    def get_normalized_name(self):
        """Normalized name: strips quality tags and language markers for dedup / matching."""
        name = self.tvg_name
        name = re.sub(r'\b(SD|HD|FHD|4K|UHD|HDR|HEVC)\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\((FR|MULTI|EN|VF|VOSTFR|VO|AR)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def get_quality(self):
        quality_match = re.search(r'\b(SD|HD|FHD|4K|UHD)\b', self.tvg_name, re.IGNORECASE)
        return quality_match.group(1).upper() if quality_match else "SD"

    def get_content_type(self):
        """Returns 'TV', 'FILM' or 'SERIE'."""
        if re.search(r'S\d{2}\s*E\d{2}', self.tvg_name, re.IGNORECASE):
            return "SERIE"
        if '/movie/' not in self.url and '/series/' not in self.url:
            return "TV"
        return "FILM"

    def get_serie_info(self):
        """Returns (serie_name, season, episode) for SERIE entries, else (None, None, None)."""
        if self.get_content_type() != "SERIE":
            return None, None, None
        episode_match = re.search(r'(S\d{2})\s*(E\d{2})', self.tvg_name, re.IGNORECASE)
        if episode_match:
            season  = episode_match.group(1).upper()
            episode = episode_match.group(2).upper()
            serie_name = re.split(r'S\d{2}\s*E\d{2}', self.tvg_name, flags=re.IGNORECASE)[0]
            serie_name = re.sub(
                r'\((FR|MULTI|EN|AR)\)\s*(SD|HD|FHD|4K)?\s*$', '', serie_name
            ).strip()
            return serie_name, season, episode
        return None, None, None

    def get_unique_id(self):
        """
        Stable ID based on normalized name only.
        For series: based on serie_name + season + episode.
        """
        if self.get_content_type() == "SERIE":
            serie_name, season, episode = self.get_serie_info()
            if serie_name:
                normalized_serie = re.sub(
                    r'\b(SD|HD|FHD|4K|UHD|HDR|HEVC)\b', '', serie_name, flags=re.IGNORECASE
                )
                normalized_serie = re.sub(r'\s+', ' ', normalized_serie).strip().lower()
                return hashlib.md5(
                    f"{normalized_serie}_{season}_{episode}".encode()
                ).hexdigest()
        normalized = self.get_normalized_name().lower()
        return hashlib.md5(normalized.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# History Database  (bulk-optimized: one read pass + one write transaction)
# ─────────────────────────────────────────────────────────────────────────────
class HistoryDB:
    """
    SQLite database that tracks every piece of content ever seen.

    Table:
      - contents : one row per unique_id (first_seen, last_seen, appearance_count)

    No URL tracking — only presence/absence matters.

    Performance strategy:
      - load_all()         : pulls the entire contents table into a dict in one query
      - flush(cache, ...)  : writes all inserts/updates in a single transaction
      All per-entry logic runs in Python against the in-memory dict → no per-row SQL.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        # WAL mode: faster writes, no lock contention
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS contents (
                unique_id        TEXT PRIMARY KEY,
                normalized_name  TEXT NOT NULL,
                content_type     TEXT NOT NULL,
                first_seen       TEXT NOT NULL,   -- ISO date YYYY-MM-DD
                last_seen        TEXT NOT NULL,   -- ISO date YYYY-MM-DD
                appearance_count INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_contents_last_seen
                ON contents(last_seen);
        """)
        self.conn.commit()

    def load_all(self) -> dict:
        """
        Loads the entire contents table into a dict {unique_id: row_dict}.
        Call once before processing entries; pass the result to flush().
        """
        rows = self.conn.execute("SELECT * FROM contents").fetchall()
        logger.info(f"DB loaded: {len(rows)} known entries")
        return {r['unique_id']: dict(r) for r in rows}

    def flush(self, cache: dict, new_rows: list, updated_rows: list):
        """
        Writes all changes in a single transaction.

        Args:
            cache        : full in-memory DB (unused here, kept for clarity)
            new_rows     : list of dicts ready for INSERT into contents
            updated_rows : list of (last_seen, unique_id) for UPDATE
        """
        with self.conn:   # automatic commit / rollback
            if new_rows:
                self.conn.executemany(
                    """INSERT OR IGNORE INTO contents
                       (unique_id, normalized_name, content_type,
                        first_seen, last_seen, appearance_count)
                       VALUES (:unique_id, :normalized_name, :content_type,
                               :first_seen, :last_seen, 1)""",
                    new_rows
                )
            if updated_rows:
                self.conn.executemany(
                    """UPDATE contents
                       SET last_seen = ?,
                           appearance_count = appearance_count + 1
                       WHERE unique_id = ?""",
                    updated_rows
                )
        logger.info(
            f"DB flushed: {len(new_rows)} inserts, {len(updated_rows)} updates"
        )

    def get_truly_removed(self, today_str: str, absence_threshold_days: int) -> list:
        """
        Returns contents whose last_seen is exactly (today - absence_threshold_days).
        These are reported as truly removed today (threshold just crossed).
        """
        threshold_date = (
            datetime.strptime(today_str, "%Y-%m-%d").date()
            - timedelta(days=absence_threshold_days)
        ).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM contents WHERE last_seen = ?", (threshold_date,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# IPTV Monitor
# ─────────────────────────────────────────────────────────────────────────────
class IPTVMonitor:
    """Main class: downloads playlist, computes smart diff, sends email report."""

    def __init__(self):
        self._load_config()
        self.current_entries  = []
        self.previous_entries = []
        self.db = HistoryDB(DB_FILE)
        self.today_str = date.today().isoformat()

    def _load_config(self):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # ── Connection settings ───────────────────────────────────────────
            self.iptv_url      = config['iptv_url']
            self.smtp_server   = config['smtp_server']
            self.smtp_port     = config['smtp_port']
            self.smtp_user     = config['smtp_user']
            self.smtp_password = config['smtp_password']
            self.email_from    = config['email_from']
            self.email_to      = config['email_to']

            # ── Formerly hard-coded constants, now read from config ───────────
            self.email_max_per_section  = config.get('email_max_per_section', 1000)
            self.absence_threshold_days = config.get('absence_threshold_days', 3)

            # ── Display section toggles (default: new=on, removed=on) ─────────
            display = config.get('display', {})
            self.show_new     = display.get('show_new', True)
            self.show_removed = display.get('show_removed', True)

            # ── Arabic content filter ─────────────────────────────────────────
            self.filter_arabic = config.get('filter_arabic', False)

            logger.info(
                f"Configuration loaded — "
                f"show_new={self.show_new}, show_removed={self.show_removed}, "
                f"filter_arabic={self.filter_arabic}, "
                f"absence_threshold={self.absence_threshold_days}d, "
                f"email_max={self.email_max_per_section}"
            )
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise

    # ── Arabic filter helpers ─────────────────────────────────────────────────
    def _filter_arabic_entries(self, entries: list) -> list:
        """
        Removes M3UEntry objects whose name contains Arabic characters.
        Only applied when self.filter_arabic is True.
        """
        if not self.filter_arabic:
            return entries
        filtered = [e for e in entries if not e.has_arabic_name()]
        removed_count = len(entries) - len(filtered)
        if removed_count:
            logger.info(f"Arabic filter: removed {removed_count} entries from list")
        return filtered

    def _filter_arabic_db_rows(self, db_rows: list) -> list:
        """
        Removes DB row dicts whose normalized_name contains Arabic characters.
        Only applied when self.filter_arabic is True.
        """
        if not self.filter_arabic:
            return db_rows
        filtered = [r for r in db_rows if not _contains_arabic(r['normalized_name'])]
        removed_count = len(db_rows) - len(filtered)
        if removed_count:
            logger.info(f"Arabic filter: removed {removed_count} removed-content DB rows")
        return filtered

    # ── Download & parse ──────────────────────────────────────────────────────
    def _download_playlist(self):
        logger.info(f"Downloading playlist from {self.iptv_url[:50]}...")
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/91.0.4472.124 Safari/537.36'
            )
        }
        request = Request(self.iptv_url, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read().decode('utf-8')
            logger.info(f"Playlist downloaded: {len(content)} chars")
            return content
        except HTTPError as e:
            logger.error(f"HTTP error while downloading: {e.code} - {e.reason}")
            raise
        except URLError as e:
            logger.error(f"URL error while downloading: {e}")
            raise

    def _parse_m3u(self, content):
        entries = []
        lines = content.split('\n')
        logger.info(f"Parsing {len(lines)} lines...")
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
                            logger.info(f"  ... {count} entries parsed")
                        i += 2
                        continue
            i += 1
        logger.info(f"Parsing done: {len(entries)} entries")
        return entries

    def _save_playlist(self, content, filepath):
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Playlist saved: {filepath}")

    def _load_previous_playlist(self):
        if PREVIOUS_LIST_FILE.exists():
            with open(PREVIOUS_LIST_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            return self._parse_m3u(content)
        return []

    # ── Smart diff using DB ───────────────────────────────────────────────────
    def _compute_smart_diff(self):
        """
        Bulk diff strategy (O(n) Python, 2 SQL round-trips total):
          1. Load entire DB into memory (1 SELECT)
          2. Diff all current entries against in-memory dict
          3. Write all changes in one transaction (1 bulk INSERT/UPDATE)

        Returns (truly_new, truly_removed):
          - truly_new    : list of M3UEntry  — never seen OR absent > threshold
          - truly_removed: list of DB row dicts — threshold crossed today
        """
        logger.info("Computing smart diff against history DB (bulk mode)...")

        # ── Step 1: load full DB cache in one query ───────────────────────────
        db_cache = self.db.load_all()

        today_date = date.today()

        truly_new    = []
        new_rows     = []   # for DB INSERT
        updated_rows = []   # for DB UPDATE (last_seen, unique_id)

        for entry in self.current_entries:
            uid      = entry.get_unique_id()
            existing = db_cache.get(uid)

            if existing is None:
                # ── Brand new: never in DB ────────────────────────────────────
                truly_new.append(entry)
                new_row = {
                    'unique_id':       uid,
                    'normalized_name': entry.get_normalized_name(),
                    'content_type':    entry.get_content_type(),
                    'first_seen':      self.today_str,
                    'last_seen':       self.today_str,
                }
                new_rows.append(new_row)
                # Update cache so duplicate entries in the same playlist don't
                # produce double inserts
                db_cache[uid] = new_row
            else:
                # ── Known content: check days absent ─────────────────────────
                last_seen_date = datetime.strptime(existing['last_seen'], "%Y-%m-%d").date()
                days_absent    = (today_date - last_seen_date).days

                if days_absent > self.absence_threshold_days:
                    # Gone long enough → treat as new again
                    truly_new.append(entry)

                # Always update last_seen in DB
                updated_rows.append((self.today_str, uid))
                # Refresh cache entry so dupes within the same playlist are idempotent
                existing['last_seen'] = self.today_str

        # ── Step 2: flush all changes in one transaction ──────────────────────
        self.db.flush(db_cache, new_rows, updated_rows)

        # ── Truly removed: one targeted SELECT ───────────────────────────────
        truly_removed = self.db.get_truly_removed(self.today_str, self.absence_threshold_days)

        logger.info(
            f"Smart diff done: {len(truly_new)} new, {len(truly_removed)} truly removed"
        )
        return truly_new, truly_removed

    # ── Categorization ────────────────────────────────────────────────────────
    @staticmethod
    def _compare_quality(q1, q2):
        order = {'SD': 1, 'HD': 2, 'FHD': 3, '4K': 4, 'UHD': 5}
        return order.get(q1, 0) - order.get(q2, 0)

    def _categorize_entries(self, entries):
        """Groups entries into TV / FILM / SERIE, deduplicating by quality."""
        categorized = {'TV': [], 'FILM': [], 'SERIE': {}}
        for entry in entries:
            ctype = entry.get_content_type()
            if ctype in ('TV', 'FILM'):
                bucket = categorized[ctype]
                norm   = entry.get_normalized_name()
                existing = next(
                    (e for e in bucket if e.get_normalized_name() == norm), None
                )
                if existing:
                    if self._compare_quality(entry.get_quality(), existing.get_quality()) > 0:
                        bucket.remove(existing)
                        bucket.append(entry)
                else:
                    bucket.append(entry)
            elif ctype == "SERIE":
                serie_name, season, episode = entry.get_serie_info()
                if serie_name:
                    categorized['SERIE'].setdefault(serie_name, [])
                    categorized['SERIE'][serie_name].append((season, episode, entry))
        return categorized

    def _categorize_removed_db_rows(self, db_rows):
        """Groups DB rows (dicts) into TV / FILM / SERIE."""
        categorized = {'TV': [], 'FILM': [], 'SERIE': {}}
        for row in db_rows:
            ctype = row['content_type']
            if ctype in ('TV', 'FILM'):
                categorized[ctype].append(row)
            elif ctype == 'SERIE':
                name = row['normalized_name']
                ep_match = re.search(r'(S\d{2})\s*(E\d{2})', name, re.IGNORECASE)
                if ep_match:
                    season     = ep_match.group(1).upper()
                    episode    = ep_match.group(2).upper()
                    serie_name = re.split(
                        r'S\d{2}\s*E\d{2}', name, flags=re.IGNORECASE
                    )[0].strip()
                else:
                    serie_name = name
                    season = episode = "??"
                categorized['SERIE'].setdefault(serie_name, [])
                categorized['SERIE'][serie_name].append((season, episode, row))
        return categorized

    # ── Limit helpers ─────────────────────────────────────────────────────────
    def _limit(self, lst, label):
        if len(lst) > self.email_max_per_section:
            logger.warning(
                f"⚠  {len(lst)} {label} — limiting to {self.email_max_per_section} for email"
            )
            return lst[:self.email_max_per_section], True
        return lst, False

    # ── HTML rendering ────────────────────────────────────────────────────────
    @staticmethod
    def _quality_badge(quality):
        return f'<span class="quality quality-{quality}">{quality}</span>'

    def _render_new_section(self, categorized, total_raw, limited):
        total_films    = len(categorized['FILM'])
        total_series   = len(categorized['SERIE'])
        total_episodes = sum(len(v) for v in categorized['SERIE'].values())
        total_tv       = len(categorized['TV'])
        if total_films + total_series + total_tv == 0:
            return ""

        html  = '<div class="section section-added">'
        html += '<h2>🆕 Nouveaux contenus</h2>'
        html += (
            '<p class="section-desc">'
            'Contenus jamais vus ou absents depuis plus de '
            f'{self.absence_threshold_days} jours.'
            '</p>'
        )
        if limited:
            html += (
                f'<div class="warning">⚠️ {total_raw} ajouts détectés au total. '
                f'Seuls les {self.email_max_per_section} premiers de chaque catégorie '
                f'sont affichés.</div>'
            )
        html += f'''
        <div class="summary">
            <span class="summary-item">🎬 Films : <span class="count">{total_films}</span></span>
            <span class="summary-item">📺 Séries : <span class="count">{total_series}</span>
                ({total_episodes} épisodes)</span>
            <span class="summary-item">📡 Chaînes TV : <span class="count">{total_tv}</span></span>
        </div>'''

        if categorized['FILM']:
            html += '<h3>🎬 Films</h3><ul>'
            for e in sorted(categorized['FILM'], key=lambda x: x.display_name):
                html += f'<li>{e.display_name} {self._quality_badge(e.get_quality())}</li>'
            html += '</ul>'

        if categorized['SERIE']:
            html += '<h3>📺 Séries</h3>'
            for serie_name in sorted(categorized['SERIE']):
                eps = sorted(categorized['SERIE'][serie_name], key=lambda x: (x[0], x[1]))
                eps_list = ", ".join(f"{s}{e}" for s, e, _ in eps)
                html += (
                    f'<div class="serie-block"><strong>{serie_name}</strong>'
                    f'<span class="serie-episodes"> → {eps_list}</span></div>'
                )

        if categorized['TV']:
            html += '<h3>📡 Chaînes TV</h3><ul>'
            for e in sorted(categorized['TV'], key=lambda x: x.display_name):
                html += f'<li>{e.display_name} {self._quality_badge(e.get_quality())}</li>'
            html += '</ul>'

        html += '</div>'
        return html

    def _render_removed_section(self, categorized, total_raw, limited):
        total_films    = len(categorized['FILM'])
        total_series   = len(categorized['SERIE'])
        total_episodes = sum(len(v) for v in categorized['SERIE'].values())
        total_tv       = len(categorized['TV'])
        if total_films + total_series + total_tv == 0:
            return ""

        html  = '<div class="section section-removed">'
        html += '<h2>🗑️ Suppressions confirmées</h2>'
        html += (
            '<p class="section-desc">'
            f'Absents depuis {self.absence_threshold_days} jours consécutifs.'
            '</p>'
        )
        if limited:
            html += (
                f'<div class="warning">⚠️ {total_raw} suppressions détectées. '
                f'Seules les {self.email_max_per_section} premières sont affichées.</div>'
            )
        html += f'''
        <div class="summary">
            <span class="summary-item">🎬 Films : <span class="count">{total_films}</span></span>
            <span class="summary-item">📺 Séries : <span class="count">{total_series}</span>
                ({total_episodes} épisodes)</span>
            <span class="summary-item">📡 Chaînes TV : <span class="count">{total_tv}</span></span>
        </div>'''

        if categorized['FILM']:
            html += '<h3>🎬 Films supprimés</h3><ul>'
            for row in sorted(categorized['FILM'], key=lambda x: x['normalized_name']):
                first = row['first_seen']
                html += f'<li>{row["normalized_name"]} <small>(vu depuis le {first})</small></li>'
            html += '</ul>'

        if categorized['SERIE']:
            html += '<h3>📺 Épisodes supprimés</h3>'
            for serie_name in sorted(categorized['SERIE']):
                eps = sorted(categorized['SERIE'][serie_name], key=lambda x: (x[0], x[1]))
                eps_list = ", ".join(f"{s}{e}" for s, e, _ in eps)
                html += (
                    f'<div class="serie-block"><strong>{serie_name}</strong>'
                    f'<span class="serie-episodes"> → {eps_list}</span></div>'
                )

        if categorized['TV']:
            html += '<h3>📡 Chaînes TV supprimées</h3><ul>'
            for row in sorted(categorized['TV'], key=lambda x: x['normalized_name']):
                html += f'<li>{row["normalized_name"]}</li>'
            html += '</ul>'

        html += '</div>'
        return html

    def _generate_html_email(
        self,
        new_cat, removed_cat,
        total_new, total_removed,
        limited_new, limited_removed
    ):
        css = """
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; color: #333;
                   max-width: 860px; margin: 0 auto; padding: 20px; background: #f4f4f4; }
            .container { background: white; border-radius: 8px; padding: 30px;
                         box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            h1 { color: #2c3e50; border-bottom: 3px solid #3498db;
                 padding-bottom: 10px; margin-bottom: 20px; }
            h2 { color: white; padding: 10px 15px; border-radius: 5px;
                 margin-top: 30px; margin-bottom: 5px; }
            h3 { color: #555; margin-top: 20px; margin-bottom: 8px;
                 border-left: 3px solid #ccc; padding-left: 10px; }
            .section { margin-bottom: 30px; border-radius: 6px;
                       border: 1px solid #e0e0e0; padding: 15px 20px; }
            .section-added   h2 { background: #27ae60; }
            .section-removed h2 { background: #c0392b; }
            .section-desc { color: #7f8c8d; font-style: italic;
                            margin: 4px 0 10px 0; font-size: 0.9em; }
            .summary { background: #f8f9fa; padding: 12px 15px; border-radius: 5px;
                       margin-bottom: 15px; }
            .summary-item { display: inline-block; margin-right: 20px; font-weight: bold; }
            .count { color: #3498db; font-size: 1.1em; }
            ul { list-style: none; padding-left: 0; }
            li { padding: 7px 12px; margin-bottom: 4px; background: #f9f9f9;
                 border-left: 3px solid #bdc3c7; border-radius: 3px; }
            .section-added   li { border-left-color: #27ae60; }
            .section-removed li { border-left-color: #c0392b; }
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
            .gs-card.green { background: #eafaf1; border: 1px solid #27ae60; }
            .gs-card.red   { background: #fdedec; border: 1px solid #c0392b; }
            .gs-card .nb { font-size: 2em; font-weight: bold; }
            .gs-card.green .nb { color: #27ae60; }
            .gs-card.red   .nb { color: #c0392b; }
            .gs-card .label { font-size: 0.9em; color: #555; }
            .warning { background: #fff3cd; border-left: 4px solid #f39c12;
                       padding: 10px 15px; margin-bottom: 15px;
                       border-radius: 4px; font-size: 0.9em; }
            .no-change { text-align: center; padding: 30px; color: #7f8c8d; }
            .footer { margin-top: 30px; padding-top: 15px; border-top: 1px solid #ddd;
                      color: #aaa; font-size: 0.85em; text-align: center; }
            small { color: #999; font-size: 0.8em; }
        </style>
        """

        display_new     = total_new     if self.show_new     else "—"
        display_removed = total_removed if self.show_removed else "—"

        parts = [f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{css}</head>
        <body><div class="container">
            <h1>📺 Rapport IPTV</h1>
            <p>Généré le <strong>{datetime.now().strftime("%d/%m/%Y à %H:%M")}</strong></p>
            <div class="global-summary">
                <div class="gs-card green">
                    <div class="nb">{display_new}</div>
                    <div class="label">🆕 Nouveau(x)</div>
                </div>
                <div class="gs-card red">
                    <div class="nb">{display_removed}</div>
                    <div class="label">🗑️ Supprimé(s)</div>
                </div>
            </div>
        """]

        visible_total = (
            (total_new     if self.show_new     else 0) +
            (total_removed if self.show_removed else 0)
        )

        if visible_total == 0:
            parts.append(
                '<div class="no-change"><p>✅ Aucun changement significatif détecté.</p>'
                f'<p><small>Les contenus qui disparaissent et réapparaissent dans '
                f'moins de {self.absence_threshold_days} jours sont ignorés.</small></p></div>'
            )
        else:
            if self.show_new:
                parts.append(self._render_new_section(new_cat, total_new, limited_new))
            if self.show_removed:
                parts.append(self._render_removed_section(removed_cat, total_removed, limited_removed))

        parts.append(
            '<div class="footer">Rapport généré automatiquement par IPTV Monitor'
            f'<br><small>Seuil de détection : {self.absence_threshold_days} jours</small>'
            '</div></div></body></html>'
        )
        return "".join(parts)

    # ── Email sending ─────────────────────────────────────────────────────────
    def _send_email(self, html_content, total_new, total_removed):
        try:
            msg = MIMEMultipart('alternative')
            parts = []
            if total_new     and self.show_new:     parts.append(f"+{total_new}")
            if total_removed and self.show_removed: parts.append(f"-{total_removed}")
            summary_str = " | ".join(parts) if parts else "Aucun changement"
            msg['Subject'] = (
                f"📺 IPTV [{summary_str}] – "
                f"{datetime.now().strftime('%d/%m/%Y')}"
            )
            msg['From'] = self.email_from
            msg['To']   = ', '.join(self.email_to)
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))

            logger.info(f"Connecting to SMTP {self.smtp_server}:{self.smtp_port}...")
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            logger.info(f"Email sent to {len(self.email_to)} recipient(s)")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            raise

    # ── Main entry point ──────────────────────────────────────────────────────
    def run(self):
        try:
            logger.info("=== IPTV Monitor starting ===")

            current_content       = self._download_playlist()
            self.current_entries  = self._parse_m3u(current_content)
            self.previous_entries = self._load_previous_playlist()

            # ── First run: build DB reference, no email ───────────────────────
            if not self.previous_entries:
                logger.info("⚠  First run: building reference DB, no email sent")
                db_cache  = self.db.load_all()
                new_rows  = []
                for entry in self.current_entries:
                    uid = entry.get_unique_id()
                    if uid not in db_cache:
                        new_row = {
                            'unique_id':       uid,
                            'normalized_name': entry.get_normalized_name(),
                            'content_type':    entry.get_content_type(),
                            'first_seen':      self.today_str,
                            'last_seen':       self.today_str,
                        }
                        new_rows.append(new_row)
                        db_cache[uid] = new_row
                self.db.flush(db_cache, new_rows, [])
                self._save_playlist(current_content, CURRENT_LIST_FILE)
                self._save_playlist(current_content, PREVIOUS_LIST_FILE)
                logger.info("=== IPTV Monitor done ===")
                return

            # ── Smart diff ────────────────────────────────────────────────────
            truly_new, truly_removed = self._compute_smart_diff()

            # ── Apply Arabic filter (before counting) ─────────────────────────
            truly_new     = self._filter_arabic_entries(truly_new)
            truly_removed = self._filter_arabic_db_rows(truly_removed)

            total_new     = len(truly_new)
            total_removed = len(truly_removed)

            # ── Limit for email ───────────────────────────────────────────────
            new_for_email,     limited_new     = self._limit(truly_new,     "new items")
            removed_for_email, limited_removed = self._limit(truly_removed, "removals")

            # ── Categorize ────────────────────────────────────────────────────
            logger.info("Categorizing changes...")
            new_cat     = self._categorize_entries(new_for_email)
            removed_cat = self._categorize_removed_db_rows(removed_for_email)

            # ── Generate and send email ───────────────────────────────────────
            logger.info("Generating email...")
            html = self._generate_html_email(
                new_cat, removed_cat,
                total_new, total_removed,
                limited_new, limited_removed
            )
            self._send_email(html, total_new, total_removed)

            # ── Rotate playlist files ─────────────────────────────────────────
            if CURRENT_LIST_FILE.exists():
                CURRENT_LIST_FILE.rename(PREVIOUS_LIST_FILE)
                logger.info("Previous playlist archived")
            self._save_playlist(current_content, CURRENT_LIST_FILE)

            logger.info("=== IPTV Monitor done ===")

        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            raise
        finally:
            self.db.close()


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    monitor = IPTVMonitor()
    monitor.run()