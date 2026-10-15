# Idle VC Bot

A minimal Discord bot that joins a voice channel and sits there 24/7.
Whenever someone joins the channel the bot is in, it plays `salam.mp3`.
No commands, no message handling, no FFmpeg.

- Guild: `1506642663208648887`
- Voice channels (tried in order): `1560338794975072346`, `1524857783605006480`

On startup and every 20 seconds the bot checks that it is connected to one of the
listed channels. If not, it connects/moves to the **first listed channel that still
exists**. If neither exists, it logs a warning and keeps retrying. If the client
crashes, it restarts after 10 seconds.

## Setup

### 1. Create the Discord app
1. Go to <https://discord.com/developers/applications> → **New Application**.
2. Open **Bot** → **Reset Token** → copy the token (you'll need it for Railway).
3. Leave all **Privileged Gateway Intents** turned **off**.

### 2. Invite the bot
1. Open **OAuth2 → URL Generator**.
2. Scopes: `bot`.
3. Bot permissions: **Connect** and **Speak** (plus *View Channel* so it can see the VCs).
4. Open the generated URL and add the bot to your server.

### 3. Push to GitHub
```bash
git init
git add .
git commit -m "Idle VC bot"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### 4. Deploy on Railway
1. <https://railway.app> → **New Project → Deploy from GitHub repo** → pick this repo.
2. In the service's **Variables** tab, add `DISCORD_TOKEN` = your bot token.
3. Set the start command to `python bot.py` (Settings → Deploy → Custom Start Command;
   the included `Procfile` / `nixpacks.toml` already specify it).
4. Deploy. Logs should show `Idling in <channel>`.

The bot runs a tiny HTTP server on `0.0.0.0:$PORT` (default 8080) that returns
`200 ok` on `GET /`, so Railway's healthcheck stays green. It runs in a daemon
thread and does not touch the Discord gateway.

### Join sound
- `salam.mp3` is the original sound. The bot actually plays `salam.pcm` (same sound,
  pre-converted to raw 48 kHz stereo PCM) so Railway doesn't need FFmpeg.
- It plays when a real user (not a bot) joins the channel the bot is sitting in.
  If the sound is already playing, extra joins are ignored until it finishes.
- To change the sound, put your new file next to `bot.py` as `new.mp3` and run:
  ```bash
  ffmpeg -y -i new.mp3 -f s16le -ar 48000 -ac 2 salam.pcm
  ```
  then commit and push `salam.pcm`.

### libopus note
Because the bot streams raw PCM, discord.py must encode it with **libopus**, which
isn't bundled on Linux. `nixpacks.toml` installs it (`libopus0`). If your Railway
builder is Railpack instead of Nixpacks and the logs say "Could not load libopus",
add the variable `RAILPACK_DEPLOY_APT_PACKAGES=libopus0` to the service.
(Adding it up front is harmless on either builder.)

### Run locally (optional)
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export DISCORD_TOKEN=your_token_here
python bot.py
```

## Why streaming silence keeps the connection alive

Discord's voice servers may drop clients that send no audio packets for a while
(commonly around 5 minutes). Simply sitting in a channel isn't always enough. The bot
plays an endless `AudioSource` that returns 3840 bytes of zeroed PCM (20 ms of 48 kHz
stereo 16-bit audio) every frame. discord.py encodes and sends those packets, so the
connection looks active to Discord while nothing audible is transmitted.
Keep `discord.py` unpinned (or recent): newer Discord voice requirements are only
supported by current releases.
