"""Moderação por prefixo e slash, com confirmação, logs e advertências."""
import asyncio
import logging
import re
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from branding import card
from mod_store import Store

log = logging.getLogger("uminida.moderacao")


def duration(value):
    match = re.fullmatch(r"([1-9][0-9]{0,6})(s|m|h|d)", value.lower())
    if not match:
        raise commands.BadArgument("Use um tempo como **30s**, **10m**, **2h** ou **1d**.")
    seconds = int(match[1]) * {"s":1,"m":60,"h":3600,"d":86400}[match[2]]
    if seconds > 28 * 86400:
        raise commands.BadArgument("O timeout pode durar no máximo **28 dias**.")
    return timedelta(seconds=seconds)


def audit_reason(actor, reason):
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise commands.BadArgument("Informe um motivo com **1 a 1.000 caracteres**.")
    return f"Por {actor} (ID {actor.id}) | {reason}"[:500]


def validate_target(guild, actor, target, permission, bot_member=None, *, bot_permission=True):
    me = bot_member or guild.me
    if not getattr(actor.guild_permissions, permission):
        raise commands.CheckFailure("Você não tem a permissão necessária para essa ação.")
    if bot_permission and not getattr(me.guild_permissions, permission):
        raise commands.CheckFailure("Meu cargo não tem a permissão necessária para essa ação.")
    if target.id in {actor.id, me.id, guild.owner_id}:
        raise commands.CheckFailure("Não posso aplicar essa ação a você, a mim ou ao dono do servidor.")
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        raise commands.CheckFailure("O cargo da pessoa precisa estar **abaixo do seu**.")
    if bot_permission and target.top_role >= me.top_role:
        raise commands.CheckFailure("Meu cargo precisa ficar **acima do cargo da pessoa**.")
    if bot_permission and permission == "moderate_members" and target.guild_permissions.administrator:
        raise commands.CheckFailure("O Discord não permite timeout em administradores.")


def person(member):
    return f"{discord.utils.escape_markdown(str(member))} (`{member.id}`)"


