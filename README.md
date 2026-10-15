# Bot de música para Discord

Bot em Python com comandos em português, busca por nome/URL, fila por servidor e repetição da música atual. A reprodução usa discord.py, yt-dlp e FFmpeg. O computador precisa permanecer ligado e o processo em execução.

## Preparação no Windows

1. Instale Python 3.11 ou superior **de 64 bits** e habilite sua inclusão no PATH. As dependências de voz podem exigir compilação no Python de 32 bits.
2. Instale FFmpeg e adicione a pasta `bin` ao PATH, ou configure `FFMPEG_PATH` no `.env`.
3. Para suporte completo ao YouTube, instale também o runtime JavaScript Deno e deixe-o no PATH. Consulte as instruções atuais do [yt-dlp](https://github.com/yt-dlp/yt-dlp#dependencies).
4. No [Discord Developer Portal](https://discord.com/developers/applications), crie uma aplicação e acesse **Bot** para obter o token. Ative **Message Content Intent** em **Privileged Gateway Intents**.
5. Em **OAuth2 → URL Generator**, selecione o escopo `bot` e as permissões **View Channels**, **Send Messages**, **Read Message History**, **Connect** e **Speak**. Abra a URL gerada e adicione o bot ao servidor. Confira também as permissões específicas do canal de voz.

No terminal, dentro desta pasta:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U -r requirements.txt
Copy-Item .env.example .env
```

Edite `.env` e substitua `seu_token_aqui` pelo token do bot. Mantenha esse arquivo privado; não envie o token no chat nem publique no GitHub.

```powershell
.\.venv\Scripts\python.exe dc.py
```

Você também pode abrir `iniciar.bat`, que sempre usa o Python da `.venv`.
Para conferir os imports sem conectar ao Discord, execute `.\.venv\Scripts\python.exe verificar.py`.
O arquivo `requirements-core.txt` serve para diagnóstico dos módulos básicos; não substitui a instalação completa de `requirements.txt` para voz.

## Comandos

Entre em um canal de voz e envie os comandos em um canal de texto do servidor:

| Comando | Ação |
| --- | --- |
| `!p nome da música` ou `!p https://...` | Busca e toca; adiciona à fila se já houver uma música |
| `!r` | Liga/desliga repetição da mesma música ao terminar |
| `!pular` | Pula a música atual, inclusive quando a repetição está ligada |
| `!pausar` / `!continuar` | Pausa/retoma |
| `!fila` | Mostra música atual e próximas músicas |
| `!volume 50` | Volume de 0 a 100; padrão 50 |
| `!parar` ou `!sair` | Limpa fila, desliga repetição e desconecta |
| `!ping` | Mostra latência |
| `!ajuda` | Lista comandos |

Os controles de música exigem estar no mesmo canal de voz do bot. A repetição mantém a fila aguardando até ser desligada ou a música ser pulada. O bot sai após 3 minutos com a fila vazia. URLs de playlists não importam a playlist inteira.

## Problemas comuns

- **Não responde:** confira o Message Content Intent, o prefixo `!` e as permissões no canal de texto.
- **Não entra ou não toca:** confira Connect/Speak, FFmpeg e o console. Links privados, protegidos, regionais ou que exigem login podem falhar. Nem todo serviço é compatível; Spotify não fornece áudio diretamente por este bot.
- **YouTube falha:** atualize as dependências com o comando de instalação acima e confira a instalação do Deno. Alguns vídeos podem ser bloqueados pelo provedor.

Referências: [discord.py](https://discordpy.readthedocs.io/en/latest/) e [yt-dlp](https://github.com/yt-dlp/yt-dlp).
