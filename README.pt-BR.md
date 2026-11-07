# wordcut

**Dá ouvidos e olhos para assistentes de IA trabalharem com seus áudios e vídeos.** O wordcut transcreve
em cerca de 99 idiomas com o tempo de cada palavra e permite **editar pelo texto**: cortar frases, vícios
de linguagem e pausas longas. Ele também exporta legendas e timelines para o seu editor de vídeo, encaixa
áudios no momento certo de um vídeo e gera storyboards para a IA **assistir ao vídeo** e explicar o que
acontece.

Tudo roda no seu computador. Dá para usar pelo terminal ou conectar ao Claude (ou a qualquer cliente
[MCP](https://modelcontextprotocol.io)) e pedir em português mesmo.

```text
"Tira todos os 'é...' e encurta as pausas"     → aula.edited.mp4 + aula.edited.srt
"Em que momento ela fala do orçamento?"        → 03:41.200 → 03:42.050  "do orçamento"
"Corta de 'isso fica entre nós' até 'enfim'"   → mostra o plano, confirma e renderiza
"O que acontece nesse vídeo?"                  → resumo cena a cena com horários
"Coloca a vinheta.mp3 aos 0:12, abaixando o som original" → aula.placed.mp4
```

## Instalação

Requisitos: Python 3.10+ e [FFmpeg](https://ffmpeg.org/download.html)
(`brew install ffmpeg` · `winget install ffmpeg` · `sudo apt install ffmpeg`).

```bash
pipx install "wordcut[all] @ git+https://github.com/OWNER/wordcut"
```

A primeira transcrição baixa o modelo (cerca de 500 MB para o `small`, que é o padrão).

## Usar com o Claude

**Claude Code:** `claude mcp add wordcut -- wordcut mcp`

**Claude Desktop:** em Configurações → Desenvolvedor → Editar configuração, adicione:

```json
{ "mcpServers": { "wordcut": { "command": "wordcut", "args": ["mcp"] } } }
```

Se o Claude não encontrar o comando, use o caminho completo que aparece em `which wordcut` (macOS/Linux)
ou `where wordcut` (Windows). Reinicie o Claude e peça, por exemplo: *"transcreve ~/Desktop/reuniao.m4a e
me diz quando falaram de prazo"*.

**Skill (opcional):** copie a pasta `skill/wordcut/` para a sua pasta de skills (no Claude Code:
`~/.claude/skills/`). Ela ensina o Claude a transcrever uma vez, mostrar os cortes antes, confirmar com
você e só então renderizar.

## Linha de comando

```bash
wordcut transcribe aula.mp4                        # -> aula.transcript.json
wordcut transcribe aula.mp4 --verbatim --also srt  # mantém os "é..." para poder cortar
wordcut search aula.transcript.json "prova final"
wordcut edit aula.mp4 --fillers --silences 0.7 -n  # só mostra o que seria cortado
wordcut edit aula.mp4 --fillers --silences 0.7 --srt --edl
wordcut edit aula.mp4 --cut-between "deixa eu ver" "então" --filler-word tipo
wordcut export aula.transcript.json legenda.srt --words-per-cue 2
wordcut place video.mp4 narracao.wav --at 0:12.5 --mode duck
wordcut storyboard aula.mp4
```

O arquivo original nunca é alterado: tudo sai como `<nome>.edited.*` ou `<nome>.placed.*`.

## Bom saber

- O Whisper "limpa" a fala e costuma omitir hesitações. Use `--verbatim` quando quiser cortar vícios.
  Palavras ambíguas como "tipo" e "né" só são removidas se você pedir com `--filler-word`.
- O tempo das palavras é aproximado (0,1 a 0,2 s). Para cortes mais precisos e identificação de quem está
  falando, use `--backend whisperx`.
- O EDL abre no Premiere, DaVinci e Avid; o FCPXML abre no Final Cut e no DaVinci.

Documentação completa em inglês: [README.md](README.md).
