"""Cog de música: fila, loop, volume, botões de controle e diagnóstico."""
import asyncio
import logging
import os
import random
import shutil
import tempfile
from collections import deque
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

import sources
from sources import AudioError, Track

log = logging.getLogger("musica")


def env_int(name, default, low, high):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


IDLE_TIMEOUT = env_int("IDLE_TIMEOUT", 180, 30, 3600)
MAX_QUEUE = env_int("MAX_QUEUE", 100, 1, 500)
DEFAULT_VOLUME = env_int("DEFAULT_VOLUME", 50, 0, 100)
COLOR = 0x7C5CFF
LOOP_NEXT = {"off": "faixa", "faixa": "fila", "fila": "off"}
LOOP_LABEL = {"off": "desligado", "faixa": "repetir faixa", "fila": "repetir fila"}


def clean(text, limit=200):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))[:limit]


def fmt(seconds):
    if not seconds:
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class TimedSource(discord.PCMVolumeTransformer):
    """Conta quanto áudio realmente foi enviado (cada leitura = 20 ms)."""

    def __init__(self, source, volume):
        super().__init__(source, volume=volume)
        self.elapsed = 0.0

    def read(self):
        data = super().read()
        if data:
            self.elapsed += 0.02
        return data


class Controls(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player

    async def interaction_check(self, interaction):
        vc = self.player.guild.voice_client
        channel = getattr(getattr(interaction.user, "voice", None), "channel", None)
        if vc is None or channel is None or channel != vc.channel:
            await interaction.response.send_message("Entre no mesmo canal de voz do bot para usar os botões.", ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary)
    async def btn_toggle(self, interaction, button):
        vc = self.player.guild.voice_client
        if vc.is_paused():
            vc.resume()
            text = "▶️ Retomado."
        elif vc.is_playing():
            vc.pause()
            text = "⏸️ Pausado."
        else:
            text = "Nada está tocando agora."
        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def btn_skip(self, interaction, button):
        text = "⏭️ Pulada." if self.player.skip() else "Nada está tocando agora."
        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary)
    async def btn_loop(self, interaction, button):
        self.player.loop = LOOP_NEXT[self.player.loop]
        await interaction.response.send_message(f"🔁 Loop: {LOOP_LABEL[self.player.loop]}.", ephemeral=True)
        await self.player.refresh_message()

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def btn_stop(self, interaction, button):
        await interaction.response.send_message("⏹️ Parei e saí do canal.", ephemeral=True)
        await self.player.destroy()