class Confirm(discord.ui.View):
    def __init__(self, ctx, title, details, action):
        super().__init__(timeout=30)
        self.ctx,self.title,self.details,self.action=ctx,title,details,action
        self.message=None
        self.finished=False
        self.lock=asyncio.Lock()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(embed=card("Esse botão tem dono", "Só quem pediu a ação pode confirmar ou cancelar."), ephemeral=True)
            return False
        return True

    def disable(self):
        self.finished=True
        for child in self.children:
            child.disabled=True
        self.stop()

    async def on_timeout(self):
        async with self.lock:
            if self.finished:
                return
            self.disable()
            if self.message:
                try:
                    await self.message.edit(content=None,embed=card("Confirmação expirada", "O prazo de 30 segundos terminou. Nenhuma ação foi executada."),view=self)
                except discord.HTTPException:
                    pass

    @discord.ui.button(label="Confirmar",emoji="✅",style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer()
        async with self.lock:
            if self.finished:
                return
            self.disable()
            try:
                result=await self.action()
            except Exception as error:
                result=card("Não consegui concluir", error_text(error),color=0xE5A0B5)
            try:
                await interaction.edit_original_response(content=None,embed=result,view=self,allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.warning("Não consegui atualizar o cartão de confirmação.")

    @discord.ui.button(label="Cancelar",style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.defer()
        async with self.lock:
            if self.finished:
                return
            self.disable()
            await interaction.edit_original_response(content=None,embed=card("Ação cancelada", "Tudo certo. Nenhuma alteração foi feita. 💜"),view=self)


def error_text(error):
    error=getattr(error,"original",error)
    if isinstance(error,commands.BotMissingPermissions):
        return "Meu cargo não tem todas as permissões necessárias. Veja **/ajudamod**."
    if isinstance(error,commands.MissingPermissions):
        return "Você não tem permissão para usar esse comando."
    if isinstance(error,(commands.MissingRequiredArgument,commands.MemberNotFound,commands.UserNotFound)):
        return "Confira a pessoa e os campos enviados. Veja os exemplos em **/ajudamod**."
    if isinstance(error,commands.CommandOnCooldown):
        return f"Aguarde **{error.retry_after:.0f}s** antes de repetir esse comando."
    if isinstance(error,commands.MaxConcurrencyReached):
        return "Já estou processando uma ação neste canal. Aguarde terminar."
    if isinstance(error,(commands.BadArgument,commands.CheckFailure)):
        return str(error)
    if isinstance(error,discord.NotFound):
        return "Não encontrei a pessoa ou o registro. Pode ter sido removido enquanto você confirmava."
    if isinstance(error,discord.Forbidden):
        return "O Discord recusou a ação. Confira minhas permissões e a posição do meu cargo."
    if isinstance(error,discord.HTTPException):
        return "Houve uma falha de comunicação. Confira o Registro de auditoria antes de repetir a ação."
    log.error("Falha na moderação",exc_info=(type(error),error,error.__traceback__))
    return "Ocorreu uma falha ao processar a ação. Confira o console da Uminida."


class Moderation(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        self.store=Store()

    async def cog_check(self,ctx):
        if ctx.guild is None:
            raise commands.CheckFailure("Use os comandos de moderação em um servidor.")
        return True

    def require_storage(self):
        if not self.store.persistent:
            raise commands.CheckFailure("Para salvar advertências e configurações, conecte um **volume ao serviço no Railway**, montado em **/data**, e faça novo deploy. Assim os dados sobrevivem às atualizações.")

    async def fresh(self,ctx,target_id,permission,*,bot_permission=True):
        actor=await ctx.guild.fetch_member(ctx.author.id)
        target=await ctx.guild.fetch_member(target_id)
        me=await ctx.guild.fetch_member(ctx.guild.me.id)
        validate_target(ctx.guild,actor,target,permission,me,bot_permission=bot_permission)
        return actor,target

    async def record(self,ctx,action,target,reason):
        """Falhas de log nunca transformam uma ação concluída em aparente falha."""
        try:
            channel_id=await self.store.call("channel",ctx.guild.id)
            if not channel_id:
                return "\n\nℹ️ Configure **/configlogs** para receber registros em um canal."
            channel=ctx.guild.get_channel(channel_id)
            if not channel:
                return "\n\n⚠️ O canal de registros não existe mais. Use **/configlogs**."
            embed=card("Registro de moderação",f"**Ação:** {action}\n**Alvo:** {target}\n**Responsável:** {person(ctx.author)}\n**Motivo:** {discord.utils.escape_markdown(reason)[:1000]}")
            embed.timestamp=discord.utils.utcnow()
            await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
            return ""
        except Exception:
            log.exception("A ação foi concluída, mas o envio ao canal de registros falhou")
            return "\n\n⚠️ A ação foi concluída, mas não consegui enviar o registro ao canal."

    async def prompt(self,ctx,title,details,action):
        view=Confirm(ctx,title,details,action)
        embed=card(title,details+"\n\nConfirme em até **30 segundos**.")
        view.message=await ctx.send(embed=embed,view=view)

    @commands.hybrid_command(name="ban",aliases=["banir"],description="Banir um membro após confirmação.")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    @commands.cooldown(1,5,commands.BucketType.member)
    async def ban(self,ctx,membro:discord.Member,*,motivo:str):
        audit_reason(ctx.author,motivo)
        validate_target(ctx.guild,ctx.author,membro,"ban_members")
        async def execute():
            actor,target=await self.fresh(ctx,membro.id,"ban_members")
            await target.ban(reason=audit_reason(actor,motivo),delete_message_seconds=0)
            note=await self.record(ctx,"Banimento",person(target),motivo)
            return card("Banimento concluído 🔨",f"**Pessoa:** {person(target)}\n**Motivo:** {discord.utils.escape_markdown(motivo)}"+note)
        await self.prompt(ctx,"Confirmar banimento?",f"**Pessoa:** {person(membro)}\n**Motivo:** {discord.utils.escape_markdown(motivo)}\nO histórico de mensagens será preservado.",execute)

    @commands.hybrid_command(name="kick",aliases=["expulsar"],description="Expulsar um membro após confirmação.")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @commands.has_guild_permissions(kick_members=True)
    @commands.bot_has_guild_permissions(kick_members=True)
    @commands.cooldown(1,5,commands.BucketType.member)
    async def kick(self,ctx,membro:discord.Member,*,motivo:str):
        audit_reason(ctx.author,motivo)
        validate_target(ctx.guild,ctx.author,membro,"kick_members")
        async def execute():
            actor,target=await self.fresh(ctx,membro.id,"kick_members")
            await target.kick(reason=audit_reason(actor,motivo))
            note=await self.record(ctx,"Expulsão",person(target),motivo)
            return card("Expulsão concluída 👋",f"**Pessoa:** {person(target)}\n**Motivo:** {discord.utils.escape_markdown(motivo)}"+note)
        await self.prompt(ctx,"Confirmar expulsão?",f"**Pessoa:** {person(membro)}\n**Motivo:** {discord.utils.escape_markdown(motivo)}\nA pessoa poderá voltar com um novo convite.",execute)

    @commands.hybrid_command(name="timeout",aliases=["silenciar"],description="Aplicar timeout. Exemplo de tempo: 10m, 2h ou 1d.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_guild_permissions(moderate_members=True)
    async def timeout(self,ctx,membro:discord.Member,tempo:str,*,motivo:str):
        period=duration(tempo)
        audit_reason(ctx.author,motivo)
        await ctx.defer()
        actor,target=await self.fresh(ctx,membro.id,"moderate_members")
        await target.timeout(period,reason=audit_reason(actor,motivo))
        note=await self.record(ctx,"Timeout — "+tempo,person(target),motivo)
        await ctx.send(embed=card("Timeout aplicado ⏳",f"**Pessoa:** {person(target)}\n**Duração:** {tempo}\n**Motivo:** {discord.utils.escape_markdown(motivo)}"+note))

    @commands.hybrid_command(name="untimeout",description="Remover o timeout de um membro.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_guild_permissions(moderate_members=True)
    async def untimeout(self,ctx,membro:discord.Member,*,motivo:str):
        audit_reason(ctx.author,motivo)
        await ctx.defer()
        actor,target=await self.fresh(ctx,membro.id,"moderate_members")
        if not target.is_timed_out():
            return await ctx.send(embed=card("Sem timeout ativo",f"{person(target)} não está em timeout."))
        await target.timeout(None,reason=audit_reason(actor,motivo))
        note=await self.record(ctx,"Timeout removido",person(target),motivo)
        await ctx.send(embed=card("Timeout removido 💜",f"**Pessoa:** {person(target)}\n**Motivo:** {discord.utils.escape_markdown(motivo)}"+note))

    @commands.hybrid_command(name="unban",description="Desbanir uma pessoa pelo ID numérico.")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    async def unban(self,ctx,usuario_id:str,*,motivo:str):
        if not usuario_id.isdecimal() or not 15 <= len(usuario_id) <= 22:
            raise commands.BadArgument("Informe o **ID numérico** da pessoa, sem @.")
        audit_reason(ctx.author,motivo)
        await ctx.defer()
        user=discord.Object(id=int(usuario_id))
        entry=await ctx.guild.fetch_ban(user)
        await ctx.guild.unban(user,reason=audit_reason(ctx.author,motivo))
        note=await self.record(ctx,"Desbanimento",person(entry.user),motivo)
        await ctx.send(embed=card("Banimento removido 💜",f"**Pessoa:** {person(entry.user)}\nEla poderá voltar com um convite.\n**Motivo:** {discord.utils.escape_markdown(motivo)}"+note))

    @commands.hybrid_command(name="limpar",aliases=["clear","purge"],description="Confirmar limpeza de até 100 mensagens recentes, preservando fixadas.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @commands.has_permissions(manage_messages=True,read_message_history=True)
    @commands.bot_has_permissions(manage_messages=True,read_message_history=True)
    @commands.cooldown(1,10,commands.BucketType.channel)
    async def limpar(self,ctx,quantidade:int):
        if not 1 <= quantidade <= 100:
            raise commands.BadArgument("Escolha entre **1 e 100 mensagens**.")
        await ctx.defer()
        cutoff=discord.utils.utcnow()-timedelta(days=14)
        # Snapshot exato; mensagens posteriores nunca entram nesta confirmação.
        selected=[message async for message in ctx.channel.history(limit=quantidade,before=ctx.message)
                  if not message.pinned and message.created_at > cutoff]
        if not selected:
            return await ctx.send(embed=card("Nada para limpar", "Não encontrei mensagens elegíveis. Preservei as fixadas e as com mais de 14 dias."))
        async def execute():
            actor=await ctx.guild.fetch_member(ctx.author.id)
            me=await ctx.guild.fetch_member(ctx.guild.me.id)
            for member in (actor,me):
                permissions=ctx.channel.permissions_for(member)
                if not (permissions.manage_messages and permissions.read_message_history):
                    raise commands.CheckFailure("As permissões mudaram. Você e a Uminida precisam de Gerenciar mensagens e Ler histórico.")
            eligible=[]
            for candidate in selected:
                try:
                    current=await ctx.channel.fetch_message(candidate.id)
                except discord.NotFound:
                    continue
                if not current.pinned and current.created_at > discord.utils.utcnow()-timedelta(days=14):
                    eligible.append(current)
            reason=f"Limpeza de {len(eligible)} mensagens no canal {ctx.channel.id}"
            # delete_messages usa a mesma lista exibida; funciona com uma mensagem.
            if eligible:
                await ctx.channel.delete_messages(eligible,reason=audit_reason(actor,reason))
            note=await self.record(ctx,"Limpeza",f"Canal {ctx.channel.name} (`{ctx.channel.id}`)",reason)
            return card("Limpeza concluída 🧹",f"**{len(eligible)} mensagem(ns)** apagada(s).\nFixadas e mensagens novas foram preservadas."+note)
        await self.prompt(ctx,"Confirmar limpeza?",f"**Canal:** {ctx.channel.mention}\n**Mensagens selecionadas:** {len(selected)}\nFixadas e mensagens com mais de 14 dias não serão apagadas. Esta ação não pode ser desfeita.",execute)

    @commands.hybrid_command(name="configlogs",description="Escolher o canal de registros de moderação.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_guild_permissions(manage_guild=True)
    async def configlogs(self,ctx,canal:discord.TextChannel):
        self.require_storage()
        permissions=canal.permissions_for(ctx.guild.me)
        if canal.guild.id != ctx.guild.id or not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            raise commands.CheckFailure("Escolha um canal deste servidor onde eu possa **ver, enviar mensagens e inserir links**.")
        await ctx.defer(ephemeral=True)
        # Só salva a escolha se a mensagem de teste foi entregue.
        await canal.send(embed=card("Registros ativados 📋",f"Este canal receberá as ações de moderação da Uminida.\n**Configurado por:** {person(ctx.author)}"),allowed_mentions=discord.AllowedMentions.none())
        await self.store.call("configure",ctx.guild.id,canal.id)
        await ctx.send(embed=card("Canal de registros configurado",f"Os próximos registros serão enviados em {canal.mention}."),ephemeral=True)

    @commands.hybrid_command(name="avisar",description="Registrar uma advertência com motivo.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @commands.has_guild_permissions(moderate_members=True)
    async def avisar(self,ctx,membro:discord.Member,*,motivo:str):
        self.require_storage()
        audit_reason(ctx.author,motivo)
        await ctx.defer(ephemeral=True)
        _,target=await self.fresh(ctx,membro.id,"moderate_members",bot_permission=False)
        warning_id=await self.store.call("warn",ctx.guild.id,target.id,ctx.author.id,motivo)
        note=await self.record(ctx,"Advertência #"+str(warning_id),person(target),motivo)
        await ctx.send(embed=card("Advertência registrada ⚠️",f"**Registro:** #{warning_id}\n**Pessoa:** {person(target)}\n**Motivo:** {discord.utils.escape_markdown(motivo)}"+note),ephemeral=True)

    @commands.hybrid_command(name="avisos",description="Consultar advertências ativas de uma pessoa.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @commands.has_guild_permissions(moderate_members=True)
    async def avisos(self,ctx,pessoa:discord.User,pagina:int=1):
        self.require_storage()
        if not 1 <= pagina <= 10000:
            raise commands.BadArgument("Use uma página entre 1 e 10.000.")
        await ctx.defer(ephemeral=True)
        rows=await self.store.call("list",ctx.guild.id,pessoa.id,(pagina-1)*10)
        embed=card("Advertências • Página "+str(pagina),f"**Pessoa:** {person(pessoa)}\n"+("Nenhum registro ativo nesta página." if not rows else "Até 10 registros por página."))
        for row in rows:
            embed.add_field(name=f"#{row['id']} • {row['created']} UTC",value=f"{discord.utils.escape_markdown(row['reason'])[:350]}\nModerador: `{row['actor']}`",inline=False)
        await ctx.send(embed=embed,ephemeral=True)

    @commands.hybrid_command(name="removeraviso",description="Retirar uma advertência pelo número, mantendo a trilha de auditoria.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @commands.has_guild_permissions(moderate_members=True)
    async def removeraviso(self,ctx,numero:int,*,motivo:str):
        self.require_storage()
        audit_reason(ctx.author,motivo)
        await ctx.defer(ephemeral=True)
        record=await self.store.call("get",ctx.guild.id,numero)
        if not record:
            raise commands.BadArgument("Esse aviso não existe neste servidor ou já foi removido.")
        # Não permite apagar o registro de um superior para contornar a hierarquia.
        try:
            target=await ctx.guild.fetch_member(record['target'])
        except discord.NotFound:
            target=None
        if target:
            validate_target(ctx.guild,ctx.author,target,"moderate_members",bot_permission=False)
        changed=await self.store.call("remove",ctx.author.id,motivo,ctx.guild.id,numero)
        if not changed:
            raise commands.BadArgument("Esse aviso já foi removido por outra pessoa.")
        note=await self.record(ctx,f"Advertência #{numero} removida",f"Pessoa ID `{record['target']}`",motivo)
        await ctx.send(embed=card("Advertência removida",f"**Registro:** #{numero}\n**Motivo:** {discord.utils.escape_markdown(motivo)}\nO registro foi arquivado para auditoria."+note),ephemeral=True)

    @commands.hybrid_command(name="ajudamod",aliases=["moderacao"],description="Ajuda e permissões dos comandos de moderação.")
    @app_commands.guild_only()
    async def help_mod(self,ctx):
        embed=card("Moderação • Central de comandos", "Use **/** para preencher os campos ou **!** para digitar. Motivos são obrigatórios.")
        embed.add_field(name="🔨 Ações",value="`!ban @pessoa motivo`\n`!kick @pessoa motivo`\n`!timeout @pessoa 10m motivo`\n`!limpar 20`",inline=False)
        embed.add_field(name="↩️ Desfazer",value="`!unban ID motivo`\n`!untimeout @pessoa motivo`",inline=False)
        embed.add_field(name="⚠️ Advertências",value="`!avisar @pessoa motivo`\n`!avisos @pessoa [página]`\n`!removeraviso número motivo`",inline=False)
        embed.add_field(name="📋 Registros",value="`!configlogs #canal` — requer Gerenciar servidor.\nAdvertências e configurações usam armazenamento persistente.",inline=False)
        embed.add_field(name="💜 Como funciona",value="Banir, expulsar e limpar pedem confirmação por 30s.\nTimeout: s, m, h ou d; máximo 28 dias.\nCada ação exige a permissão correspondente e respeita os cargos.\nAdvertências exigem Moderar membros.",inline=False)
        await ctx.send(embed=embed,ephemeral=True)

    async def cog_command_error(self,ctx,error):
        await ctx.send(embed=card("Vamos conferir isso?",error_text(error),color=0xE5A0B5),ephemeral=True)
