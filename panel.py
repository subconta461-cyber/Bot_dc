"""Painel local da Uminida. Não expõe o token do Discord nem portas de rede."""
import asyncio
import hmac
import logging
import os
import secrets
import time
from collections import deque
from urllib.parse import urlsplit
from pathlib import Path
from types import SimpleNamespace

import discord
from aiohttp import web, BasicAuth
from branding import message_kwargs
from youtube_audio import AudioError

log = logging.getLogger("uminida.painel")
ROOT = Path(__file__).parent / "site"


class PanelContext:
    def __init__(self, bot, guild, channel, voice_channel):
        self.bot, self.guild, self.channel = bot, guild, channel
        self.author = SimpleNamespace(display_name="Painel da Uminida", voice=SimpleNamespace(channel=voice_channel))

    @property
    def voice_client(self):
        return self.guild.voice_client

    async def send(self, content=None, **kwargs):
        payload = message_kwargs(content, embed=kwargs.pop("embed", None), embeds=kwargs.pop("embeds", None))
        return await self.channel.send(**payload, **kwargs)


def track_data(track):
    if not track:
        return None
    return {"id": track.uid, "title": track.title, "url": track.url,
            "thumbnail": track.thumbnail, "duration": track.duration,
            "requester": track.requester}


def create_app(bot, runtime):
    token = secrets.token_urlsafe(32)
    public = bool(os.getenv("PORT") or os.getenv("RAILWAY_ENVIRONMENT_ID"))
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    origin = (os.getenv("PANEL_PUBLIC_URL", "").strip() or ("https://" + domain if domain else "")) if public else "http://127.0.0.1:8765"
    origin = origin.rstrip("/")
    if public and origin and (urlsplit(origin).scheme != "https" or not urlsplit(origin).netloc or urlsplit(origin).path):
        raise RuntimeError("PANEL_PUBLIC_URL deve ser um endereço HTTPS sem caminho.")
    password = os.getenv("PANEL_PASSWORD", "")
    if public and len(password) < 16:
        raise RuntimeError("Configure PANEL_PASSWORD nas Variables do Railway, com pelo menos 16 caracteres.")
    failures = deque(maxlen=30)

    @web.middleware
    async def guard(request, handler):
        if request.path == "/healthz" and request.method == "GET":
            return web.json_response({"ready": bot.is_ready()}, status=200 if bot.is_ready() else 503)
        expected_host = urlsplit(origin).netloc
        if public and not expected_host:
            return web.Response(status=503, text="Gere um domínio no Railway e faça novo deploy. Para domínio próprio, configure PANEL_PUBLIC_URL.")
        if request.host != expected_host:
            raise web.HTTPForbidden(text="Use o endereço configurado para o painel da Uminida.")
        if request.headers.get("Origin") not in (None, origin):
            raise web.HTTPForbidden(text="Origem não permitida.")
        if public:
            now = time.monotonic()
            while failures and now - failures[0] > 60:
                failures.popleft()
            authorized = False
            try:
                auth = BasicAuth.decode(request.headers.get("Authorization", ""), encoding="utf-8")
                authorized = hmac.compare_digest(auth.login.encode(), b"uminida") and hmac.compare_digest(auth.password.encode(), password.encode())
            except (ValueError, UnicodeError):
                pass
            if not authorized:
                if len(failures) >= 20:
                    return web.Response(status=429, text="Muitas tentativas. Aguarde um minuto.", headers={"Retry-After": "60", "Cache-Control": "no-store"})
                if request.headers.get("Authorization"):
                    failures.append(now)
                return web.Response(status=401, text="Entre com o usuário uminida e a senha PANEL_PASSWORD.", headers={"WWW-Authenticate": 'Basic realm="Uminida", charset="UTF-8"', "Cache-Control": "no-store"})
        if request.method != "GET" and not hmac.compare_digest(request.headers.get("X-Uminida-Key", ""), token):
            return web.json_response({"error": "Recarregue o painel para continuar."}, status=403)
        try:
            response = await handler(request)
        except (ValueError, TypeError, KeyError):
            response = web.json_response({"error": "Confira o servidor, os canais e os dados enviados."}, status=400)
        except discord.DiscordException:
            log.exception("Falha do Discord no painel")
            response = web.json_response({"error": "O Discord recusou a ação. Confira as permissões do bot e tente novamente."}, status=409)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' https:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    app = web.Application(middlewares=[guard], client_max_size=8192)

    async def index(request):
        html = (ROOT / "index.html").read_text(encoding="utf-8").replace("__UMINIDA_KEY__", token)
        if public:
            html = html.replace("No seu PC", "Painel online").replace("O painel controla a Uminida neste PC.", "O painel controla a Uminida no Railway.")
            html = html.replace("Este painel funciona enquanto o bot está aberto no seu PC. Para abrir de novo: http://127.0.0.1:8765", "Seu painel online. Use o usuário uminida e a senha definida no Railway. Para encerrar o acesso, feche a janela privativa do navegador.")
        return web.Response(text=html, content_type="text/html")

    async def asset(request):
        name = request.match_info["name"]
        if name not in {"app.js", "style.css", "moon.svg"}:
            raise web.HTTPNotFound()
        return web.FileResponse(ROOT / name)

    async def state(request):
        guilds = []
        for guild in bot.guilds:
            me = guild.me
            if not me:
                continue
            voices = [{"id": str(c.id), "name": c.name} for c in guild.voice_channels
                      if c.permissions_for(me).view_channel and c.permissions_for(me).connect and c.permissions_for(me).speak]
            texts = [{"id": str(c.id), "name": c.name} for c in guild.text_channels
                     if c.permissions_for(me).view_channel and c.permissions_for(me).send_messages]
            guilds.append({"id": str(guild.id), "name": guild.name,
                           "icon": str(guild.icon.url) if guild.icon else None,
                           "voice_channels": voices, "text_channels": texts})
        guild = bot.get_guild(int(request.query["guild"])) if request.query.get("guild", "").isdigit() else None
        player = runtime["players"].get(guild.id) if guild else None
        voice = guild.voice_client if guild else None
        connected = bool(voice and voice.is_connected())
        current = None
        if player and not player.task.done() and player.current:
            current = track_data(player.display_track or player.current)
        source = getattr(voice, "source", None)
        payload = {"ready": bot.is_ready(), "guilds": guilds,
                   "bot": {"name": bot.user.name if bot.user else "Uminida", "avatar": str(bot.user.display_avatar.url) if bot.user else None},
                   "connected": connected, "voice_channel_id": str(voice.channel.id) if connected else None,
                   "voice_channel": voice.channel.name if connected else None,
                   "text_channel_id": str(player.ctx.channel.id) if player and hasattr(player.ctx, "channel") else None,
                   "playing": bool(voice and voice.is_playing()), "paused": bool(voice and voice.is_paused()),
                   "current": current, "queue": [track_data(t) for t in player.queue] if player else [],
                   "volume": round(player.volume * 100) if player else 50,
                   "repeat": bool(player and player.repeat), "elapsed": getattr(source, "elapsed", 0)}
        return web.json_response(payload)

    async def action(request):
        if not bot.is_ready():
            return web.json_response({"error": "A Uminida ainda está conectando ao Discord."}, status=503)
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError()
        guild = bot.get_guild(int(data["guild"]))
        if not guild:
            raise ValueError()
        kind = data.get("action")
        if kind == "add":
            query = str(data.get("query", "")).strip()
            if not query or len(query) > 500:
                return web.json_response({"error": "Digite um nome ou link com até 500 caracteres."}, status=400)
            # Restringe links de entrada a fontes musicais públicas, evitando URLs locais.
            if "://" in query:
                from urllib.parse import urlparse
                parsed = urlparse(query)
                host = (parsed.hostname or "").lower()
                if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in ("youtube.com", "youtu.be", "music.youtube.com", "soundcloud.com")):
                    return web.json_response({"error": "Use um nome, ou link HTTPS do YouTube ou SoundCloud."}, status=400)
            try:
                track, _ = await asyncio.wait_for(asyncio.to_thread(runtime["extract"], query), timeout=45)
            except AudioError as error:
                log.warning("Busca recusada pela fonte: %s", error.code)
                return web.json_response({"error": str(error), "code": error.code}, status=422)
            except Exception:
                log.exception("Busca pelo painel falhou")
                return web.json_response({"error": "Não encontrei áudio acessível. Tente outro nome ou link."}, status=422)
            async with runtime["locks"].setdefault(guild.id, asyncio.Lock()):
                voice_channel = guild.get_channel(int(data["voice"]))
                text_channel = guild.get_channel(int(data["text"]))
                if not isinstance(voice_channel, discord.VoiceChannel) or not isinstance(text_channel, discord.TextChannel):
                    raise ValueError()
                vp, tp = voice_channel.permissions_for(guild.me), text_channel.permissions_for(guild.me)
                if not (vp.view_channel and vp.connect and vp.speak and tp.view_channel and tp.send_messages):
                    return web.json_response({"error": "A Uminida precisa de acesso aos canais escolhidos."}, status=403)
                voice = guild.voice_client
                if voice and voice.channel.id != voice_channel.id:
                    return web.json_response({"error": "A Uminida já está em outro canal. Selecione o canal atual ou encerre a sessão antes."}, status=409)
                if voice and not voice.is_connected():
                    return web.json_response({"error": "A conexão de voz está sendo restabelecida. Tente em alguns segundos."}, status=409)
                player = runtime["players"].get(guild.id)
                if player and len(player.queue) >= 100:
                    return web.json_response({"error": "A fila está cheia (100 músicas)."}, status=409)
                if not voice:
                    await voice_channel.connect(self_deaf=True)
                ctx = PanelContext(bot, guild, text_channel, voice_channel)
                if player is None or player.task.done():
                    player = runtime["players"][guild.id] = runtime["Player"](ctx)
                track.requester = "Painel da Uminida"
                player.ctx = ctx
                player.queue.append(track)
                player.sync_buffer()
                player.changed.set()
            return web.json_response({"message": "Música adicionada à fila 💜"})
        player = runtime["players"].get(guild.id)
        voice = guild.voice_client
        if not player or player.task.done() or not voice or not voice.is_connected():
            return web.json_response({"error": "Adicione uma música para começar uma sessão."}, status=409)
        async with runtime["locks"].setdefault(guild.id, asyncio.Lock()):
            if kind == "pause":
                if voice.is_paused():
                    voice.resume()
                elif voice.is_playing():
                    voice.pause()
                else:
                    return web.json_response({"error": "Aguarde a música começar."}, status=409)
            elif kind == "skip":
                if not (voice.is_playing() or voice.is_paused()):
                    return web.json_response({"error": "Aguarde a música começar."}, status=409)
                player.skip = True
                voice.stop()
            elif kind == "repeat":
                player.repeat = not player.repeat
            elif kind == "volume":
                value = int(data["value"])
                if not 0 <= value <= 100:
                    raise ValueError()
                player.volume = value / 100
                if isinstance(voice.source, discord.PCMVolumeTransformer):
                    voice.source.volume = player.volume
            elif kind == "remove":
                track_id = str(data["track"])
                track = next((t for t in player.queue if t.uid == track_id), None)
                if not track:
                    return web.json_response({"error": "Essa música já saiu da fila. O painel foi atualizado."}, status=409)
                player.queue.remove(track)
            elif kind == "move":
                items = list(player.queue)
                index = next((i for i, t in enumerate(items) if t.uid == str(data["track"])), None)
                if index is None:
                    return web.json_response({"error": "A fila mudou. Tente novamente."}, status=409)
                target = max(0, min(len(items) - 1, index + int(data["direction"])))
                items.insert(target, items.pop(index))
                player.queue.clear()
                player.queue.extend(items)
            elif kind == "clear":
                player.queue.clear()
            elif kind == "stop":
                player.queue.clear()
                player.task.cancel()
                try:
                    await player.task
                except asyncio.CancelledError:
                    pass
            else:
                raise ValueError()
            await player.refresh_card()
        return web.json_response({"message": "Prontinho 💜"})

    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/action", action)
    app.router.add_get("/{name}", asset)
    return app


async def start(bot, runtime):
    public = bool(os.getenv("PORT") or os.getenv("RAILWAY_ENVIRONMENT_ID"))
    host = "0.0.0.0" if public else "127.0.0.1"
    port = int(os.getenv("PORT", "8765"))
    runner = web.AppRunner(create_app(bot, runtime), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
    except OSError:
        await runner.cleanup()
        if public:
            raise
        log.exception("Não consegui abrir o painel local. O bot continua funcionando.")
        return None
    log.info("Painel da Uminida iniciado em %s:%s", host, port)
    return runner