class GuildPlayer:
    def __init__(self, cog, guild, text_channel):
        self.cog, self.guild, self.text = cog, guild, text_channel
        self.queue = deque()
        self.current = None
        self.loop = "off"
        self.volume = cog.volumes.get(guild.id, DEFAULT_VOLUME / 100)
        self.source = None
        self.skipped = False
        self.closing = False
        self.announced = None
        self.message = None
        self.wake = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    # ---------- controle ----------
    def skip(self):
        vc = self.guild.voice_client
        if self.current is None or vc is None or not (vc.is_playing() or vc.is_paused()):
            return False
        self.skipped = True
        vc.stop()
        return True

    def set_volume(self, percent):
        self.volume = percent / 100
        self.cog.volumes[self.guild.id] = self.volume
        if self.source:
            self.source.volume = self.volume

    async def say(self, text):
        try:
            await self.text.send(text)
        except discord.HTTPException:
            pass

    def embed(self, track=None):
        track = track or self.current
        e = discord.Embed(title=f"🎵 {track.title[:240]}", url=track.page_url, color=COLOR)
        e.add_field(name="Duração", value=fmt(track.duration))
        e.add_field(name="Pedido por", value=clean(track.requester or "—", 60))
        e.add_field(name="Volume", value=f"{round(self.volume * 100)}%")
        e.add_field(name="Loop", value=LOOP_LABEL[self.loop])
        e.add_field(name="Na fila", value=str(len(self.queue)))
        if track.thumbnail.startswith("http"):
            e.set_thumbnail(url=track.thumbnail)
        return e

    def queue_embed(self):
        e = discord.Embed(title="📜 Fila", color=COLOR)
        if self.current:
            e.add_field(name="Tocando agora", value=f"{clean(self.current.title, 100)} `{fmt(self.current.duration)}`", inline=False)
        upcoming = list(self.queue)
        if upcoming:
            lines = [f"`{i}.` {clean(t.title, 80)} `{fmt(t.duration)}`" for i, t in enumerate(upcoming[:10], 1)]
            if len(upcoming) > 10:
                lines.append(f"… e mais {len(upcoming) - 10}")
            e.add_field(name="A seguir", value="\n".join(lines), inline=False)
        else:
            e.add_field(name="A seguir", value="Fila vazia.", inline=False)
        e.set_footer(text=f"Loop: {LOOP_LABEL[self.loop]} • Volume: {round(self.volume * 100)}%")
        return e

    async def refresh_message(self):
        if self.message and self.current:
            try:
                await self.message.edit(embed=self.embed())
            except discord.HTTPException:
                pass

    async def retire_message(self):
        message, self.message = self.message, None
        if message:
            try:
                await message.edit(view=None)
            except discord.HTTPException:
                pass

    async def announce_when_ready(self, source, track):
        """Só anuncia 'Tocando agora' quando o áudio realmente começou a sair."""
        if self.announced is track:
            return
        for _ in range(40):
            await asyncio.sleep(0.25)
            if source is not self.source:
                return
            if source.elapsed > 0.3:
                break
        else:
            return
        if self.announced is track or self.closing:
            return
        self.announced = track
        await self.retire_message()
        try:
            self.message = await self.text.send(embed=self.embed(track), view=Controls(self))
        except discord.HTTPException:
            self.message = None

    # ---------- reprodução ----------
    async def play_once(self, vc, track, target, before):
        """Toca uma tentativa. Devolve (segundos enviados, últimas linhas do FFmpeg, erro)."""
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        result = {}
        err = tempfile.TemporaryFile()
        try:
            ffmpeg = discord.FFmpegPCMAudio(target, executable=sources.ffmpeg_path() or "ffmpeg",
                                            before_options=before, options="-vn", stderr=err)
            source = TimedSource(ffmpeg, self.volume)

            def after(error):
                result["error"] = error
                loop.call_soon_threadsafe(done.set)

            self.source = source
            try:
                vc.play(source, after=after)
            except Exception:
                source.cleanup()
                raise
            self.cog.spawn(self.announce_when_ready(source, track))
            await done.wait()
            err.seek(0)
            tail = err.read().decode("utf-8", "replace").strip().splitlines()[-3:]
            return source.elapsed, tail, result.get("error")
        finally:
            self.source = None
            err.close()

    async def play_track(self, vc, track):
        """Tenta 3 formas: transmissão completa, transmissão simples e download. True se tocou."""
        try:
            if not track.fresh():
                await asyncio.to_thread(sources.refresh, track)
        except AudioError as error:
            await self.say(f"⚠️ {error.message}")
            return False
        except Exception:
            log.exception("Falha ao atualizar a URL de %s", track.title)
            await self.say("⚠️ Não consegui carregar essa música. Pulando.")
            return False

        tmpdir = None
        tail = []
        try:
            for attempt in range(3):
                if self.closing or not vc.is_connected():
                    return False
                if attempt == 0:
                    target, before, label = track.stream_url, sources.before_full(track.headers), "transmissão"
                elif attempt == 1:
                    target, before, label = track.stream_url, sources.BEFORE_SIMPLE, "transmissão simples"
                else:
                    await self.say(f"⏳ Baixando **{clean(track.title, 150)}** para tocar (a transmissão falhou).")
                    tmpdir = tempfile.mkdtemp(prefix="musica-")
                    try:
                        target = await asyncio.to_thread(sources.download, track.page_url, tmpdir)
                    except Exception as error:
                        log.warning("Download falhou: %s", error)
                        break
                    before, label = sources.BEFORE_SIMPLE, "download"
                elapsed, tail, error = await self.play_once(vc, track, target, before)
                if error:
                    log.warning("Erro do player (%s): %s", label, error)
                if self.skipped or elapsed >= 1.0:
                    if attempt:
                        log.info("Tocou usando o modo: %s", label)
                    return True
                log.warning("Sem áudio no modo '%s' (%.2fs): %s", label, elapsed, " | ".join(tail))
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

        detail = ("\n```\n" + "\n".join(tail)[-350:] + "\n```") if tail else ""
        await self.say(f"⚠️ Não consegui tocar **{clean(track.title, 150)}**: o FFmpeg não recebeu áudio da fonte.{detail}")
        return False

    async def run(self):
        try:
            while True:
                if self.current is None:
                    if not self.queue:
                        self.wake.clear()
                        try:
                            await asyncio.wait_for(self.wake.wait(), IDLE_TIMEOUT)
                        except asyncio.TimeoutError:
                            await self.say("👋 Saí do canal por inatividade.")
                            return
                        continue
                    self.current = self.queue.popleft()
                    self.announced = None
                vc = self.guild.voice_client
                if vc is None or not vc.is_connected():
                    return
                track = self.current
                self.skipped = False
                played = await self.play_track(vc, track)
                if played and self.loop == "faixa" and not self.skipped:
                    continue
                await self.retire_message()
                if played and self.loop == "fila":
                    self.queue.append(track)
                self.current = None
        finally:
            await self.cleanup()

    async def cleanup(self):
        if self.closing:
            return
        self.closing = True
        await self.retire_message()
        vc = self.guild.voice_client
        if vc:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        if self.cog.players.get(self.guild.id) is self:
            del self.cog.players[self.guild.id]

    async def destroy(self):
        if not self.task.done() and self.task is not asyncio.current_task():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.cleanup()


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}
        self.locks = {}
        self.volumes = {}
        self.bg = set()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.bg.add(task)
        task.add_done_callback(self.bg.discard)

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage("Use os comandos dentro de um servidor.")
        return True

    def require_voice(self, ctx):
        channel = getattr(ctx.author.voice, "channel", None)
        if channel is None:
            raise commands.CheckFailure("Entre em um canal de voz primeiro.")
        if ctx.voice_client and ctx.voice_client.channel != channel:
            raise commands.CheckFailure("Entre no mesmo canal de voz do bot.")
        return channel

    def active(self, ctx):
        self.require_voice(ctx)
        player = self.players.get(ctx.guild.id)
        if player is None or player.task.done():
            raise commands.CheckFailure("Não há nada tocando. Use `!play nome ou link`.")
        return player

    # ---------- comandos ----------
    @commands.hybrid_command(name="play", aliases=["p"], description="Toca uma música pelo nome ou link (SoundCloud).")
    @app_commands.describe(busca="Nome da música ou link")
    async def play(self, ctx, *, busca: str):
        channel = self.require_voice(ctx)
        me = ctx.guild.me
        perms = channel.permissions_for(me)
        if not (perms.connect and perms.speak):
            await ctx.send("⚠️ Eu preciso das permissões **Conectar** e **Falar** nesse canal de voz.")
            return
        await ctx.defer()
        try:
            track = await asyncio.to_thread(sources.extract, busca)
        except AudioError as error:
            await ctx.send(f"⚠️ {error.message}")
            return
        except Exception:
            log.exception("Falha inesperada na busca")
            await ctx.send("⚠️ Não consegui buscar essa música agora.")
            return
        track.requester = ctx.author.display_name
        lock = self.locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            channel = self.require_voice(ctx)
            player = self.players.get(ctx.guild.id)
            if player and len(player.queue) >= MAX_QUEUE:
                await ctx.send(f"A fila está cheia ({MAX_QUEUE} músicas).")
                return
            if ctx.voice_client is None:
                try:
                    await channel.connect(self_deaf=True)
                except Exception:
                    log.exception("Falha ao conectar no canal de voz")
                    await ctx.send("⚠️ Não consegui entrar no canal de voz.")
                    return
            if player is None or player.task.done():
                player = GuildPlayer(self, ctx.guild, ctx.channel)
                self.players[ctx.guild.id] = player
            player.text = ctx.channel
            player.queue.append(track)
            position = len(player.queue)
            player.wake.set()
        where = f"posição {position} na fila" if player.current else "preparando para tocar"
        await ctx.send(f"✅ **{clean(track.title, 200)}** — {where}.")

    @commands.hybrid_command(name="skip", aliases=["pular", "s"], description="Pula a música atual.")
    async def skip(self, ctx):
        player = self.active(ctx)
        await ctx.send("⏭️ Pulada." if player.skip() else "Nada está tocando agora.")

    @commands.hybrid_command(name="pause", aliases=["pausar"], description="Pausa a música.")
    async def pause(self, ctx):
        self.active(ctx)
        if ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Pausado.")
        else:
            await ctx.send("Nada está tocando agora.")

    @commands.hybrid_command(name="resume", aliases=["continuar"], description="Retoma a música pausada.")
    async def resume(self, ctx):
        self.active(ctx)
        if ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Retomado.")
        else:
            await ctx.send("A música não está pausada.")

    @commands.hybrid_command(name="stop", aliases=["parar", "sair", "leave"], description="Para tudo, limpa a fila e sai do canal.")
    async def stop(self, ctx):
        player = self.active(ctx)
        await ctx.send("⏹️ Parei e saí do canal.")
        await player.destroy()

    @commands.hybrid_command(name="queue", aliases=["fila", "q"], description="Mostra a fila.")
    async def queue(self, ctx):
        await ctx.send(embed=self.active(ctx).queue_embed())

    @commands.hybrid_command(name="nowplaying", aliases=["np", "tocando"], description="Mostra a música atual.")
    async def nowplaying(self, ctx):
        player = self.active(ctx)
        if player.current is None:
            await ctx.send("Nada está tocando agora.")
        else:
            await ctx.send(embed=player.embed())

    @commands.hybrid_command(name="loop", aliases=["repetir", "l"], description="Repete a faixa, a fila ou desliga.")
    @app_commands.describe(modo="off, faixa ou fila (vazio = alterna)")
    async def loop_cmd(self, ctx, modo: Optional[Literal["off", "faixa", "fila"]] = None):
        player = self.active(ctx)
        player.loop = modo or LOOP_NEXT[player.loop]
        await ctx.send(f"🔁 Loop: **{LOOP_LABEL[player.loop]}**.")
        await player.refresh_message()

    @commands.hybrid_command(name="volume", aliases=["vol", "v"], description="Mostra ou muda o volume (0 a 100).")
    @app_commands.describe(nivel="Volume de 0 a 100")
    async def volume(self, ctx, nivel: Optional[commands.Range[int, 0, 100]] = None):
        player = self.active(ctx)
        if nivel is None:
            await ctx.send(f"🔊 Volume atual: **{round(player.volume * 100)}%**")
            return
        player.set_volume(nivel)
        await ctx.send(f"🔊 Volume: **{nivel}%**")
        await player.refresh_message()

    @commands.hybrid_command(name="shuffle", aliases=["misturar"], description="Embaralha a fila.")
    async def shuffle(self, ctx):
        player = self.active(ctx)
        items = list(player.queue)
        random.shuffle(items)
        player.queue.clear()
        player.queue.extend(items)
        await ctx.send("🔀 Fila embaralhada.")

    @commands.hybrid_command(name="remove", aliases=["remover"], description="Remove uma música da fila pela posição.")
    @app_commands.describe(posicao="Número da música na fila")
    async def remove(self, ctx, posicao: commands.Range[int, 1, 500]):
        player = self.active(ctx)
        if posicao > len(player.queue):
            await ctx.send("Essa posição não existe na fila.")
            return
        items = list(player.queue)
        removed = items.pop(posicao - 1)
        player.queue.clear()
        player.queue.extend(items)
        await ctx.send(f"🗑️ Removida: **{clean(removed.title, 150)}**")

    @commands.hybrid_command(name="clear", aliases=["limpar"], description="Limpa a fila (a música atual continua).")
    async def clear(self, ctx):
        player = self.active(ctx)
        total = len(player.queue)
        player.queue.clear()
        await ctx.send(f"🧹 Fila limpa ({total} músicas removidas).")

    @commands.hybrid_command(name="ajuda", aliases=["help", "comandos"], description="Lista os comandos.")
    async def ajuda(self, ctx):
        p = ctx.clean_prefix
        e = discord.Embed(title="🎶 Comandos", color=COLOR)
        e.description = (
            f"`{p}play <nome ou link>` (`{p}p`) — toca ou adiciona à fila\n"
            f"`{p}skip` (`{p}s`) — pula\n"
            f"`{p}pause` / `{p}resume` — pausa / retoma\n"
            f"`{p}stop` — para, limpa a fila e sai\n"
            f"`{p}queue` (`{p}q`) — mostra a fila\n"
            f"`{p}nowplaying` (`{p}np`) — música atual\n"
            f"`{p}loop [off|faixa|fila]` — repetição\n"
            f"`{p}volume [0-100]` — volume\n"
            f"`{p}shuffle` — embaralha a fila\n"
            f"`{p}remove <n>` — remove da fila\n"
            f"`{p}clear` — limpa a fila\n"
            f"`{p}diagnostico` — testa busca e FFmpeg (Gerenciar servidor)\n\n"
            "Todos também funcionam como comandos `/`."
        )
        await ctx.send(embed=e)

    @commands.hybrid_command(name="diagnostico", description="Testa a busca e o FFmpeg nesta hospedagem (Gerenciar servidor).")
    @app_commands.describe(busca="Música para testar (padrão: lofi hip hop)")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_guild_permissions(manage_guild=True)
    @commands.cooldown(1, 20, commands.BucketType.guild)
    async def diagnostico(self, ctx, *, busca: str = "lofi hip hop"):
        await ctx.defer(ephemeral=True)
        ffmpeg = sources.ffmpeg_path()
        provider = os.getenv("SEARCH_PROVIDER", "soundcloud")
        lines = [
            f"yt-dlp: {sources.YTDLP_VERSION}",
            f"FFmpeg: {ffmpeg or 'AUSENTE'}",
            f"Opus carregado: {'sim' if discord.opus.is_loaded() else 'NÃO'}",
            f"Busca por nome: {provider}",
            f"Teste: {busca[:80]}",
        ]
        try:
            track = await asyncio.wait_for(asyncio.to_thread(sources.extract, busca), timeout=45)
            kind = "HLS (m3u8)" if ".m3u8" in track.stream_url.lower() else "arquivo direto"
            lines.append(f"Achou: {track.title[:80]} [{kind}]")
            if not ffmpeg:
                raise AudioError("ffmpeg", "FFmpeg não encontrado.")
            for label, before in (("completa", sources.before_full(track.headers)), ("simples", sources.BEFORE_SIMPLE)):
                code, size, tail = await asyncio.to_thread(sources.probe, ffmpeg, track.stream_url, before)
                ok = code == 0 and size > 100000
                lines.append(f"{'✅' if ok else '⚠️'} Leitura {label}: código {code}, {size // 1000} KB de áudio")
                if not ok and tail:
                    lines.append("   " + " | ".join(tail)[-250:])
        except AudioError as error:
            lines.append(f"⚠️ [{error.code}] {error.message}")
        except asyncio.TimeoutError:
            lines.append("⚠️ A busca passou de 45 segundos.")
        except Exception:
            log.exception("Falha no diagnóstico")
            lines.append("⚠️ Falha inesperada (veja os logs).")
        await ctx.send("```\n" + "\n".join(lines)[:1850] + "\n```", ephemeral=True)

    # ---------- eventos de voz ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        player = self.players.get(member.guild.id)
        if player is None:
            return
        if member.id == self.bot.user.id and after.channel is None:
            await player.destroy()
            return
        vc = member.guild.voice_client
        if vc and vc.channel and before.channel == vc.channel and member.id != self.bot.user.id:
            if not any(not m.bot for m in vc.channel.members):
                self.spawn(self.leave_if_empty(member.guild.id, vc.channel))

    async def leave_if_empty(self, guild_id, channel):
        await asyncio.sleep(60)
        player = self.players.get(guild_id)
        if player and player.guild.voice_client and player.guild.voice_client.channel == channel \
                and not any(not m.bot for m in channel.members):
            await player.say("👋 Saí porque o canal ficou vazio.")
            await player.destroy()
