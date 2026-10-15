"""Identidade visual compartilhada pelas respostas do bot."""
import discord
import logging
from discord.ext import commands

SIGNATURE = "✦ Uminida | By: Mt - X-x | 💜"
log = logging.getLogger("uminida.mensagens")


def plain_payload(payload):
    parts = [payload.get("content") or ""]
    for embed in ([payload["embed"]] if payload.get("embed") else payload.get("embeds", [])):
        parts.extend([embed.title or "", embed.description or ""])
        for field in embed.fields:
            parts.append(f"**{field.name}**\n{field.value}")
    text = "\n\n".join(part for part in parts if part)
    note = "ℹ️ Permita **Inserir links** neste canal para exibir meus cartões."
    tail = "\n\n" + note + "\n" + SIGNATURE
    return {"content": text[:2000-len(tail)] + tail}


def signed(embed):
    embed = embed.copy()
    footer = (embed.footer.text or "").split("\n" + SIGNATURE)[0]
    if footer == SIGNATURE:
        footer = ""
    embed.set_footer(text=(footer[:1800] + "\n" if footer else "") + SIGNATURE)
    return embed


def card(title, text="", *, color=0xB69CFF):
    return signed(discord.Embed(title=title[:256], description=text[:4000] or None, color=color))


def message_kwargs(content=None, *, embed=None, embeds=None):
    if embed is not None:
        return {"content":content, "embed":signed(embed)}
    if embeds is not None:
        return {"content":content, "embeds":[signed(e) for e in embeds]}
    if content is not None:
        return {"content":None, "embed":card("Uminida 💜",str(content))}
    return {}


class UminidaContext(commands.Context):
    async def send(self, content=None, **kwargs):
        payload = message_kwargs(content, embed=kwargs.pop("embed",None), embeds=kwargs.pop("embeds",None))
        try:
            return await super().send(**payload, **kwargs)
        except discord.Forbidden:
            if payload.get("embed") or payload.get("embeds"):
                try:
                    return await super().send(**plain_payload(payload), **kwargs)
                except discord.Forbidden:
                    pass
            # Sem permissão de enviar texto, outra tentativa só repetiria o erro.
            log.warning("Discord recusou a resposta no canal %s. Confira Ver canal, Enviar mensagens e Inserir links.", getattr(self.channel, "id", "desconhecido"))
            return None
