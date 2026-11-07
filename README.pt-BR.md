# mediasense

**Dá ouvidos e olhos para o Claude.** Cole um link do TikTok, YouTube ou Instagram, ou aponte um arquivo,
no Claude Code ou no Claude Desktop e peça em português mesmo. O Claude consegue resumir o vídeo,
explicar o que acontece nele, achar o momento em que algo é dito ou editar.

```text
você: resume esse vídeo https://www.tiktok.com/@alguem/video/7350…
você: o que ele mostra aos 0:30? que ferramenta é essa?
você: limpa ~/Desktop/podcast.m4a, tira os "é..." e as pausas longas
você: faz legenda curta para ~/Videos/reel.mp4
```

![mediasense no Claude: um link do TikTok é baixado, transcrito e resumido com horários](docs/demo.gif)

<sub>Exemplo ilustrativo, com um vídeo fictício.</sub>

O mediasense roda **no seu computador**: baixa o link, transcreve a fala em cerca de 99 idiomas com o
tempo de cada palavra, separa quadros nas mudanças de cena e entrega ao Claude a transcrição, a lista de
cenas e as imagens. Nada é enviado para fora.

## Instalação

**Windows**: cole no PowerShell:

```powershell
irm https://raw.githubusercontent.com/joaoestella/mediasense/main/install.ps1 | iex
```

**macOS / Linux**: cole no terminal:

```bash
curl -LsSf https://raw.githubusercontent.com/joaoestella/mediasense/main/install.sh | sh
```

Depois, **feche o Claude Desktop de verdade** (ícone perto do relógio → Quit), abra de novo e peça:
*"resume esse vídeo: <link>"*. A primeira transcrição baixa o modelo de fala, de uns 500 MB.

**Prefere ler o script antes de rodar?**

```powershell
irm https://raw.githubusercontent.com/joaoestella/mediasense/main/install.ps1 -OutFile install.ps1
notepad install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

O instalador só instala o que estiver faltando: Python 3.12 e FFmpeg pelo `winget`, e o `pipx` pelo pip.
Depois instala o mediasense deste repositório com o `pipx` e roda o `mediasense setup`. Ele para no
primeiro erro.

**Instalação manual, sem script e sem precisar de Git:**

```powershell
winget install ffmpeg
python -m pip install --user pipx
python -m pipx ensurepath                # depois feche e reabra o terminal
python -m pipx install "mediasense[all] @ https://github.com/joaoestella/mediasense/archive/refs/heads/main.zip"
mediasense setup -n                      # mostra o que vai mudar, sem mudar nada
mediasense setup
mediasense doctor
```

Use `python -m pipx` em vez de só `pipx` logo depois de instalar: o comando `pipx` só aparece num
terminal novo.

**O que o `mediasense setup` altera:**

- **Claude Desktop:** adiciona só a entrada `mcpServers.mediasense` no `claude_desktop_config.json`.
  Mantém todo o resto do arquivo e guarda a versão anterior ao lado, como `.bak`. Funciona também com
  a versão da Microsoft Store, que usa uma pasta diferente.
- **Claude Code** (se estiver instalado): roda `claude mcp add --scope user mediasense …`.
- Nada além disso. Para desfazer, apague a entrada `mediasense` do arquivo e rode
  `claude mcp remove mediasense --scope user`.

Se algo der errado, rode `mediasense doctor`.

### Ensine o Claude a usar (recomendado)

O mediasense vem com uma **skill**: um guia curto que faz o Claude usar o mediasense sempre que você
colar um link de vídeo, em vez de tentar abrir a página (o TikTok e outros sites bloqueiam).

- **Claude Code:** o `mediasense setup` instala sozinho.
- **Claude Desktop / claude.ai:** baixe o
  [mediasense-skill.zip](https://github.com/joaoestella/mediasense/releases/latest/download/mediasense-skill.zip)
  (o instalador também salva em `Downloads/mediasense/`) e, no Claude, abra **Configurações → Skills** e
  envie o arquivo. Só precisa fazer uma vez; vale para todos os seus dispositivos.

> **Importante:** no Claude Desktop, arquivos anexados no chat vão para o modelo, e não para o seu
> computador. Mande um **link ou o caminho do arquivo**. No Claude Code, dá para arrastar o arquivo
> para o terminal.

### Atalhos

No Claude Code, aparecem como `/mcp__mediasense__summarize` e assim por diante:

- `summarize`: resumo com pontos-chave e horários.
- `explain_video`: explicação cena a cena.
- `clean_up`: tira vícios e pausas, mostrando o plano antes.
- `subtitles`: gera legendas.

## Linha de comando

```bash
mediasense understand "https://youtu.be/…"
mediasense transcribe aula.mp4 -l pt --vocab "Cloudflare, Claude" --also srt
mediasense edit aula.mp4 --fillers --silences 0.7 -n   # só mostra o plano
mediasense edit aula.mp4 --fillers --silences 0.7 --srt --edl
mediasense export aula.transcript.json legenda.srt --words-per-cue 2
mediasense place video.mp4 narracao.wav --at 0:12.5 --mode duck
```

O original nunca é alterado: o resultado sai como `<nome>.edited.*` ou `<nome>.placed.*`.

## Bom saber

- **Nomes próprios:** o Whisper pode escrever "Cloud Fair" no lugar de Cloudflare. Use `--vocab`. Pelo
  Claude, isso é feito automaticamente quando ele consegue deduzir os nomes do título ou da tela.
- **Vícios de fala:** use `--verbatim` para mantê-los na transcrição e poder cortar. Palavras reais são
  protegidas por idioma: "um" é artigo em português e nunca é cortado.
- **Velocidade:** com placa NVIDIA e as bibliotecas CUDA instaladas, usa a GPU; senão, vai pela CPU.
- **Downloads:** baixar de links segue os termos de cada site. Use só para conteúdo que você pode baixar.

Documentação completa em inglês: [README.md](README.md).
