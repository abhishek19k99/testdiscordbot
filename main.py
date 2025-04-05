import os

import discord
import requests
import yt_dlp
from bs4 import BeautifulSoup
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot_token = os.getenv("DISCORD_TOKEN")


@bot.event
async def on_ready():
    print(f"{bot.guilds}")
    for guild in bot.guilds:
        print(f"- {guild.name}")
    print(f"Bot is online as {bot.user}")


def get_spotify_title(spotify_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(spotify_url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.text.replace(" | Spotify", "").strip()
        return title
    return None


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
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch:{search_query}", download=True)
        if "entries" in info:
            info = info["entries"][0]
        filename = (
            ydl.prepare_filename(info).replace(".webm", ".mp3").replace(".m4a", ".mp3")
        )
        return filename


@bot.command()
async def play(ctx, *, spotify_url):
    if not ctx.author.voice:
        await ctx.send("You must be in a voice channel!")
        return

    voice_channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await voice_channel.connect()
    elif ctx.voice_client.channel != voice_channel:
        await ctx.voice_client.move_to(voice_channel)

    await ctx.send("Fetching track info...")
    title = get_spotify_title(spotify_url)
    if not title:
        await ctx.send("Couldn't get song title from Spotify.")
        return

    await ctx.send(f"Searching YouTube for: **{title}**")
    try:
        filename = download_youtube_audio(title)

        if os.path.exists(filename):
            source = discord.FFmpegPCMAudio(executable="ffmpeg", source=filename)
            if ctx.voice_client.is_playing():
                ctx.voice_client.stop()
            ctx.voice_client.play(
                source,
                after=lambda e: os.remove(filename)
                if e is None
                else print(f"Player error: {e}"),
            )
            await ctx.send(f"Now playing: **{title}**")
        else:
            await ctx.send("Failed to download audio.")
    except Exception as e:
        await ctx.send(f"Error: {str(e)}")


@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected.")
    else:
        await ctx.send("I'm not connected to a voice channel.")


@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused.")
    else:
        await ctx.send("Nothing is playing.")


@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed.")
    else:
        await ctx.send("Nothing is paused.")


# Add this line at the end to run the bot
bot.run(bot_token)
