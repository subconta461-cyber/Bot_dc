"""Identidade visual compartilhada pelas respostas do bot."""
import discord
from discord.ext import commands

SIGNATURE = "✦ Uminida | By: Mt - X-x | 💜"


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
        return await super().send(**payload, **kwargs)
