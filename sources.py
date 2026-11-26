"""Busca e extração de áudio com yt-dlp (SoundCloud por padrão) + utilidades do FFmpeg."""
import importlib.metadata
import logging
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.version import __version__ as YTDLP_VERSION

log = logging.getLogger("musica.fontes")

KNOWN_HOSTS = (
    "soundcloud.com/", "www.soundcloud.com/", "m.soundcloud.com/", "on.soundcloud.com/",
    "youtube.com/", "www.youtube.com/", "music.youtube.com/", "m.youtube.com/", "youtu.be/",
)
BEFORE_SIMPLE = "-nostdin"


class AudioError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Track:
    title: str
    page_url: str
    duration: int = 0
    thumbnail: str = ""
    requester: str = ""
    stream_url: str = ""
    headers: dict = field(default_factory=dict)
    resolved_at: float = 0.0

    def fresh(self):
        """URLs de stream expiram; considera válida por 5 minutos."""
        return bool(self.stream_url) and time.monotonic() - self.resolved_at < 300


def classify(error):
    text = str(error).lower().replace("’", "'")
    if "sign in to confirm" in text or "not a bot" in text:
        return AudioError("youtube_auth", "O YouTube bloqueou esta hospedagem. Use o nome da música (busca no SoundCloud) ou um link do SoundCloud.")
    if "429" in text or "too many requests" in text:
        return AudioError("rate_limit", "A fonte limitou os pedidos. Aguarde um minuto e tente de novo.")
    if "private" in text or "unavailable" in text or "not available" in text or "removed" in text or "404" in text:
        return AudioError("unavailable", "Esse conteúdo está privado, removido ou indisponível. Tente outra música pública.")
    if "403" in text:
        return AudioError("forbidden", "A fonte recusou o acesso ao áudio (403).")
    if "timed out" in text or "timeout" in text:
        return AudioError("timeout", "A fonte demorou para responder. Tente de novo em instantes.")
    return AudioError("extract", "Não consegui obter o áudio dessa música. Tente outro nome ou um link público.")


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def ffmpeg_path():
    return shutil.which(os.getenv("FFMPEG_PATH", "").strip() or "ffmpeg")


def to_target(query):
    """Converte o texto do usuário em URL ou busca. Retorna o alvo para o yt-dlp."""
    q = (query or "").strip()
    if not q:
        raise AudioError("empty", "Digite o nome ou o link da música.")
    low = q.lower()
    if low.startswith(("http://", "https://")):
        return q
    if low.startswith(KNOWN_HOSTS):
        return "https://" + q
    provider = os.getenv("SEARCH_PROVIDER", "soundcloud").strip().lower()
    return f"{'ytsearch1' if provider == 'youtube' else 'scsearch1'}:{q}"


def _opts(**extra):
    opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "retries": 2,
        "extractor_retries": 1,
        "cachedir": False,
    }
    opts.update(extra)
    return opts


def extract(query):
    """Bloqueante: use com asyncio.to_thread. Devolve um Track com URL de stream."""
    target = to_target(query)
    try:
        with yt_dlp.YoutubeDL(_opts()) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as error:
        log.warning("yt-dlp falhou para %r: %s", target, error)
        raise classify(error) from error
    if info and "entries" in info:
        info = next((e for e in info["entries"] if e), None)
    if not info:
        raise AudioError("not_found", "Não encontrei nada com esse nome. Tente outras palavras ou um link.")
    url = info.get("url") or ""
    if urlsplit(url).scheme not in ("http", "https"):
        raise AudioError("no_audio", "A fonte respondeu, mas não liberou um formato de áudio que eu consiga tocar.")
    page = info.get("webpage_url") or info.get("original_url") or (target if target.startswith("http") else url)
    return Track(
        title=info.get("title") or "Música",
        page_url=page,
        duration=int(info.get("duration") or 0),
        thumbnail=info.get("thumbnail") or "",
        stream_url=url,
        headers=dict(info.get("http_headers") or {}),
        resolved_at=time.monotonic(),
    )


def refresh(track):
    """Bloqueante: obtém uma URL de stream nova para a mesma música."""
    new = extract(track.page_url)
    track.stream_url, track.headers, track.resolved_at = new.stream_url, new.headers, new.resolved_at
    if not track.duration:
        track.duration = new.duration


def download(page_url, directory):
    """Bloqueante: baixa o áudio para um arquivo (plano C quando a transmissão falha)."""
    opts = _opts(outtmpl=os.path.join(directory, "%(id)s.%(ext)s"))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(page_url, download=True)
            if "entries" in info:
                info = next(e for e in info["entries"] if e)
            done = (info.get("requested_downloads") or [{}])[0].get("filepath")
            path = done or ydl.prepare_filename(info)
    except Exception as error:
        raise classify(error) from error
    if not os.path.isfile(path):
        raise AudioError("download", "O download do áudio não gerou arquivo.")
    return path


def before_full(headers):
    """Opções de leitura de rede do FFmpeg. Só repassa cabeçalhos seguros."""
    base = "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    safe = []
    for key in ("User-Agent", "Referer", "Origin"):
        value = str((headers or {}).get(key, ""))
        if value and not any(c in value for c in "\r\n\x00"):
            safe.append(f"{key}: {value}\r\n")
    return base + (" -headers " + shlex.quote("".join(safe)) if safe else "")


def probe(ffmpeg, url, before, seconds=3):
    """Bloqueante: decodifica alguns segundos como o bot faz. Devolve (código, bytes, últimas linhas de erro)."""
    cmd = [ffmpeg, "-loglevel", "warning", *shlex.split(before), "-i", url,
           "-vn", "-t", str(seconds), "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return -1, 0, ["FFmpeg não terminou em 30 segundos."]
    tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
    return proc.returncode, len(proc.stdout), tail
