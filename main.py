import asyncio
import os
import re
from collections import deque
from random import shuffle

import discord
import yt_dlp
from discord.ext import commands
from dotenv import load_dotenv
from spotdl import Spotdl
from spotdl.types.options import DownloaderOptions

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)
play_locks = {}
load_dotenv()

# Spotify regex for validation
spotify_regex = (
    r"(https?://)?(www\.)?(open\.spotify\.com)/(track|album|playlist)/[a-zA-Z0-9]+"
)


# Enhanced Queue System
class MusicQueue:
    def __init__(self):
        self.queue = deque()
        self.history = deque(maxlen=50)
        self.loop = False  # False, 'song', or 'queue'
        self._current_index = 0
        self._is_looping = False

    @property
    def current_position(self):
        return self._current_index

    @current_position.setter
    def current_position(self, value):
        self._current_index = max(0, min(value, len(self.queue)))

    def add(self, items):
        self.queue.extend(items)

    def next(self):
        if self.loop == "song" and self.queue:
            return self.queue[self.current_position - 1]

        if self.current_position < len(self.queue):
            song = self.queue[self.current_position]
            self.current_position += 1
            self.history.append(song)
            return song
        elif self.loop == "queue" and self.queue:
            self.current_position = 0
            return self.next()
        return None

    def previous(self):
        if len(self.history) > 0:
            prev_song = self.history.pop()
            self.current_position = max(0, self.current_position - 2)
            return prev_song
        return None

    def current_song(self):
        if self.queue and self.current_position > 0:
            return self.queue[self.current_position - 1]
        return None

    def clear(self):
        self.queue.clear()
        self.current_position = 0
        self.history.clear()

    def remove(self, index):
        if 0 <= index < len(self.queue):
            del self.queue[index]
            if index < self.current_position:
                self.current_position -= 1

    def shuffle(self):
        current = list(self.queue)[self.current_position :]
        shuffle(current)
        self.queue = deque(list(self.queue)[: self.current_position] + current)

    def __len__(self):
        return len(self.queue) - self.current_position


# Initialize spotdl
downloader_options = DownloaderOptions(
    audio_providers=["youtube-music"],
    lyrics_providers=["genius"],
    ffmpeg="ffmpeg",
    bitrate="320k",
    output_format="mp3",
    overwrite="skip",
    client_id=os.environ["SPOTIFY_CLIENT_ID"],
    client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
)

spotdl_client = Spotdl(
    client_id=downloader_options["client_id"],
    client_secret=downloader_options["client_secret"],
    downloader_settings=downloader_options,
)

# Global states
queues = {}


def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = MusicQueue()
    return queues[guild_id]


@bot.event
async def on_ready():
    print(f"Connected to {len(bot.guilds)} guilds:")
    for guild in bot.guilds:
        print(f"- {guild.name} (ID: {guild.id})")
    print(f"Bot is online as {bot.user}")


def download_youtube_audio(search_query):
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "default_search": "ytsearch",
        "quiet": True,
        "outtmpl": "song.%(ext)s",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "opus",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{search_query}", download=True)
            if "entries" in info:
                info = info["entries"][0]
            filename = (
                ydl.prepare_filename(info)
                .replace(".webm", ".mp3")
                .replace(".m4a", ".mp3")
            )
            return filename, info.get("title")
    except Exception as e:
        print(f"Download error: {e}")
        return None, None
    finally:
        if os.path.exists(filename):
            os.remove(filename)


def get_play_lock(guild_id):
    if guild_id not in play_locks:
        play_locks[guild_id] = asyncio.Lock()
    return play_locks[guild_id]


async def play_next(ctx):
    lock = get_play_lock(ctx.guild.id)
    async with lock:
        queue = get_queue(ctx.guild.id)

        while True:
            song_info = queue.next()
            if not song_info:
                await ctx.send("Queue finished")
                return

            filename, title = await asyncio.to_thread(download_youtube_audio, song_info)
            if not filename or not os.path.exists(filename):
                await ctx.send(f"Failed to download: {song_info}")
                continue

            try:
                source = discord.FFmpegPCMAudio(
                    executable="ffmpeg",
                    source=filename,
                    before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                    options="-vn",
                )
                ctx.voice_client.play(
                    source,
                    after=lambda e: asyncio.run_coroutine_threadsafe(
                        play_next(ctx), bot.loop
                    ),
                )
                await ctx.send(f"Now playing: **{title}**")
                return
            except Exception as e:
                await ctx.send(f"Playback error: {e}")
                continue


