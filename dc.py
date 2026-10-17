"""Bot de música: execute python dc.py depois de configurar .env."""
import asyncio
import logging
import math
import os
import shutil
from dataclasses import dataclass, field
from collections import deque
from uuid import uuid4

import panel
from moderation import Moderation
from branding import UminidaContext, signed, message_kwargs, card

import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
from youtube_audio import resolver, AudioError, status as audio_status, ffmpeg_before

log = logging.getLogger("musica")


@dataclass
class Track:
    title: str
    url: str
    thumbnail: str = ""
    duration: int = 0
    requester: str = ""
    uid: str = field(default_factory=lambda: uuid4().hex)
    headers: dict = field(default_factory=dict)


class TimedAudio(discord.PCMVolumeTransformer):
    """Mede o áudio realmente enviado, incluindo pausas feitas no Discord."""
    def __init__(self, source, volume):
        super().__init__(source, volume=volume)
        self.elapsed = 0.0

    def read(self):
        data = super().read()
        if data:
            self.elapsed += 0.02
        return data


def extract(query):
    data = resolver.resolve(query)
    return Track(data["title"], data["webpage_url"], data["thumbnail"], data["duration"],
                 headers=data["http_headers"]), data["url"]


class MusicControls(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player

    async def interaction_check(self, interaction):
        player = self.player
        voice = player.ctx.guild.voice_client
        channel = getattr(getattr(interaction.user, "voice", None), "channel", None)
        if player.task.done() or player.controls is not self or not voice or channel != voice.channel:
            await interaction.response.send_message(
                **message_kwargs("Entre no canal da Uminida e use o cartão da música atual. 💜"), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Pausar", emoji="⏯️", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction, button):
        voice = self.player.ctx.guild.voice_client
        if voice.is_paused():
            voice.resume()
        elif voice.is_playing():
            voice.pause()
        await interaction.response.defer()
        await self.player.refresh_card()

    @discord.ui.button(label="Pular", emoji="⏭️")
    async def skip_button(self, interaction, button):
        voice = self.player.ctx.guild.voice_client
        await interaction.response.defer()
        self.player.skip = True
        voice.stop()

    @discord.ui.button(label="Repetir", emoji="🔁")
    async def repeat_button(self, interaction, button):
        self.player.repeat = not self.player.repeat
        await interaction.response.defer()
        await self.player.refresh_card()

    @discord.ui.button(label="Fila", emoji="🎶")
    async def queue_button(self, interaction, button):
        tracks = list(self.player.queue)
        lines = [f"{i}. {discord.utils.escape_markdown(t.title)[:100]}"
                 for i, t in enumerate(tracks[:10], 1)]
        await interaction.response.send_message(
            **message_kwargs("**Fila da Uminida 💜**\n" + ("\n".join(lines) or "Bora adicionar uma música? Use !p nome ou link.")),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def music_embed(player):
    track = player.display_track
    voice = player.ctx.guild.voice_client
    paused = bool(voice and voice.is_paused())
    embed = discord.Embed(title=track.title[:256], color=0xB69CFF,
                          description="⏸️ Pausada" if paused else "🎧 Tocando agora")
    if track.url.startswith(("https://", "http://")):
        embed.url = track.url
    embed.set_author(name="Uminida • Seu cantinho de música",
                     icon_url=player.ctx.bot.user.display_avatar.url)
    if track.thumbnail.startswith(("https://", "http://")):
        embed.set_thumbnail(url=track.thumbnail)
    duration = f"{track.duration // 60}:{track.duration % 60:02d}" if track.duration else "Ao vivo / indisponível"
    embed.add_field(name="Duração", value=duration)
    embed.add_field(name="Volume", value=f"{round(player.volume * 100)}%")
    embed.add_field(name="Repetição", value="Ligada 🔁" if player.repeat else "Desligada")
    embed.add_field(name="Pedido por", value=track.requester[:100] or "Alguém do servidor")
    embed.set_footer(text="Bora ouvir alguma coisa? 💜 • !p nome ou link")
    return signed(embed)


class Player:
    def __init__(self, ctx):
        self.ctx = ctx
        self.queue = deque()
        self.current = None
        self.repeat = False
        self.skip = False
        self.volume = 0.5
        self.closing = False
        self.card = None
        self.controls = None
        self.display_track = None
        self.done = asyncio.Event()
        self.changed = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    async def say(self, message):
        try:
            await self.ctx.send(message)
        except discord.HTTPException:
            log.warning("Não foi possível enviar mensagem no canal.")

    async def retire_card(self):
        if self.controls:
            self.controls.stop()
            for button in self.controls.children:
                button.disabled = True
        if self.card:
            try:
                await self.card.edit(view=self.controls)
            except discord.HTTPException:
                pass
        self.card = None
        self.controls = None

    async def refresh_card(self):
        if not self.card or not self.controls:
            return
        voice = self.ctx.guild.voice_client
        self.controls.pause_button.label = "Continuar" if voice and voice.is_paused() else "Pausar"
        self.controls.repeat_button.style = discord.ButtonStyle.success if self.repeat else discord.ButtonStyle.secondary
        try:
            await self.card.edit(embed=music_embed(self), view=self.controls)
        except discord.HTTPException:
            log.warning("Não consegui atualizar o cartão da música.")

    async def run(self):
        try:
            while True:
                if self.current is None:
                    if not self.queue:
                        self.changed.clear()
                        try:
                            await asyncio.wait_for(self.changed.wait(), timeout=180)
                        except asyncio.TimeoutError:
                            await self.say("Saí do canal após 3 minutos sem música.")
                            return
                        continue
                    self.current = self.queue.popleft()
                self.done.clear()
                self.skip = False
                self.display_track = None
                try:
                    track, stream = await asyncio.to_thread(extract, self.current.url)
                    voice = self.ctx.guild.voice_client
                    if not voice or not voice.is_connected():
                        return
                    source = TimedAudio(discord.FFmpegPCMAudio(
                        stream, executable=os.getenv("FFMPEG_PATH") or "ffmpeg",
                        before_options=ffmpeg_before(track.headers),
                        options="-vn"), volume=self.volume)
                    loop = asyncio.get_running_loop()
                    errors = []

                    def finished(error):
                        if error:
                            errors.append(error)
                        loop.call_soon_threadsafe(self.done.set)

                    try:
                        voice.play(source, after=finished)
                    except Exception:
                        source.cleanup()
                        raise
                    track.requester = self.current.requester
                    self.display_track = track
                    self.controls = MusicControls(self)
                    try:
                        self.card = await self.ctx.send(embed=music_embed(self), view=self.controls)
                        await self.refresh_card()
                    except discord.HTTPException:
                        await self.say(f"🎵 Tocando: {discord.utils.escape_markdown(track.title)[:250]}")
                    await self.done.wait()
                    await self.retire_card()
                    if errors:
                        raise RuntimeError("Falha durante a reprodução") from errors[0]
                except AudioError as error:
                    await self.retire_card()
                    log.warning("Falha na fonte de áudio: %s", error.code)
                    await self.say(str(error))
                    self.current = None
                    continue
                except Exception:
                    await self.retire_card()
                    log.exception("Falha ao tocar música")
                    await self.say("Não consegui tocar essa música. Vou tentar a próxima da fila.")
                    self.current = None
                    continue
                if self.skip or not self.repeat:
                    self.current = None
        finally:
            self.closing = True
            await self.retire_card()
            voice = self.ctx.guild.voice_client
            try:
                if voice:
                    await voice.disconnect(force=True)
            finally:
                if players.get(self.ctx.guild.id) is self:
                    players.pop(self.ctx.guild.id, None)


class UminidaBot(commands.Bot):
    async def get_context(self, origin, *, cls=UminidaContext):
        return await super().get_context(origin, cls=cls)

    async def setup_hook(self):
        await self.add_cog(Moderation(self))
        self.panel_runner = await panel.start(self, globals())
        self.command_sync_task = asyncio.create_task(self.sync_commands())

    async def sync_commands(self):
        await self.wait_until_ready()
        try:
            synced = await self.tree.sync()
            log.info("Comandos com / sincronizados: %s", len(synced))
        except discord.HTTPException:
            log.exception("Não consegui sincronizar os comandos com /. Os comandos com ! continuam disponíveis.")

    async def close(self):
        sync_task = getattr(self, "command_sync_task", None)
        if sync_task:
            sync_task.cancel()
            await asyncio.gather(sync_task, return_exceptions=True)
        runner = getattr(self, "panel_runner", None)
        if runner:
            await runner.cleanup()
            self.panel_runner = None
        await super().close()


intents = discord.Intents.default()
intents.message_content = True
bot = UminidaBot(command_prefix="!", intents=intents, help_command=None,
                   allowed_mentions=discord.AllowedMentions.none())
players = {}
locks = {}


@bot.check
async def guild_only(ctx):
    if ctx.guild is None:
        raise commands.CheckFailure("Use os comandos em um servidor.")
    return True


def same_channel(ctx):
    channel = getattr(ctx.author.voice, "channel", None)
    if channel is None:
        raise commands.CheckFailure("Entre em um canal de voz primeiro.")
    if ctx.voice_client and ctx.voice_client.channel != channel:
        raise commands.CheckFailure("Entre no mesmo canal de voz que o bot.")
    return channel


def active(ctx):
    same_channel(ctx)
    player = players.get(ctx.guild.id)
    if player is None or player.task.done():
        raise commands.CheckFailure("Não há reprodução ativa. Use !p nome ou URL.")
    return player


@bot.hybrid_command(aliases=["play"])
async def p(ctx, *, query: str):
    """Busca e adiciona uma música à fila."""
    same_channel(ctx)
    async with ctx.typing():
        try:
            track, _ = await asyncio.to_thread(extract, query)
            track.requester = ctx.author.display_name
        except AudioError as error:
            log.warning("Busca recusada pela fonte: %s", error.code)
            await ctx.send(str(error))
            return
        except Exception:
            log.exception("Falha na busca")
            await ctx.send("Não encontrei áudio acessível. Tente outro nome ou link.")
            return
        async with locks.setdefault(ctx.guild.id, asyncio.Lock()):
            channel = same_channel(ctx)
            if not ctx.voice_client:
                await channel.connect(self_deaf=True)
            player = players.get(ctx.guild.id)
            if player is None or player.task.done():
                player = players[ctx.guild.id] = Player(ctx)
            if len(player.queue) >= 100:
                await ctx.send("A fila está cheia (100 músicas).")
                return
            player.ctx = ctx
            player.queue.append(track)
            player.changed.set()
    await ctx.send(f"✅ Na fila: {discord.utils.escape_markdown(track.title)[:250]}")


@bot.hybrid_command(aliases=["repeat"])
async def r(ctx):
    player = active(ctx)
    player.repeat = not player.repeat
    await player.refresh_card()
    await ctx.send(f"🔁 Repetição da música atual {'ativada' if player.repeat else 'desativada'}.")


@bot.hybrid_command(aliases=["skip"])
async def pular(ctx):
    player = active(ctx)
    if not player.current or not (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        await ctx.send("Aguarde a música começar a tocar.")
        return
    player.skip = True
    ctx.voice_client.stop()
    await ctx.send("⏭️ Música pulada.")


@bot.hybrid_command(aliases=["pause"])
async def pausar(ctx):
    player = active(ctx)
    ctx.voice_client.pause()
    await player.refresh_card()
    await ctx.send("⏸️ Reprodução pausada.")


@bot.hybrid_command(aliases=["resume"])
async def continuar(ctx):
    player = active(ctx)
    ctx.voice_client.resume()
    await player.refresh_card()
    await ctx.send("▶️ Reprodução retomada.")


@bot.hybrid_command(aliases=["queue"])
async def fila(ctx):
    player = active(ctx)
    lines = [f"{i}. {discord.utils.escape_markdown(t.title)[:100]}"
             for i, t in enumerate(list(player.queue)[:15], 1)]
    current = discord.utils.escape_markdown(player.current.title)[:200] if player.current else "Nenhuma"
    await ctx.send(f"🎵 Atual: {current}\n🔁 Repetição: {'sim' if player.repeat else 'não'}\n"
                   + ("\n".join(lines) if lines else "Fila vazia.")
                   + (f"\n… {len(player.queue) - 15} outras músicas." if len(player.queue) > 15 else ""))


@bot.hybrid_command()
async def volume(ctx, valor: int):
    player = active(ctx)
    if not 0 <= valor <= 100:
        await ctx.send("Use !volume de 0 a 100.")
        return
    player.volume = valor / 100
    if isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer):
        ctx.voice_client.source.volume = player.volume
    await player.refresh_card()
    await ctx.send(f"🔊 Volume: {valor}%")


@bot.hybrid_command(aliases=["stop", "sair"])
async def parar(ctx):
    player = active(ctx)
    player.queue.clear()
    player.task.cancel()
    try:
        await player.task
    except asyncio.CancelledError:
        pass
    await ctx.send("⏹️ Reprodução encerrada e fila limpa. Saí do canal.")


@bot.hybrid_command()
async def ping(ctx):
    await ctx.send(f"🏓 {round(bot.latency * 1000)} ms")


@bot.hybrid_command(aliases=["help", "comandos"])
async def ajuda(ctx):
    embed = card("Uminida • Central de ajuda", "Seu cantinho de música e moderação. 💜\nUse **/** para preencher os campos ou **!** para digitar.")
    embed.add_field(name="🎶 Começar a ouvir", value="`!p nome ou link` — adicionar uma música\n`!fila` — consultar a fila", inline=False)
    embed.add_field(name="🎛️ Controlar o som", value="`!pausar` / `!continuar` — pausar e retomar\n`!pular` — próxima música\n`!r` — repetir a música atual\n`!volume 50` — volume entre 0 e 100\n`!parar` — limpar a fila e sair", inline=False)
    embed.add_field(name="🛡️ Cuidar do servidor", value="`!ajudamod` — ações, advertências e registros\nOs comandos respeitam suas permissões e os cargos.", inline=False)
    embed.add_field(name="✨ Utilidades", value="`!apresentação` — conheça a Uminida\n`!ping` — latência\n`!ajuda` — esta central", inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="apresentacao", aliases=["apresentação", "sobre"], description="Conheça a Uminida, veja o ping atual e os comandos de ajuda.")
async def apresentacao(ctx):
    latency = bot.latency
    ping_text = f"**{round(latency * 1000)} ms**" if math.isfinite(latency) else "Medindo minha conexão…"
    embed = card("Oi, eu sou a Uminida! 💜", "Seu cantinho de música e uma ajudinha para cuidar do servidor.\nCriada por **Mt - X-x**, estou pronta para acompanhar a resenha. ✨")
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.add_field(name="🎶 Eu cuido da trilha sonora", value="Busco músicas, organizo a fila e deixo os controles na sua mão.\nComece com **`!p nome ou link`**.", inline=False)
    embed.add_field(name="🛡️ Também ajudo na moderação", value="Banimentos, expulsões, timeout, advertências e registros — sempre respeitando as permissões e os cargos.", inline=False)
    embed.add_field(name="🏓 Meu ping agora", value=ping_text + "\nLatência da minha conexão com o Discord.", inline=False)
    embed.add_field(name="📖 Quer conhecer meus comandos?", value="**`!ajuda`** ou **`/ajuda`** — central de ajuda\n**`!ajudamod`** ou **`/ajudamod`** — moderação\n**`!ping`** ou **`/ping`** — consultar o ping novamente", inline=False)
    await ctx.send(embed=embed)


@bot.event
async def on_voice_state_update(member, before, after):
    if bot.user and member.id == bot.user.id and after.channel is None:
        player = players.get(member.guild.id)
        if player and not player.closing and not player.task.done():
            player.task.cancel()


@bot.event
async def on_command_error(ctx, error):
    if ctx.command and isinstance(ctx.command.cog, Moderation):
        return
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send("Argumento ausente ou inválido. Veja !ajuda. Exemplo: !p nome da música")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send(str(error))
    else:
        log.error("Erro em comando", exc_info=(type(error), error, error.__traceback__))
        await ctx.send("Não consegui executar o comando. Confira minhas permissões e o console do bot.")


@bot.hybrid_command(name="diagnosticoyoutube", description="Testar o acesso ao YouTube nesta hospedagem. Requer Gerenciar servidor.")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
@commands.has_guild_permissions(manage_guild=True)
@commands.cooldown(1, 60, commands.BucketType.guild)
async def diagnosticoyoutube(ctx):
    await ctx.defer(ephemeral=True)
    info = await asyncio.to_thread(audio_status)
    embed = card("YouTube • Diagnóstico da hospedagem", "Teste de consulta de áudio público. Não inicia reprodução nem usa sua conta por padrão.")
    embed.add_field(name="Extrator", value=f"yt-dlp `{info['yt_dlp']}`\nEJS `{info['ejs']}`", inline=False)
    embed.add_field(name="Dependências", value=f"Deno: {info['deno']}\nFFmpeg: {info['ffmpeg']}\nAutenticação: {info['authentication']}", inline=False)
    try:
        await asyncio.wait_for(asyncio.to_thread(extract, "https://www.youtube.com/watch?v=jNQXAC9IVRw"), timeout=45)
        result = "✅ A hospedagem conseguiu obter uma URL de áudio para o vídeo de teste. Isso ainda não testa a transmissão no Discord."
    except AudioError as error:
        result = f"**Código:** `{error.code}`\n{error}"
    except asyncio.TimeoutError:
        result = "A consulta não terminou em 45 segundos. A rede ou o serviço pode estar demorando para responder."
    except Exception:
        log.exception("Falha inesperada no diagnóstico de áudio")
        result = "O diagnóstico encontrou uma falha inesperada. Confira o console da hospedagem."
    embed.add_field(name="Resultado", value=result, inline=False)
    await ctx.send(embed=embed, ephemeral=True)


@bot.event
async def on_ready():
    log.info("Conectado como %s. Use !ajuda no servidor.", bot.user)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == "seu_token_aqui":
        raise SystemExit("Configure DISCORD_TOKEN no arquivo .env (veja .env.example).")
    if not shutil.which(os.getenv("FFMPEG_PATH") or "ffmpeg"):
        raise SystemExit("FFmpeg não encontrado. Instale-o ou configure FFMPEG_PATH no .env.")
    bot.run(token)

