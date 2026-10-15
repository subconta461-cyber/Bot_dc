"""Confere as dependências locais sem conectar ao Discord."""
import importlib.util
import platform
import shutil

import discord
import dotenv
import nacl
import yt_dlp
import dc

print("Imports do bot: OK")
print("Python:", platform.python_version(), platform.architecture()[0])
print("discord.py:", discord.__version__)
print("FFmpeg no PATH:", shutil.which("ffmpeg") or "não encontrado (ou configure FFMPEG_PATH)")
print("Dependência de voz davey:", "OK" if importlib.util.find_spec("davey") else "ausente; instale requirements.txt com Python 64 bits")
