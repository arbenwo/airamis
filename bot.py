import os
import sqlite3
import logging
import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv


# -----------------------------
# Konfiguration & Logging
# -----------------------------

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_USER_ID_RAW = os.getenv("TARGET_USER_ID")
REPORT_CHANNEL_ID_RAW = os.getenv("REPORT_CHANNEL_ID")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
DATABASE_PATH = os.getenv("DATABASE_PATH", "voice_active_time.db")

# Europe/Berlin berücksichtigt automatisch CET/CEST.
LOCAL_TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Berlin"))

# Jeden Tag um 23:59 lokale Zeit Bericht senden.
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "23"))
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "59"))

# Sonntag = 6 bei Python weekday(): Montag=0, Sonntag=6
RESET_WEEKDAY = int(os.getenv("RESET_WEEKDAY", "6"))

# Wenn true, zählt Zeit NICHT, wenn der User server-muted oder self-muted ist.
REQUIRE_UNMUTED = os.getenv("REQUIRE_UNMUTED", "true").lower() == "true"

# Wenn true, zählt Zeit NICHT, wenn der User deafened ist.
REQUIRE_UNDEAFENED = os.getenv("REQUIRE_UNDEAFENED", "true").lower() == "true"

# Wenn true, werden Bots als weitere Personen im Channel ignoriert.
IGNORE_BOTS_AS_COMPANY = os.getenv("IGNORE_BOTS_AS_COMPANY", "true").lower() == "true"

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env Datei.")

if not TARGET_USER_ID_RAW:
    raise RuntimeError("TARGET_USER_ID fehlt in der .env Datei.")

if not REPORT_CHANNEL_ID_RAW:
    raise RuntimeError("REPORT_CHANNEL_ID fehlt in der .env Datei.")

try:
    TARGET_USER_ID = int(TARGET_USER_ID_RAW)
except ValueError:
    raise RuntimeError("TARGET_USER_ID muss eine Discord User-ID als Zahl sein.")

try:
    REPORT_CHANNEL_ID = int(REPORT_CHANNEL_ID_RAW)
except ValueError:
    raise RuntimeError("REPORT_CHANNEL_ID muss eine Discord Channel-ID als Zahl sein.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("voice-weekly-tracker")


# -----------------------------
# Hilfsfunktionen
# -----------------------------

def utc_now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))

    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)

    parts = []
    if days:
        parts.append(f"{days} Tag{'e' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} Stunde{'n' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} Minute{'n' if minutes != 1 else ''}")
    if secs or not parts:
        parts.append(f"{secs} Sekunde{'n' if secs != 1 else ''}")

    return ", ".join(parts)


def next_report_datetime(now: Optional[datetime] = None) -> datetime:
    """
    Nächster täglicher Bericht um REPORT_HOUR:REPORT_MINUTE in Europe/Berlin.
    """
    if now is None:
        now = local_now()

    target_today = datetime.combine(
        now.date(),
        time(hour=REPORT_HOUR, minute=REPORT_MINUTE),
        tzinfo=LOCAL_TZ,
    )

    if now < target_today:
        return target_today

    return target_today + timedelta(days=1)


# -----------------------------
# Datenbank
# -----------------------------

