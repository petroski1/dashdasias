# Fábrica de Cortes — agentes de IA para YouTube Shorts

Sistema automático que encontra vídeos em alta no YouTube, escolhe os melhores trechos,
edita em formato vertical com legendas e publica como Shorts.

## Os agentes

| Agente | O que faz | Tecnologia |
|---|---|---|
| **Caçador** | Lista os vídeos recentes dos canais autorizados (e buscas Creative Commons), filtra por views/idade/duração, calcula a velocidade de crescimento e pede ao Claude para escolher os que têm mais potencial de corte | YouTube Data API + Claude |
| **Baixador** | Baixa o vídeo em até 1080p | yt-dlp |
| **Transcritor** | Transcreve com o tempo exato de cada palavra | faster-whisper (local, grátis) |
| **Curador** | Lê a transcrição e escolhe os trechos de 20–58s com gancho forte, começo e fim completos; gera título, descrição e hashtags | Claude |
| **Revisor** | Confere se o corte faz sentido sozinho, se o título é fiel ao trecho e se nada viola as políticas do YouTube | Claude |
| **Editor** | Recorta, converte para 1080x1920 (fundo desfocado ou corte central), normaliza o áudio, queima legendas animadas e o gancho nos 3 primeiros segundos | ffmpeg |
| **Publicador** | Sobe no YouTube com `#Shorts`, créditos da fonte, agendamento espaçado e limite diário | YouTube Data API |

O **orquestrador** (`cortes/pipeline.py`) passa cada vídeo por essas etapas e guarda tudo num
SQLite (`dados/cortes.db`). Se algo falhar no meio, a próxima rodada continua de onde parou, e
nada é postado duas vezes.

```
Caçador → Baixador → Transcritor → Curador → Revisor → Editor → Publicador
```

## ⚠️ Direitos autorais — leia antes

Só use vídeos que você **tem direito** de recortar e republicar:

- vídeos do seu próprio canal;
- criadores que te autorizaram por escrito (muitos podcasts/streamers têm programas de cortes);
- vídeos com licença **Creative Commons (CC BY)**, com crédito.

Repostar conteúdo de outros sem autorização gera reclamações de Content ID, strikes de copyright,
desmonetização por "conteúdo reutilizado" e pode derrubar o canal. Por isso o Caçador **só aceita**
vídeos de canais listados em `canais_autorizados` ou com licença Creative Commons, e o Publicador
coloca o crédito da fonte na descrição.

## Instalação

Requisitos: Python 3.10+, ffmpeg, uma chave da API da Anthropic e uma conta Google com o canal.

```bash
git clone <este repositório> && cd dashdasias
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                 # coloque sua ANTHROPIC_API_KEY
cp config.example.yaml config.yaml   # coloque os canais autorizados e ajuste os filtros
```

### Acesso ao YouTube (uma vez só)

1. No [Google Cloud Console](https://console.cloud.google.com/), crie um projeto e ative a
   **YouTube Data API v3**.
2. Em *APIs e serviços → Tela de consentimento OAuth*, configure o app e adicione seu e-mail como
   usuário de teste.
3. Em *Credenciais → Criar credenciais → ID do cliente OAuth → App para computador*, baixe o JSON e
   salve como `client_secret.json` na raiz do projeto.
4. Rode `python -m cortes auth`, faça login com a conta **do canal onde os Shorts serão postados** e
   autorize. Isso cria o `token.json`.

> Enquanto o app OAuth estiver em modo "Teste", o Google expira o token em 7 dias e os vídeos
> enviados por apps não verificados ficam privados. Para rodar sozinho em produção, publique o app
> e peça a [auditoria da YouTube API](https://support.google.com/youtube/contact/yt_api_form).

## Uso

```bash
python -m cortes rodar --sem-publicar   # testa tudo menos o upload; veja os vídeos em dados/shorts/
python -m cortes rodar                  # uma rodada completa
python -m cortes status                 # fila e últimos Shorts publicados
python -m cortes daemon                 # roda sozinho a cada `intervalo_minutos`
```

Comece com `privacidade: private` no `config.yaml`, confira os primeiros Shorts no YouTube Studio e
só então mude para `public`. Em modo `public`, os Shorts são agendados com `intervalo_horas` entre
um e outro.

## Rodando 24h (automático)

Use um servidor (VPS) e Docker:

```bash
docker compose up -d --build
docker compose logs -f
```

Ou use o cron, sem Docker: `0 */2 * * * cd /caminho/dashdasias && .venv/bin/python -m cortes rodar`.

> Servidores de nuvem (GitHub Actions, AWS etc.) costumam ser bloqueados pelo YouTube no download.
> Uma VPS menor ou um computador ligado em casa funcionam melhor para o Baixador.

## Custos e limites

- **YouTube API:** cota grátis de 10.000 unidades por dia. Cada upload gasta 1.600 unidades, então dá
  cerca de 5 Shorts por dia (`max_postagens_por_dia: 5`). Ler os canais custa poucas unidades, e
  cada busca Creative Commons custa 100. Dá para pedir aumento de cota ao Google.
- **Claude:** usa `claude-opus-5-5` (dá para trocar com `CORTES_MODELO`). O que mais pesa é o
  Curador, que lê a transcrição inteira: um vídeo de 1 hora custa em torno de US$ 0,10–0,30. O
  Caçador e o Revisor custam centavos.
- **Transcrição:** roda localmente. Em CPU, o modelo `small` leva mais ou menos o tempo do vídeo.
  Com GPU (`dispositivo: cuda`) é bem mais rápido.

## Estrutura

```
cortes/
  agentes/       cacador, baixador, transcritor, curador, revisor, editor, publicador
  pipeline.py    orquestrador
  llm.py         chamada ao Claude com saída JSON validada
  db.py          estado (SQLite)
  youtube.py     OAuth e cliente da API
  __main__.py    linha de comando
tests/           testes (inclui edição real com ffmpeg)
```

Testes: `pip install pytest && python -m pytest`.
