"""Idle VC bot: sits in a voice channel 24/7, streams silence, and plays salam.mp3
whenever someone joins the channel it is in."""

import asyncio
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord
from discord.ext import tasks

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
GUILD_ID = 1506642663208648887
VOICE_CHANNEL_IDS = [1560338794975072346, 1524857783605006480]  # tried in order
KEEP_ALIVE_SECONDS = 20
RESTART_DELAY_SECONDS = 10

# Sound played when someone joins the bot's voice channel.
# salam.pcm is salam.mp3 pre-converted to raw 48kHz stereo s16le (no FFmpeg needed).
SOUND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salam.pcm")
JOIN_DELAY_SECONDS = 1.0  # wait a moment so the joiner's client is ready to hear it

# 48 kHz * 2 channels * 2 bytes (s16le) * 0.020 s = 3840 bytes per frame
FRAME_BYTES = 3840

log = logging.getLogger("idle-vc")


# --------------------------------------------------------------------------- #
# Audio: endless silence
# --------------------------------------------------------------------------- #
class SoundSource(discord.AudioSource):
    """Raw 48kHz stereo s16le PCM. Plays silence, or the join sound when triggered.

    One instance is kept playing forever, so the connection never goes quiet and
    we never have to stop/restart the stream to play a clip.
    """

    _SILENCE = b"\x00" * FRAME_BYTES

    def __init__(self, sound_path: str) -> None:
        self._sound = b""
        self._pos = 0
        self._playing = False
        try:
            with open(sound_path, "rb") as f:
                self._sound = f.read()
            log.info("Loaded join sound: %s (%.1fs)", sound_path, len(self._sound) / (FRAME_BYTES * 50))
        except OSError:
            log.warning("Join sound %s not found; running silent-only", sound_path)

    @property
    def is_playing_sound(self) -> bool:
        return self._playing

    def trigger(self) -> bool:
        """Start the join sound. Ignored if empty or already playing."""
        if not self._sound or self._playing:
            return False
        self._pos = 0
        self._playing = True
        return True

    def read(self) -> bytes:
        if not self._playing:
            return self._SILENCE
        chunk = self._sound[self._pos : self._pos + FRAME_BYTES]
        self._pos += FRAME_BYTES
        if len(chunk) < FRAME_BYTES:  # last (partial) frame -> pad, then back to silence
            chunk = chunk.ljust(FRAME_BYTES, b"\x00")
            self._playing = False
        return chunk

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        pass


def load_opus() -> None:
    """discord.py needs libopus to encode PCM. Try common Linux names."""
    if discord.opus.is_loaded():
        return
    for name in ("libopus.so.0", "libopus.so", "opus"):
        try:
            discord.opus.load_opus(name)
            log.info("Loaded opus library: %s", name)
            return
        except OSError:
            continue
    log.warning("Could not load libopus; voice playback will fail. See README.")


# --------------------------------------------------------------------------- #
# Health server (for Railway)
# --------------------------------------------------------------------------- #
class HealthHandler(BaseHTTPRequestHandler):
    def _respond(self, send_body: bool) -> None:
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            if send_body:
                self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self) -> None:
        self._respond(True)

    def do_HEAD(self) -> None:
        self._respond(False)

    def log_message(self, *args) -> None:  # keep logs quiet
        pass


def start_health_server() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health").start()
    log.info("Health server listening on 0.0.0.0:%d", port)


# --------------------------------------------------------------------------- #
# Bot
# --------------------------------------------------------------------------- #
class IdleBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.source = SoundSource(SOUND_FILE)

    async def setup_hook(self) -> None:
        self.keep_alive.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Play the join sound when a real user enters the channel the bot is in."""
        if member.bot or member.id == (self.user.id if self.user else None):
            return
        if after.channel is None or after.channel == before.channel:
            return  # left, or just muted/deafened/etc. inside the same channel

        vc: discord.VoiceClient | None = member.guild.voice_client  # type: ignore[assignment]
        if vc is None or not vc.is_connected() or vc.channel != after.channel:
            return  # they joined some other channel

        await asyncio.sleep(JOIN_DELAY_SECONDS)
        if not vc.is_connected():
            return
        if self.source.trigger():
            log.info("Playing join sound for %s in %s", member, after.channel.name)

    def first_available_channel(self, guild: discord.Guild):
        """First listed VC that still exists in the guild, else None."""
        for channel_id in VOICE_CHANNEL_IDS:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                return channel
        return None

    @tasks.loop(seconds=KEEP_ALIVE_SECONDS)
    async def keep_alive(self) -> None:
        try:
            await self.ensure_connected()
        except Exception:
            log.exception("Keep-alive iteration failed; will retry")

    @keep_alive.before_loop
    async def before_keep_alive(self) -> None:
        await self.wait_until_ready()

    async def ensure_connected(self) -> None:
        guild = self.get_guild(GUILD_ID)
        if guild is None:
            log.warning("Guild %s not found; retrying", GUILD_ID)
            return

        vc: discord.VoiceClient | None = guild.voice_client  # type: ignore[assignment]

        # Already connected to one of the listed VCs -> nothing to do
        # (just make sure silence is still playing).
        if vc is not None and vc.is_connected() and vc.channel and vc.channel.id in VOICE_CHANNEL_IDS:
            if not vc.is_playing():
                vc.play(self.source)
                log.info("Restarted silence stream")
            return

        target = self.first_available_channel(guild)
        if target is None:
            log.warning("None of the voice channels %s exist; retrying", VOICE_CHANNEL_IDS)
            return

        # Connected, but to the wrong channel -> move.
        if vc is not None and vc.is_connected():
            log.info("Moving to %s (%s)", target.name, target.id)
            await vc.move_to(target)
        else:
            # Stale/broken voice client -> clean it up before reconnecting.
            if vc is not None:
                log.info("Cleaning up stale voice client")
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    log.exception("Error while disconnecting stale voice client")
            log.info("Connecting to %s (%s)", target.name, target.id)
            vc = await target.connect(self_deaf=True, reconnect=True, timeout=30.0)

        # Make sure we're self-deafened after a move as well.
        await guild.change_voice_state(channel=target, self_deaf=True)

        if not vc.is_playing():
            vc.play(self.source)
        log.info("Idling in %s (%s)", target.name, target.id)


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
async def run_forever(token: str) -> None:
    while True:
        client = IdleBot()
        try:
            await client.start(token, reconnect=True)
        except discord.LoginFailure:
            log.critical("Invalid DISCORD_TOKEN; exiting")
            sys.exit(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Client crashed")
        finally:
            if not client.is_closed():
                await client.close()
        log.info("Restarting in %ds...", RESTART_DELAY_SECONDS)
        await asyncio.sleep(RESTART_DELAY_SECONDS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.critical("DISCORD_TOKEN environment variable is not set")
        sys.exit(1)

    load_opus()
    start_health_server()

    try:
        asyncio.run(run_forever(token))
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