class ActiveVoiceTimeStore:
    """
    Speichert:
    - total_seconds: abgeschlossene aktive Voice-Zeit der aktuellen Woche
    - current_session_start: Startzeit der aktuellen aktiven Session oder NULL
    - last_report_date: Datum des letzten Auto-Berichts, gegen doppelte Berichte
    - last_weekly_reset_date: Datum des letzten Wochen-Resets
    """

    def __init__(self, db_path: str, target_user_id: int):
        self.db_path = db_path
        self.target_user_id = target_user_id
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS active_voice_time (
                    user_id INTEGER PRIMARY KEY,
                    total_seconds INTEGER NOT NULL DEFAULT 0,
                    current_session_start INTEGER
                )
                """
            )
            con.execute(
                """
                INSERT OR IGNORE INTO active_voice_time (
                    user_id,
                    total_seconds,
                    current_session_start
                )
                VALUES (?, 0, NULL)
                """,
                (self.target_user_id,),
            )

            con.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            con.commit()

    def get_state(self, key: str) -> Optional[str]:
        with self._connect() as con:
            row = con.execute(
                "SELECT value FROM bot_state WHERE key = ?",
                (key,),
            ).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO bot_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            con.commit()

    def get_session_start(self) -> Optional[int]:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT current_session_start
                FROM active_voice_time
                WHERE user_id = ?
                """,
                (self.target_user_id,),
            ).fetchone()

        return row[0] if row else None

    def get_total_seconds(self, include_current_session: bool = True) -> int:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT total_seconds, current_session_start
                FROM active_voice_time
                WHERE user_id = ?
                """,
                (self.target_user_id,),
            ).fetchone()

        if not row:
            return 0

        total_seconds, session_start = row

        if include_current_session and session_start is not None:
            total_seconds += max(0, utc_now_ts() - int(session_start))

        return int(total_seconds)

    def start_session(self) -> bool:
        now = utc_now_ts()

        with self._connect() as con:
            row = con.execute(
                """
                SELECT current_session_start
                FROM active_voice_time
                WHERE user_id = ?
                """,
                (self.target_user_id,),
            ).fetchone()

            if row and row[0] is not None:
                return False

            con.execute(
                """
                UPDATE active_voice_time
                SET current_session_start = ?
                WHERE user_id = ?
                """,
                (now, self.target_user_id),
            )
            con.commit()

        return True

    def end_session(self) -> int:
        now = utc_now_ts()

        with self._connect() as con:
            row = con.execute(
                """
                SELECT total_seconds, current_session_start
                FROM active_voice_time
                WHERE user_id = ?
                """,
                (self.target_user_id,),
            ).fetchone()

            if not row:
                return 0

            total_seconds, session_start = row

            if session_start is None:
                return 0

            added_seconds = max(0, now - int(session_start))
            new_total = int(total_seconds) + added_seconds

            con.execute(
                """
                UPDATE active_voice_time
                SET total_seconds = ?, current_session_start = NULL
                WHERE user_id = ?
                """,
                (new_total, self.target_user_id),
            )
            con.commit()

        return added_seconds

    def reset_keep_running_session_if_needed(self, should_count_after_reset: bool) -> None:
        """
        Reset am Sonntag nach dem Bericht.

        Wenn der User genau beim Reset weiterhin aktiv ist, startet danach direkt
        eine neue Wochen-Session ab Reset-Zeit. Dadurch geht keine Zeit verloren,
        und die neue Woche beginnt sauber.
        """
        new_session_start = utc_now_ts() if should_count_after_reset else None

        with self._connect() as con:
            con.execute(
                """
                UPDATE active_voice_time
                SET total_seconds = 0,
                    current_session_start = ?
                WHERE user_id = ?
                """,
                (new_session_start, self.target_user_id),
            )
            con.commit()


# -----------------------------
# Discord Bot
# -----------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)
store = ActiveVoiceTimeStore(DATABASE_PATH, TARGET_USER_ID)


def other_people_in_channel(channel: discord.abc.Connectable, target_id: int) -> int:
    count = 0

    for member in channel.members:
        if member.id == target_id:
            continue

        if IGNORE_BOTS_AS_COMPANY and member.bot:
            continue

        count += 1

    return count


def is_target_actively_countable(member: discord.Member) -> bool:
    """
    True, wenn der Zieluser aktiv gezählt werden soll:

    - User ist in einem Voice-Channel
    - Mindestens eine weitere Person ist im selben Channel
    - Optional: User ist nicht gemutet
    - Optional: User ist nicht deafened
    """
    voice = member.voice

    if voice is None or voice.channel is None:
        return False

    if REQUIRE_UNMUTED and (voice.self_mute or voice.mute):
        return False

    if REQUIRE_UNDEAFENED and (voice.self_deaf or voice.deaf):
        return False

    return other_people_in_channel(voice.channel, member.id) >= 1


async def find_target_member() -> Optional[discord.Member]:
    for guild in bot.guilds:
        member = guild.get_member(TARGET_USER_ID)
        if member:
            return member
    return None


async def reevaluate_tracking(reason: str = "") -> None:
    member = await find_target_member()

    if member is None:
        logger.warning(
            "Zieluser wurde in keiner gemeinsamen Guild gefunden. "
            "Bot und Zieluser müssen auf demselben Server sein."
        )
        return

    should_count = is_target_actively_countable(member)
    session_running = store.get_session_start() is not None

    logger.info(
        "Reevaluate Tracking | reason=%s | should_count=%s | session_running=%s",
        reason,
        should_count,
        session_running,
    )

    if should_count and not session_running:
        started = store.start_session()
        if started:
            logger.info("Aktive Voice-Session gestartet.")

    elif not should_count and session_running:
        added = store.end_session()
        logger.info("Aktive Voice-Session beendet. Hinzugefügt: %s Sekunden.", added)


async def send_daily_report_and_maybe_reset() -> None:
    """
    Sendet täglich den Bericht in REPORT_CHANNEL_ID.
    Sonntags wird nach dem Bericht zurückgesetzt.
    """
    today = local_now().date().isoformat()

    # Schutz gegen doppelte Ausführung am selben Datum.
    if store.get_state("last_report_date") == today:
        logger.info("Tagesbericht für %s wurde bereits gesendet.", today)
        return

    channel = bot.get_channel(REPORT_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(REPORT_CHANNEL_ID)
        except discord.DiscordException:
            logger.exception("Report-Channel konnte nicht gefunden werden.")
            return

    total_seconds = store.get_total_seconds(include_current_session=True)
    session_start = store.get_session_start()
    member = await find_target_member()
    should_count_after_reset = bool(member and is_target_actively_countable(member))

    date_text = local_now().strftime("%d.%m.%Y")
    weekday_text = local_now().strftime("%A")

    embed = discord.Embed(
        title="Täglicher Voice-Zeit Report",
        description=f"Ausgabe wie `!voicetime` für <@{TARGET_USER_ID}>",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Datum", value=date_text, inline=False)
    embed.add_field(name="User", value=f"<@{TARGET_USER_ID}>", inline=False)
    embed.add_field(
        name="Aktive Voice-Zeit diese Woche",
        value=format_duration(total_seconds),
        inline=False,
    )
    embed.add_field(
        name="Aktuelle Session",
        value="läuft gerade" if session_start is not None else "nicht aktiv",
        inline=False,
    )

    is_reset_day = local_now().weekday() == RESET_WEEKDAY

    if is_reset_day:
        embed.add_field(
            name="Wochenreset",
            value="Heute ist Sonntag. Die Zeit wird nach diesem Report zurückgesetzt.",
            inline=False,
        )

    await channel.send(content=f"<@{TARGET_USER_ID}>", embed=embed)
    store.set_state("last_report_date", today)

    if is_reset_day:
        if store.get_state("last_weekly_reset_date") != today:
            store.reset_keep_running_session_if_needed(should_count_after_reset)
            store.set_state("last_weekly_reset_date", today)
            await channel.send(
                f"<@{TARGET_USER_ID}> Wochenzeit wurde zurückgesetzt. "
                "Die neue Woche startet jetzt bei 0."
            )
            logger.info("Wochenreset für %s durchgeführt.", today)


async def report_scheduler_loop() -> None:
    """
    Wartet täglich bis 23:59 Europe/Berlin und sendet dann den Report.
    """
    await bot.wait_until_ready()

    while not bot.is_closed():
        next_run = next_report_datetime()
        sleep_seconds = max(1, (next_run - local_now()).total_seconds())

        logger.info(
            "Nächster Auto-Report geplant für %s",
            next_run.strftime("%d.%m.%Y %H:%M:%S %Z"),
        )

        await asyncio.sleep(sleep_seconds)

        try:
            await send_daily_report_and_maybe_reset()
        except Exception:
            logger.exception("Fehler beim automatischen Tagesbericht.")


@bot.event
async def on_ready():
    logger.info("Bot ist eingeloggt als %s (%s)", bot.user, bot.user.id)
    logger.info("Tracke aktive Voice-Zeit von User-ID: %s", TARGET_USER_ID)
    logger.info("Report-Channel-ID: %s", REPORT_CHANNEL_ID)
    logger.info("Zeitzone: %s", LOCAL_TZ)

    if not hasattr(bot, "report_scheduler_started"):
        bot.report_scheduler_started = True
        bot.loop.create_task(report_scheduler_loop())

    await reevaluate_tracking("bot_ready")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    relevant = False

    if member.id == TARGET_USER_ID:
        relevant = True
    else:
        before_channel = before.channel
        after_channel = after.channel

        if before_channel and any(m.id == TARGET_USER_ID for m in before_channel.members):
            relevant = True

        if after_channel and any(m.id == TARGET_USER_ID for m in after_channel.members):
            relevant = True

    if relevant:
        await reevaluate_tracking(f"voice_state_update:{member.id}")


@bot.command(name="voicetime")
async def voicetime(ctx: commands.Context):
    """Zeigt die gesamte aktive Voice-Zeit der aktuellen Woche."""
    total_seconds = store.get_total_seconds(include_current_session=True)
    session_start = store.get_session_start()

    status_text = "läuft gerade" if session_start is not None else "nicht aktiv"

    member = await find_target_member()
    current_channel = "nicht im Voice-Channel"

    if member and member.voice and member.voice.channel:
        current_channel = member.voice.channel.name

    embed = discord.Embed(
        title="Aktive Voice-Zeit Tracker",
        color=discord.Color.green(),
    )
    embed.add_field(name="User", value=f"<@{TARGET_USER_ID}>", inline=False)
    embed.add_field(
        name="Aktive Voice-Zeit diese Woche",
        value=format_duration(total_seconds),
        inline=False,
    )
    embed.add_field(name="Aktuelle Session", value=status_text, inline=False)
    embed.add_field(name="Aktueller Channel", value=current_channel, inline=False)

    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="session")
async def session(ctx: commands.Context):
    """Zeigt die Dauer der aktuellen aktiven Session."""
    session_start = store.get_session_start()

    if session_start is None:
        await ctx.reply("Aktuell läuft keine aktive Voice-Session.", mention_author=False)
        return

    current_seconds = utc_now_ts() - int(session_start)
    await ctx.reply(
        f"Aktive Voice-Session läuft seit: **{format_duration(current_seconds)}**",
        mention_author=False,
    )


@bot.command(name="testreport")
@commands.has_permissions(administrator=True)
async def testreport(ctx: commands.Context):
    """Sendet testweise sofort den Tagesbericht, ohne Datumssperre zu beachten."""
    store.set_state("last_report_date", "")
    await send_daily_report_and_maybe_reset()
    await ctx.reply("Testreport wurde ausgelöst.", mention_author=False)


@bot.command(name="resetvoicetime")
@commands.has_permissions(administrator=True)
async def resetvoicetime(ctx: commands.Context):
    """Setzt die gespeicherte aktive Voice-Zeit zurück. Nur für Administratoren."""
    member = await find_target_member()
    should_count_after_reset = bool(member and is_target_actively_countable(member))
    store.reset_keep_running_session_if_needed(should_count_after_reset)
    await ctx.reply("Die aktive Voice-Zeit wurde zurückgesetzt.", mention_author=False)


@resetvoicetime.error
@testreport.error
async def admin_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply(
            "Du brauchst Administrator-Rechte, um diesen Command zu nutzen.",
            mention_author=False,
        )
    else:
        logger.exception("Admin-Command-Fehler: %s", error)
        await ctx.reply("Beim Ausführen ist ein Fehler aufgetreten.", mention_author=False)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return

    logger.exception("Command-Fehler: %s", error)
    await ctx.reply("Es ist ein Fehler beim Ausführen des Commands aufgetreten.", mention_author=False)


if __name__ == "__main__":
    bot.run(TOKEN)
