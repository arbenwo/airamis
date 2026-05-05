# Discord Weekly Active Voice Tracker Bot

Dieser Bot zählt aktive Voice-Zeit pro Woche.

Gezählt wird nur, wenn:

- der Zieluser in einem Voice-Channel ist
- mindestens eine weitere Person im selben Voice-Channel ist
- der Zieluser nicht gemutet ist, sofern `REQUIRE_UNMUTED=true`
- der Zieluser nicht deafened ist, sofern `REQUIRE_UNDEAFENED=true`

Zusätzlich:

- jeden Tag um 23:59 Uhr Europe/Berlin wird automatisch ein Report gesendet
- der User mit der hinterlegten ID wird markiert
- sonntags wird nach dem Report die Wochenzeit zurückgesetzt

## Commands

- `!voicetime` zeigt die aktive Voice-Zeit der aktuellen Woche
- `!session` zeigt die aktuelle aktive Session
- `!resetvoicetime` setzt die Zeit manuell zurück, nur Admins
- `!testreport` sendet testweise sofort einen Report, nur Admins

## Installation

```bash
pip install -r requirements.txt
```

## Konfiguration

Kopiere `.env.example` zu `.env`.

Windows:

```bash
copy .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

Beispiel:

```env
DISCORD_TOKEN=DEIN_BOT_TOKEN
TARGET_USER_ID=123456789012345678
REPORT_CHANNEL_ID=123456789012345678
COMMAND_PREFIX=!
DATABASE_PATH=voice_active_time.db

TIMEZONE=Europe/Berlin
REPORT_HOUR=23
REPORT_MINUTE=59
RESET_WEEKDAY=6

REQUIRE_UNMUTED=true
REQUIRE_UNDEAFENED=true
IGNORE_BOTS_AS_COMPANY=true
```

## Was muss in `.env` eingetragen werden?

### DISCORD_TOKEN

Der Bot-Token aus dem Discord Developer Portal.

### TARGET_USER_ID

Die User-ID der Person, deren aktive Voice-Zeit getrackt werden soll.

### REPORT_CHANNEL_ID

Die Channel-ID des Textchannels, in den der tägliche Report gesendet werden soll.

Discord Entwickler-Modus aktivieren:

Discord → Einstellungen → Erweitert → Entwicklermodus aktivieren.

Dann Rechtsklick auf den Textchannel → Kanal-ID kopieren.

## Uhrzeit und Kalenderlogik

Standard:

- täglicher Report: 23:59 Uhr Europe/Berlin
- Europe/Berlin berücksichtigt automatisch CET/CEST
- Wochenreset: Sonntag

`RESET_WEEKDAY=6` bedeutet Sonntag.

Python zählt so:

- Montag = 0
- Dienstag = 1
- Mittwoch = 2
- Donnerstag = 3
- Freitag = 4
- Samstag = 5
- Sonntag = 6

## Discord Developer Portal

Unter **Bot > Privileged Gateway Intents** aktivieren:

- Servermitglieder-Intent
- Nachrichteninhalt-Intent

Für Voice-State-Updates ist normalerweise kein Presence Intent nötig.

## Bot einladen

OAuth2 → URL Generator:

Scopes:

- `bot`

Bot-Berechtigungen:

- Kanäle ansehen
- Nachrichten senden
- Nachrichtenverlauf anzeigen
- Links einbetten

## Starten

```bash
python bot.py
```

## Test

In Discord:

```text
!voicetime
!testreport
```

`!testreport` sendet sofort den automatischen Tagesreport in den eingestellten `REPORT_CHANNEL_ID`.
