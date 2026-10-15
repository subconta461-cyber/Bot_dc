"""Moderação da Uminida: permissões, hierarquia e motivos no registro de auditoria."""
import asyncio
import logging
import re
from datetime import timedelta

import discord
from discord.ext import commands

log = logging.getLogger("uminida.moderacao")


def duration(value):
    match = re.fullmatch(r"([1-9][0-9]{0,6})(s|m|h|d)", value.lower())
    if not match:
        raise commands.BadArgument("Use um tempo como 30s, 10m, 2h ou 1d.")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if seconds > 28 * 86400:
        raise commands.BadArgument("O timeout pode durar no máximo 28 dias.")
    return timedelta(seconds=seconds)


def audit_reason(actor, reason):
    reason = reason.strip()
    if not reason:
        raise commands.BadArgument("Informe um motivo para a ação.")
    return f"Por {actor} (ID {actor.id}) | {reason}"[:500]


def validate_target(guild, actor, target, permission, bot_member=None):
    bot_member = bot_member or guild.me
    if not getattr(actor.guild_permissions, permission):
        raise commands.CheckFailure("Você não tem a permissão necessária para essa ação.")
    if not getattr(bot_member.guild_permissions, permission):
        raise commands.CheckFailure("Meu cargo não tem a permissão necessária para essa ação.")
    if target.id in {actor.id, guild.me.id, guild.owner_id}:
        raise commands.CheckFailure("Não posso aplicar essa ação a você, a mim ou ao dono do servidor.")
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        raise commands.CheckFailure("O cargo da pessoa precisa estar abaixo do seu.")
    if target.top_role >= bot_member.top_role:
        raise commands.CheckFailure("Meu cargo precisa ficar acima do cargo dessa pessoa.")
    if permission == "moderate_members" and target.guild_permissions.administrator:
        raise commands.CheckFailure("O Discord não permite timeout em administradores.")