async def process_spotify_url(ctx, url):
    try:
        if not re.match(spotify_regex, url):
            await ctx.send("Invalid Spotify URL")
            return False

        queue = get_queue(ctx.guild.id)
        songs = await asyncio.to_thread(spotdl_client.search, [url])

        if not songs:
            await ctx.send("No results found")
            return False

        items = [f"{song.artists[0]} - {song.name}" for song in songs]
        queue.add(items)

        if "track" in url:
            await ctx.send(f"Added to queue: **{items[0]}**")
        else:
            await ctx.send(f"Added {len(items)} tracks to queue")

        if not ctx.voice_client.is_playing():
            await play_next(ctx)

        return True
    except Exception as e:
        await ctx.send(f"Spotify error: {e}")
        return False


@bot.command()
async def play(ctx, *, query):
    if not ctx.author.voice:
        return await ctx.send("Join a voice channel first!")

    try:
        voice_channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            await voice_channel.connect()
        elif ctx.voice_client.channel != voice_channel:
            await ctx.voice_client.move_to(voice_channel)

        if "open.spotify.com" in query:
            await process_spotify_url(ctx, query)
        else:
            queue = get_queue(ctx.guild.id)
            queue.add([query])
            await ctx.send(f"Added to queue: **{query}**")
            if not ctx.voice_client.is_playing():
                await play_next(ctx)

    except Exception as e:
        await ctx.send(f"Error: {e}")


@bot.command()
async def queue(ctx):
    queue = get_queue(ctx.guild.id)
    current = queue.current_song()
    upcoming = list(queue.queue)[queue.current_position :]

    if not current and not upcoming:
        return await ctx.send("Queue is empty")

    message = []
    if current:
        message.append(f"**Now Playing:** {current}")

    if upcoming:
        message.append("\n**Upcoming:**")
        for i, song in enumerate(upcoming[:10], 1):
            message.append(f"{i}. {song}")

    await ctx.send("\n".join(message))


@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Skipped current song")
        await play_next(ctx)
    else:
        await ctx.send("Nothing playing")


@bot.command()
async def previous(ctx):
    queue = get_queue(ctx.guild.id)
    prev_song = queue.previous()

    if prev_song:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.send("Returning to previous song")
        await play_next(ctx)
    else:
        await ctx.send("No previous song in history")


@bot.command()
async def remove(ctx, index: int):
    queue = get_queue(ctx.guild.id)
    try:
        queue.remove(index - 1)
        await ctx.send(f"Removed item #{index}")
    except IndexError:
        await ctx.send("Invalid queue position")


@bot.command()
async def clear(ctx):
    queue = get_queue(ctx.guild.id)
    queue.clear()
    await ctx.send("Queue cleared")


@bot.command()
async def loop(ctx, mode: str = None):
    queue = get_queue(ctx.guild.id)
    modes = {
        None: "Looping disabled",
        "song": "Looping current song",
        "queue": "Looping entire queue",
    }

    if mode not in modes and mode is not None:
        return await ctx.send("Invalid mode. Use 'song' or 'queue'")

    queue.loop = mode if mode in ("song", "queue") else False
    await ctx.send(modes[mode] if mode else modes[None])


@bot.command()
async def shuff(ctx):
    queue = get_queue(ctx.guild.id)
    queue.shuffle()
    await ctx.send("Queue shuffled")


@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        get_queue(ctx.guild.id).clear()
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected")
    else:
        await ctx.send("Not connected")


@bot.command()
async def next(ctx):
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_connected():
        return await ctx.send("Not connected to a voice channel")

    queue = get_queue(ctx.guild.id)

    if len(queue) == 0:
        return await ctx.send("Queue is empty")

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()  # This will trigger the after callback to play the next song
        await ctx.send("Skipped to next song")
    else:
        await ctx.send("Nothing is currently playing")


@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused")
    else:
        await ctx.send("Nothing playing")


@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed")
    else:
        await ctx.send("Not paused")


if __name__ == "__main__":
    bot.run(os.environ["BOT_TOKEN"])
