"""Prepara áudio em disco, com no máximo a faixa atual e a próxima por sessão."""
import asyncio
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from youtube_audio import resolver, ffmpeg_before, AudioError, classify


class AudioBuffer:
    def __init__(self, executable="ffmpeg", *, resolve=None, directory=None, timeout=600):
        self.executable=executable
        self.resolve=resolve or resolver.resolve
        self.directory=directory
        self.timeout=timeout
        self.jobs={}
        self.retired=set()
        self.closed=False
        self.temp=None

    def _directory(self):
        if self.temp is None:
            self.temp=tempfile.TemporaryDirectory(prefix="uminida-audio-",dir=self.directory,ignore_cleanup_errors=True)
        return Path(self.temp.name)

    @staticmethod
    def _discard_file(task):
        if task.cancelled():
            return
        try:
            path=task.result()
            path.unlink(missing_ok=True)
        except (Exception,asyncio.CancelledError):
            pass

    @staticmethod
    def _observe(task):
        if not task.cancelled():
            task.exception()

    def prefetch(self, key):
        if self.closed:
            raise RuntimeError("Sessão de áudio encerrada")
        if key not in self.jobs:
            task=asyncio.create_task(self._download(key))
            task.add_done_callback(self._observe)
            self.jobs[key]=task
        return self.jobs[key]

    async def get(self,key):
        return await asyncio.shield(self.prefetch(key))

    def retain(self,keys):
        for key in list(self.jobs):
            if key not in keys:
                task=self.jobs.pop(key)
                if not task.done():
                    task.cancel()
                    self.retired.add(task)
                    task.add_done_callback(self.retired.discard)
                task.add_done_callback(self._discard_file)

    async def close(self):
        self.closed=True
        tasks=set(self.jobs.values())|self.retired
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for task in tasks:self._discard_file(task)
        self.jobs.clear()
        self.retired.clear()
        if self.temp:
            self.temp.cleanup()
            self.temp=None

    async def _download(self,key):
        info=await asyncio.to_thread(self.resolve,key)
        duration=info.get("duration",0)
        if info.get("is_live") or not duration:
            raise AudioError("live","O modo de áudio local precisa de uma música com duração definida. Escolha uma faixa em vez de uma transmissão ao vivo.")
        if duration>3600:
            raise AudioError("length","Neste modo, escolha músicas com até 60 minutos para limitar o tempo de preparação e o espaço usado.")
        root=self._directory()
        filename=uuid4().hex
        partial=root/(filename+".part.opus")
        finished=root/(filename+".opus")
        args=[self.executable,"-hide_banner","-loglevel","error","-y"]
        args+=shlex.split(ffmpeg_before(info.get("http_headers")))
        args += ["-i",info["url"],"-vn","-c:a","libopus","-b:a","96k","-ar","48000","-ac","2",
                 "-t","3601","-f","opus",str(partial)]
        options={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
        process=None
        try:
            process=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.DEVNULL,
                                                         stderr=asyncio.subprocess.PIPE,**options)
            _,stderr=await asyncio.wait_for(process.communicate(),timeout=self.timeout)
            if process.returncode:
                error=classify(RuntimeError(stderr.decode("utf-8",errors="replace")))
                if error.code=="extract":
                    error=AudioError("download","Não consegui preparar o arquivo de áudio. Tente outra música ou confira o FFmpeg.")
                raise error
            if not partial.is_file() or partial.stat().st_size==0:
                raise AudioError("download","O download terminou sem um arquivo de áudio válido.")
            partial.replace(finished)
            return finished
        except asyncio.TimeoutError:
            raise AudioError("download_timeout","A preparação passou de 10 minutos. Tente uma música menor ou aguarde a conexão melhorar.") from None
        except OSError:
            raise AudioError("download_setup","Não consegui abrir o FFmpeg ou criar o arquivo temporário. Confira FFMPEG_PATH e o espaço livre.") from None
        finally:
            if process and process.returncode is None:
                try:process.kill()
                except ProcessLookupError:pass
                await process.communicate()
            partial.unlink(missing_ok=True)
