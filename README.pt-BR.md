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

O instalador coloca o que estiver faltando (Python, FFmpeg, pipx), instala o mediasense e conecta ao
Claude Desktop e ao Claude Code. Depois, **feche o Claude Desktop de verdade** (ícone perto do relógio →
Quit), abra de novo e peça: *"resume esse vídeo: <link>"*.

Se já tiver tudo instalado, basta rodar `mediasense setup`. Se algo der errado, rode `mediasense doctor`.

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
