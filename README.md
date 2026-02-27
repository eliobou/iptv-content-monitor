# IPTV Content Monitor

A Python script that automatically monitors your M3U IPTV playlist, detects new content and sends you a daily HTML email summary.

---

## Features

- ✅ Automatic playlist download and parsing
- ✅ Smart new-content detection via local SQLite history database
- ✅ Filters out provider playlist rebuilds (no thousands of false positives)
- ✅ Confirmed removal alerts after a configurable number of consecutive days of absence
- ✅ URL-change detection (same content, new streaming address)
- ✅ Quality deduplication — same film in SD/HD/FHD/4K counted once, best quality shown
- ✅ Auto-categorisation: Films, Series, TV Channels
- ✅ Responsive HTML email with colour-coded quality badges
- ✅ Configurable email sections — independently enable/disable New, Removed and URL-update sections
- ✅ Optional Arabic content filter — silently excludes entries with Arabic names from all sections and counters
- ✅ Multiple recipients support
- ✅ Scheduled execution via cron

---

## How It Works

### The Problem with Naive Diffing

IPTV providers frequently rebuild their entire playlist from scratch — sometimes removing 10,000+ entries one day and re-adding them the next with different streaming URLs. A simple file comparison produces enormous false-positive reports full of "new" and "removed" content that hasn't actually changed.

### Smart Detection

IPTV Monitor maintains a local SQLite database of every content item ever seen. On each run it computes a **smart diff** with the following rules:

| Event | Condition | Email report |
|-------|-----------|--------------|
| 🆕 New content | Never seen in DB | ✅ Shown |
| 🆕 Returning content | Absent for more than `absence_threshold_days` days | ✅ Shown |
| 🔄 URL update | Present yesterday & today, URL changed | ✅ Shown |
| 🗑️ True removal | Absent for exactly `absence_threshold_days` consecutive days | ✅ Shown |
| *(silence)* | Disappears & reappears within threshold, same URL | ❌ Ignored |

### Content Identification

Each item is assigned a stable ID based on its **normalised name** — quality tags (SD, HD, FHD, 4K) and language markers (FR, MULTI, EN…) are stripped, and matching is case-insensitive. `"Avatar (MULTI) FHD 2023"` and `"Avatar HD"` are treated as the same film. For series, the ID is based on show name + season + episode number.

---

## Requirements

- Python 3.7+
- A Gmail account with an [App Password](https://myaccount.google.com/apppasswords)

---

## Installation

```bash
# Clone repo

# Make iptv_monitor.py executable
chmod +x iptv_monitor.py
```

---

## Configuration

- Update `config.json` :

| Key                        | Default | Description                                                                                                     |
| -------------------------- | ------- | --------------------------------------------------------------------------------------------------------------- |
| `absence_threshold_days`   | `3`     | Days of absence before content is considered truly removed or truly new on return                               |
| `email_max_per_section`    | `1000`  | Maximum number of entries displayed per section in the email                                                    |
| `display.show_new`         | `true`  | Include the New Content section in the email                                                                    |
| `display.show_removed`     | `true`  | Include the Confirmed Removals section in the email                                                             |
| `display.show_url_updates` | `false` | Include the URL Updates section in the email                                                                    |
| `filter_arabic`            | `false` | When `true`, entries whose name contains Arabic characters are silently excluded from all sections and counters |

> ⚠️ **This file contains your passwords — never commit it.**
> ```bash
> chmod 600 config.json
> ```

---

## Running

```bash
sudo python3 iptv_monitor.py
```

**First run:** builds the history database, no email sent.  
**Subsequent runs:** compares against history, sends email if anything changed.

---

## Scheduled Execution (cron)

```bash
crontab -e
```

Add this line to run every day at 7:00 AM:

```cron
0 7 * * * /usr/bin/python3 /home/pi/update_iptv/iptv_monitor.py >> /home/pi/update_iptv/cron.log 2>&1
```

---

## File Structure

```
/update_iptv/
├── iptv_monitor.py          # Main script
├── config.json              # Configuration (⚠️ contains passwords — do not commit)
├── iptv_history.db          # SQLite history database (auto-created)
├── current_playlist.m3u     # Latest downloaded playlist
├── previous_playlist.m3u    # Previous playlist
├── iptv_monitor.log         # Script logs
└── cron.log                 # Cron execution logs
```

Recommended `.gitignore`:

```
config.json
*.m3u
*.db
*.db-wal
*.db-shm
*.log
```

---

## Email Report

The HTML email contains up to three sections, each independently toggleable via `config.json`:

- **🆕 New content** — films, series episodes and TV channels never seen before or returning after the configured threshold
- **🗑️ Confirmed removals** — content absent for the configured number of consecutive days
- **🔄 URL updates** — same content, new streaming address (disabled by default)

Each section groups content by type (Films / Series / TV Channels) and shows quality badges (SD / HD / FHD / 4K). The number of items per section is capped by `email_max_per_section`.
