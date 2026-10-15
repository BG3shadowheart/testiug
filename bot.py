import asyncio
import logging
import os

import discord
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("idle-bot")

GUILD_ID = 1506642663208648887

# (env var name, voice channel id) — one bot per VC (Discord limit)
INSTANCES = [
    ("DISCORD_TOKEN_1", 1560338794975072346),
    ("DISCORD_TOKEN_2", 1524857783605006480),
]


class Silence(discord.AudioSource):
    """Endless silent PCM so Discord doesn't kick the bot from the VC."""
    def read(self) -> bytes:
        return b"\x00" * 3840  # 20 ms @ 48 kHz stereo s16le

    def is_opus(self) -> bool:
        return False


class IdleBot(discord.Client):
    def __init__(self, vc_id: int):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.vc_id = vc_id

    async def on_ready(self):
        log.info("Logged in as %s (%s) -> VC %s", self.user, self.user.id, self.vc_id)
        if not self.keep_alive.is_running():
            self.keep_alive.start()

    async def _join(self):
        guild = self.get_guild(GUILD_ID)
        if guild is None:
            log.warning("Guild %s not found for %s", GUILD_ID, self.user)
            return

        channel = guild.get_channel(self.vc_id)
        if channel is None:
            log.warning("VC %s not found", self.vc_id)
            return

        vc = guild.voice_client
        if vc and vc.is_connected() and vc.channel.id == self.vc_id:
            if not vc.is_playing():
                vc.play(Silence())
            return

        if vc:
            await vc.move_to(channel)
        else:
            vc = await channel.connect(self_deaf=True, reconnect=True)

        if not vc.is_playing():
            vc.play(Silence())
        log.info("Joined VC %s (%s)", channel.name, channel.id)

    @tasks.loop(seconds=20)
    async def keep_alive(self):
        try:
            await self._join()
        except Exception as e:
            log.exception("keep_alive error: %s", e)

    @keep_alive.before_loop
    async def _before(self):
        await self.wait_until_ready()


async def run_instance(token: str, vc_id: int):
    bot = IdleBot(vc_id)
    try:
        await bot.start(token)
    except Exception:
        log.exception("Bot for VC %s crashed, restarting in 10s", vc_id)
        await asyncio.sleep(10)
        asyncio.create_task(run_instance(token, vc_id))


async def main():
    tasks_list = []
    for env, vc in INSTANCES:
        token = os.getenv(env)
        if not token:
            log.warning("Missing %s — skipping VC %s", env, vc)
            continue
        tasks_list.append(asyncio.create_task(run_instance(token, vc)))

    if not tasks_list:
        raise SystemExit("No tokens configured. Copy .env.example to .env")

    await asyncio.gather(*tasks_list)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
