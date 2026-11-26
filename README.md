# 🎶 Bot de música para Discord

Bot de música com fila, loop e volume. Busca no **SoundCloud** por padrão e roda no **Render** (Docker).

## Comandos

Funcionam com `!` e também como comandos `/`.

| Comando | O que faz |
|---|---|
| `!play <nome ou link>` (`!p`) | Toca ou adiciona à fila |
| `!skip` (`!s`) | Pula a música |
| `!pause` / `!resume` | Pausa / retoma |
| `!stop` | Para, limpa a fila e sai do canal |
| `!queue` (`!q`) | Mostra a fila |
| `!nowplaying` (`!np`) | Música atual |
| `!loop [off\|faixa\|fila]` | Repetição (sem argumento, alterna) |
| `!volume [0-100]` | Mostra ou muda o volume |
| `!shuffle` | Embaralha a fila |
| `!remove <n>` | Remove uma música da fila |
| `!clear` | Limpa a fila |
| `!teste` | Toca um bipe de 4s para testar a voz |
| `!diagnostico` | Testa versões, busca e FFmpeg (precisa de *Gerenciar servidor*) |

A mensagem "Tocando agora" tem botões: ⏯️ pausar/retomar, ⏭️ pular, 🔁 loop, ⏹️ parar.

## Como subir no Render

1. **Developer Portal** (discord.com/developers) → seu app → **Bot**:
   - ative **Message Content Intent** (Privileged Gateway Intents);
   - clique em **Reset Token** e copie o novo token (veja o aviso de segurança abaixo).
2. **GitHub**: apague os arquivos antigos do repositório e envie os deste projeto
   (`bot.py`, `music.py`, `sources.py`, `requirements.txt`, `Dockerfile`, `.gitignore`, `.dockerignore`).
   **Nunca envie o arquivo `.env`.**
3. **Render** → seu serviço → **Environment**: defina `DISCORD_TOKEN` com o token novo.
   As variáveis antigas (como `UMINIDA_AUDIO_MODE`) podem ser apagadas.
4. **Manual Deploy** → *Deploy latest commit*. Espere ficar **Live** e aparecer `Conectado como ...` nos Logs.
5. **Manter o bot acordado (importante no plano Free):** o Render desliga o serviço após alguns minutos sem
   visitas. Crie um monitor gratuito no **UptimeRobot** (tipo HTTP(s)) apontando para
   `https://SEU-SERVICO.onrender.com/health`, com intervalo de 5 minutos.
6. No Discord, entre num canal de voz e use `!play` com o nome de uma música.

> ⚠️ **Segurança do token:** se o arquivo `.env` do projeto antigo foi enviado ao GitHub em algum momento,
> o token antigo está exposto. Por isso o passo 1 manda gerar um novo.

## Se não sair som

0. **DAVE (criptografia de voz):** desde março de 2026 o Discord exige DAVE nas chamadas. Isso precisa do
   `discord.py` 2.7.1 ou mais novo **e** da biblioteca `davey` (já no `requirements.txt`). Sem elas, o bot entra
   na call, aparece "falando", mas ninguém ouve nada. No Render, use **Manual Deploy → Clear build cache & deploy**
   para garantir que as versões novas sejam instaladas. Nos Logs deve aparecer `discord.py 2.7.x | davey x.y.z`.
   Se aparecer `davey AUSENTE`, é esse o problema.
1. Use `/teste` dentro de um canal de voz: o bot toca um bipe de 4 segundos. Se você ouvir, a voz está ok e o problema
   é a música. Se não ouvir, o problema é a voz.
2. Use `/diagnostico` no servidor. Ele mostra a versão do yt-dlp, se o FFmpeg e o Opus estão ok,
   se a busca achou a música e se o FFmpeg consegue ler o áudio.
3. Confira se o bot tem as permissões **Conectar** e **Falar** no canal de voz.
4. Veja a aba **Logs** do Render logo depois de pedir a música. O bot tenta 3 formas de tocar
   (transmissão completa, transmissão simples e download) e registra qual falhou e por quê.

## Erro 429 com página HTML nos Logs

Se os Logs mostrarem `429 Too Many Requests` seguido de uma página HTML (`<!doctype html>...`), o **IP compartilhado
da hospedagem está bloqueado pelo Cloudflare do Discord**. O bot continua conectado e a voz funciona, mas as
**respostas em texto falham**. Isso acontece no Render (e no Railway/Replit) porque vários bots dividem o mesmo IP
e a equipe do Render informa que não é possível trocar o IP de saída. O bot foi feito para tocar a música mesmo assim.
Se persistir por dias, o caminho é outra hospedagem (VPS, PC próprio) ou um proxy de IP fixo.

## YouTube

Links do YouTube costumam ser bloqueados em hospedagens como o Render (erro "confirme que você não é um bot").
Por isso a busca por nome usa o SoundCloud. Para buscar no YouTube mesmo assim, defina `SEARCH_PROVIDER=youtube`
(sem garantia de funcionar nessa hospedagem).

## Variáveis opcionais

| Variável | Padrão | Descrição |
|---|---|---|
| `PREFIX` | `!` | Prefixo dos comandos |
| `SEARCH_PROVIDER` | `soundcloud` | `soundcloud` ou `youtube` (busca por nome) |
| `DEFAULT_VOLUME` | `50` | Volume inicial (0 a 100) |
| `IDLE_TIMEOUT` | `180` | Segundos sem música até o bot sair |
| `MAX_QUEUE` | `100` | Tamanho máximo da fila |
| `FFMPEG_PATH` | — | Caminho do FFmpeg (só se não estiver no PATH) |
| `SYNC_COMMANDS` | `1` | `0` para não sincronizar os comandos `/` ao iniciar |

## Rodar no PC (opcional)

```
pip install -r requirements.txt
copie .env.example para .env e preencha DISCORD_TOKEN
python bot.py
```
É preciso ter o FFmpeg instalado e no PATH.
