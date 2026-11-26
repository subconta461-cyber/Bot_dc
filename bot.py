"""Bot de música para Discord. Execute: python bot.py"""
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands
from dotenv import load_dotenv

import music
import sources

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")
if os.getenv("DEBUG_VOICE") == "1":  # logs detalhados da voz (inclui a negociação DAVE), só para diagnóstico
    for _name in ("discord.voice_state", "discord.voice_client", "discord.player"):
        logging.getLogger(_name).setLevel(logging.DEBUG)


def load_opus():
    if discord.opus.is_loaded():
        return
    for name in ("libopus.so.0", "libopus.so", "opus"):
        try:
            discord.opus.load_opus(name)
            return
        except OSError:
            continue
    log.warning("Não consegui carregar a libopus. Sem ela o bot não envia áudio de voz.")


class MusicBot(commands.Bot):
    async def setup_hook(self):
        await self.add_cog(music.Music(self))
        await self.start_web()
        if os.getenv("SYNC_COMMANDS", "1") != "0":
            try:
                synced = await self.tree.sync()
                log.info("%d comandos / sincronizados.", len(synced))
            except Exception:
                log.exception("Falha ao sincronizar os comandos /")

    async def start_web(self):
        """Servidor mínimo: o Render exige uma porta aberta e serve de ponto para o UptimeRobot."""
        async def health(_request):
            return web.Response(text="ok")

        app = web.Application()
        app.router.add_get("/", health)
        app.router.add_get("/health", health)
        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        port = int(os.getenv("PORT", "10000"))
        await web.TCPSite(self.web_runner, "0.0.0.0", port).start()
        log.info("Servidor de saúde ouvindo na porta %d.", port)

    async def close(self):
        runner = getattr(self, "web_runner", None)
        if runner:
            await runner.cleanup()
        await super().close()

    async def on_ready(self):
        problem = sources.davey_problem()
        if problem:
            log.error("PROBLEMA DE VOZ: %s", problem)
        log.info("Conectado como %s | discord.py %s | davey %s | yt-dlp %s | opus %s",
                 self.user, sources.package_version("discord.py"), sources.package_version("davey") or "AUSENTE",
                 sources.YTDLP_VERSION, discord.opus.is_loaded())

    async def on_command_error(self, ctx, error):
        while getattr(error, "original", None) is not None:
            error = error.original
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument, commands.BadLiteralArgument)):
            text = "Argumento ausente ou inválido. Veja `!ajuda`."
        elif isinstance(error, (commands.CommandOnCooldown, discord.app_commands.CommandOnCooldown)):
            text = f"Aguarde {error.retry_after:.0f}s para usar esse comando de novo."
        elif isinstance(error, (commands.MissingPermissions, discord.app_commands.MissingPermissions)):
            text = "Você precisa da permissão **Gerenciar servidor** para isso."
        elif isinstance(error, commands.CheckFailure):
            text = str(error) or "Você não pode usar esse comando agora."
        else:
            log.error("Erro em comando", exc_info=(type(error), error, error.__traceback__))
            text = "Não consegui executar o comando. Veja os logs do Render."
        try:
            await ctx.send(text, ephemeral=True)
        except discord.HTTPException:
            pass


def main():
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("Defina a variável DISCORD_TOKEN (veja o README).")
    if not sources.ffmpeg_path():
        raise SystemExit("FFmpeg não encontrado. Instale-o ou defina FFMPEG_PATH.")
    load_opus()
    dpy, davey = sources.package_version("discord.py"), sources.package_version("davey")
    log.info("discord.py %s | davey %s | yt-dlp %s", dpy, davey or "AUSENTE", sources.YTDLP_VERSION)
    problem = sources.davey_problem()
    if problem:
        log.error("PROBLEMA DE VOZ: %s Refaça o deploy com 'Clear build cache'.", problem)
    intents = discord.Intents.default()
    intents.message_content = True
    prefix = os.getenv("PREFIX", "!").strip() or "!"
    bot = MusicBot(
        command_prefix=commands.when_mentioned_or(prefix),
        intents=intents,
        help_command=None,
        allowed_mentions=discord.AllowedMentions.none(),
        activity=discord.Activity(type=discord.ActivityType.listening, name=f"{prefix}ajuda"),
    )
    try:
        bot.run(token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Ative 'Message Content Intent' no Developer Portal (Bot > Privileged Gateway Intents).")


if __name__ == "__main__":
    main()
