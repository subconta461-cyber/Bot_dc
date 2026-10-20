"""Resolução de áudio: EJS, cache breve, classificação de falhas e diagnóstico."""
import importlib.metadata
import os
import shlex
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp


class AudioError(RuntimeError):
    def __init__(self,code,message):
        self.code=code
        super().__init__(message)


def classify(error):
    text=str(error).lower().replace("’", "'")
    if "confirm you're not a bot" in text or "confirm you are not a bot" in text:
        return AudioError("youtube_auth", "O YouTube exigiu confirmação de acesso nesta hospedagem. A música não pôde ser consultada. Aguarde antes de repetir; um link do SoundCloud pode ser usado enquanto isso.")
    if "429" in text or "too many requests" in text or "request rate" in text:
        return AudioError("rate_limit", "A fonte limitou as consultas por excesso de pedidos. Aguarde um minuto antes de tentar novamente.")
    if "private video" in text or "not available" in text or "video unavailable" in text or "age" in text and "confirm" in text:
        return AudioError("unavailable", "Esse conteúdo está privado, indisponível ou exige verificação de idade. Tente outra música pública.")
    if "403" in text:
        return AudioError("forbidden", "A fonte recusou o acesso ao áudio (403). Isso pode exigir validação de acesso do YouTube; não é falta de permissão no Discord.")
    if "timed out" in text or "timeout" in text:
        return AudioError("timeout", "A fonte demorou para responder. Tente novamente em alguns instantes.")
    return AudioError("extract", "Não consegui obter áudio acessível. Tente outro nome ou link público.")


def status():
    def version(name):
        try:return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:return "ausente"
    deno=shutil.which("deno")
    deno_version="ausente"
    if deno:
        try:
            result=subprocess.run([deno,"--version"],capture_output=True,text=True,timeout=5,check=True)
            deno_version=result.stdout.splitlines()[0][:100]
        except (OSError,subprocess.SubprocessError):deno_version="encontrado, mas não respondeu"
    cookie_path=os.getenv("YOUTUBE_COOKIES_FILE","").strip()
    return {"yt_dlp":version("yt-dlp"),"ejs":version("yt-dlp-ejs"),"deno":deno_version,
            "ffmpeg":"disponível" if shutil.which(os.getenv("FFMPEG_PATH") or "ffmpeg") else "ausente",
            "authentication":"arquivo configurado" if cookie_path else "sem cookies (padrão)"}


def ffmpeg_before(headers):
    """Só encaminha cabeçalhos relevantes; impede injeção de linhas no HTTP."""
    base="-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    safe=[]
    for key in ("User-Agent","Referer","Origin"):
        value=str((headers or {}).get(key, ""))
        if value and not any(char in value for char in "\r\n\x00"):
            safe.append(f"{key}: {value}\r\n")
    return base+(" -headers "+shlex.quote("".join(safe)) if safe else "")


class Resolver:
    def __init__(self,*,clock=time.monotonic,ttl=60):
        self.clock,self.ttl=clock,ttl
        self.cache=OrderedDict()
        self.lock=threading.Lock()
        self.blocked_until=0
        self.blocked=None

    def invalidate(self, query):
        """Remove também as entradas equivalentes ao link consultado."""
        with self.lock:
            entry = self.cache.get(query)
            canonical = entry[1]["webpage_url"] if entry else query
            for key in list(self.cache):
                if key == query or self.cache[key][1]["webpage_url"] == canonical:
                    self.cache.pop(key)

    def resolve(self,query):
        query=query.strip()
        if not query:
            raise AudioError("empty","Digite o nome ou link da música.")
        host=(urlsplit(query).hostname or "").lower()
        youtube=not host or host=="youtu.be" or host=="youtube.com" or host.endswith(".youtube.com")
        with self.lock:
            now=self.clock()
            for key in list(self.cache):
                if self.cache[key][0]<=now:self.cache.pop(key)
            if query in self.cache:
                return dict(self.cache[query][1])
            if youtube and now<self.blocked_until:
                raise AudioError(self.blocked.code,str(self.blocked)+" A consulta está temporariamente pausada para evitar pedidos repetidos.")
            options={"format":"bestaudio/best","noplaylist":True,"default_search":"ytsearch1",
                     "quiet":True,"no_warnings":True,"socket_timeout":15,"retries":1,
                     "extractor_retries":1,"js_runtimes":{"deno":{}},"cachedir":False}
            cookie_path=os.getenv("YOUTUBE_COOKIES_FILE","").strip()
            if youtube and cookie_path:
                if not Path(cookie_path).is_file():
                    raise AudioError("configuration","O arquivo de autenticação configurado não existe na hospedagem. Confira YOUTUBE_COOKIES_FILE; não envie credenciais ao chat ou ao GitHub.")
                options["cookiefile"]=cookie_path
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info=ydl.extract_info(query,download=False)
                if info and "entries" in info:
                    info=next((entry for entry in info["entries"] if entry),None)
                if not info or not info.get("url"):
                    raise AudioError("no_audio","A fonte respondeu, mas não disponibilizou um formato de áudio.")
                if urlsplit(info["url"]).scheme not in ("http","https"):
                    raise AudioError("format","Esse formato de áudio não é compatível com a reprodução.")
            except AudioError:
                raise
            except Exception as error:
                failure=classify(error)
                if youtube and failure.code in ("youtube_auth","rate_limit"):
                    self.blocked_until=self.clock()+60
                    self.blocked=failure
                raise failure from error
            result={"title":info.get("title") or "Música","url":info["url"],
                    "webpage_url":info.get("webpage_url") or query,"thumbnail":info.get("thumbnail") or "",
                    "duration":int(info.get("duration") or 0),"is_live":bool(info.get("is_live")),"http_headers":dict(info.get("http_headers") or {})}
            expires=self.clock()+self.ttl
            for key in (query,result["webpage_url"]):
                self.cache[key]=(expires,result)
                self.cache.move_to_end(key)
            while len(self.cache)>64:self.cache.popitem(last=False)
            return dict(result)


resolver=Resolver()