class BanConfirmation(discord.ui.View):
    def __init__(self, ctx, target, reason):
        super().__init__(timeout=30)
        self.ctx, self.target_id, self.reason = ctx, target.id, reason
        self.message = None
        self.finished = False
        self.lock = asyncio.Lock()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Só quem pediu o banimento pode confirmar ou cancelar.", ephemeral=True)
            return False
        return True

    def disable(self):
        self.finished = True
        for item in self.children:
            item.disabled = True
        self.stop()

    async def on_timeout(self):
        async with self.lock:
            if self.finished:
                return
            self.disable()
            if self.message:
                try:
                    await self.message.edit(content="⌛ Confirmação expirada. Ninguém foi banido.", embed=None, view=self)
                except discord.HTTPException:
                    pass

    @discord.ui.button(label="Confirmar banimento", emoji="🔨", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer()
        async with self.lock:
            if self.finished:
                return
            self.disable()
            try:
                guild = self.ctx.guild
                # Reconsulta os cargos e permissões: podem ter mudado desde o comando.
                actor = await guild.fetch_member(self.ctx.author.id)
                target = await guild.fetch_member(self.target_id)
                bot_member = await guild.fetch_member(guild.me.id)
                validate_target(guild, actor, target, "ban_members", bot_member)
                await target.ban(reason=audit_reason(actor, self.reason), delete_message_seconds=0)
                text = f"🔨 {discord.utils.escape_markdown(str(target))} foi banido. Motivo: {discord.utils.escape_markdown(self.reason)[:700]}"
                log.info("Banimento concluído: servidor=%s alvo=%s moderador=%s", guild.id, target.id, actor.id)
            except commands.CommandError as error:
                text = str(error)
            except discord.NotFound:
                text = "A pessoa não está mais no servidor. Ninguém foi banido por este comando."
            except discord.Forbidden:
                text = "O Discord recusou o banimento. Confira meus cargos e permissões."
            except discord.HTTPException:
                text = "Não consegui confirmar o resultado com o Discord. Confira o registro de auditoria antes de tentar novamente."
            try:
                await interaction.edit_original_response(content=text, embed=None, view=self, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.warning("Não consegui atualizar a confirmação de banimento.")

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.defer()
        async with self.lock:
            if self.finished:
                return
            self.disable()
            await interaction.edit_original_response(content="Banimento cancelado. 💜", embed=None, view=self)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.CheckFailure("Use os comandos de moderação em um servidor.")
        return True

    @commands.command(name="ban", aliases=["banir"])
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def ban(self, ctx, membro: discord.Member, *, motivo: str):
        validate_target(ctx.guild, ctx.author, membro, "ban_members")
        audit_reason(ctx.author, motivo)
        view = BanConfirmation(ctx, membro, motivo)
        embed = discord.Embed(title="Confirmar banimento?", color=0xB69CFF,
                              description=f"**Pessoa:** {discord.utils.escape_markdown(str(membro))} (`{membro.id}`)\n**Motivo:** {discord.utils.escape_markdown(motivo)[:700]}")
        embed.set_footer(text="Confirme em até 30 segundos. O histórico de mensagens será preservado.")
        view.message = await ctx.send(embed=embed, view=view)

    @commands.command(name="kick", aliases=["expulsar"])
    @commands.has_guild_permissions(kick_members=True)
    @commands.bot_has_guild_permissions(kick_members=True)
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def kick(self, ctx, membro: discord.Member, *, motivo: str):
        validate_target(ctx.guild, ctx.author, membro, "kick_members")
        await membro.kick(reason=audit_reason(ctx.author, motivo))
        await ctx.send(f"👋 {discord.utils.escape_markdown(str(membro))} foi expulso. Motivo: {discord.utils.escape_markdown(motivo)[:700]}")

    @commands.command(name="timeout", aliases=["silenciar"])
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_guild_permissions(moderate_members=True)
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def timeout(self, ctx, membro: discord.Member, tempo: str, *, motivo: str):
        validate_target(ctx.guild, ctx.author, membro, "moderate_members")
        period = duration(tempo)
        await membro.timeout(period, reason=audit_reason(ctx.author, motivo))
        await ctx.send(f"⏳ {discord.utils.escape_markdown(str(membro))} recebeu timeout de {tempo}. Motivo: {discord.utils.escape_markdown(motivo)[:700]}")

    @commands.command(name="limpar", aliases=["clear", "purge"])
    @commands.has_permissions(manage_messages=True, read_message_history=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    @commands.cooldown(1, 10, commands.BucketType.channel)
    @commands.max_concurrency(1, per=commands.BucketType.channel, wait=False)
    async def limpar(self, ctx, quantidade: int):
        if not 1 <= quantidade <= 100:
            raise commands.BadArgument("Escolha uma quantidade entre 1 e 100. Exemplo: !limpar 20")
        cutoff = discord.utils.utcnow() - timedelta(days=14)
        deleted = await ctx.channel.purge(limit=quantidade, before=ctx.message,
                                          check=lambda message: not message.pinned and message.created_at > cutoff,
                                          reason=audit_reason(ctx.author, f"Limpeza de até {quantidade} mensagens no canal {ctx.channel.id}"))
        await ctx.send(f"🧹 Apaguei {len(deleted)} mensagem(ns). Preservei as fixadas e as com mais de 14 dias.", delete_after=10)

    async def cog_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if isinstance(error, commands.BotMissingPermissions):
            message = "Meu cargo não tem todas as permissões necessárias. Veja !ajudamod."
        elif isinstance(error, commands.MissingPermissions):
            message = "Você não tem permissão para usar esse comando."
        elif isinstance(error, (commands.MissingRequiredArgument, commands.MemberNotFound)):
            message = "Confira a pessoa e os argumentos. Use !ajudamod para ver exemplos."
        elif isinstance(error, commands.CommandOnCooldown):
            message = f"Aguarde {error.retry_after:.0f}s antes de repetir esse comando."
        elif isinstance(error, commands.MaxConcurrencyReached):
            message = "Já estou limpando esse canal. Aguarde terminar."
        elif isinstance(error, (commands.BadArgument, commands.CheckFailure)):
            message = str(error)
        elif isinstance(error, discord.Forbidden):
            message = "O Discord recusou a ação. Confira as permissões e a posição do meu cargo."
        elif isinstance(error, discord.HTTPException):
            message = "Houve uma falha de comunicação. Confira o registro de auditoria antes de repetir a ação."
        else:
            log.error("Erro na moderação", exc_info=(type(error), error, error.__traceback__))
            message = "Não consegui concluir a ação. Confira o console do bot."
        await ctx.send(message)

    @commands.command(name="ajudamod", aliases=["moderacao"])
    async def help_mod(self, ctx):
        embed = discord.Embed(title="Uminida • Moderação 💜", color=0xB69CFF,
            description="`!ban @pessoa motivo` — banir após confirmação\n"
                        "`!kick @pessoa motivo` — expulsar\n"
                        "`!timeout @pessoa 10m motivo` — silenciar temporariamente\n"
                        "`!limpar 20` — examinar as 20 mensagens anteriores e apagar as elegíveis")
        embed.add_field(name="Tempos", value="Use s, m, h ou d. Máximo: 28 dias.", inline=False)
        embed.add_field(name="Permissões", value="Banir membros, Expulsar membros, Moderar membros e Gerenciar mensagens, conforme o comando. Limpeza também exige Ler histórico de mensagens.", inline=False)
        embed.add_field(name="Cargos e registros", value="O seu cargo e o meu devem ficar acima do alvo. Os motivos incluem o ID do moderador no Registro de auditoria do Discord.", inline=False)
        await ctx.send(embed=embed)
