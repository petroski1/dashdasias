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
| **Editor** | Recorta, converte para 1080x1920 (fundo desfocado ou corte central), normaliza o áudio, coloca legendas animadas, o gancho nos 3 primeiros segundos e a barra de progresso | [HyperFrames](https://github.com/heygen-com/hyperframes) (padrão) ou ffmpeg |
| **Capista** | Separa frames do trecho, o Claude olha as imagens e escolhe a melhor (rosto expressivo, nítido), indica onde está a pessoa e escreve o texto da capa (2–5 palavras); monta a capa vertical com texto grande e palavra em destaque | Claude (visão) + Pillow |
| **Publicador** | Sobe no YouTube com `#Shorts`, créditos da fonte, capa, agendamento espaçado e limite diário | YouTube Data API |

```
Caçador → Baixador → Transcritor → Curador → Revisor → Editor → Capista → Publicador
```

### Equipe administrativa

Acima dos especialistas tem uma equipe que gerencia o trabalho:

| Agente | Papel | Como funciona |
|---|---|---|
| **Gerente** (orquestrador) | Comanda a rodada: olha o painel, resolve problemas, manda planejar e executar, pausa as publicações se algo sério acontecer e escreve um relatório | Claude com ferramentas (`agentes/gerente.py`) |
| **Planejador** | Transforma o estado de cada vídeo/corte em tarefas ("baixar v1", "editar corte 12"), sem duplicar e respeitando o limite diário e as pausas | Código, sem IA: é uma regra fixa, então fica rápido, de graça e nunca esquece nada |
| **Executor** | Executa as tarefas encadeando as etapas e trata os erros: tenta de novo, adia, descarta o item ou chama um humano, explicando em português o que aconteceu e o que fazer | Código + Claude para diagnosticar erros desconhecidos |

Erros conhecidos o Executor resolve sem IA: cota do YouTube esgotada (adia 6h), login expirado ou
chave inválida (chama você), vídeo removido ou privado (descarta). Depois de 3 tentativas sem
sucesso, a tarefa é **escalada** para você.

Cada rodada gera um relatório em `dados/relatorios/`. Tudo fica num SQLite (`dados/cortes.db`).
Se o processo cair no meio, a próxima rodada continua de onde parou, e nada é postado duas vezes.

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

Requisitos: Python 3.10+, ffmpeg, Node.js 22+ (para o HyperFrames), uma chave da API da Anthropic e uma
conta Google com o canal.

```bash
git clone <este repositório> && cd dashdasias
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cd hyperframes && npm install && npm run preparar && cd ..   # HyperFrames + Chrome de renderização

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
python -m cortes rodar                  # uma rodada completa, comandada pelo Gerente
python -m cortes rodar --sem-gerente    # só Planejador + Executor (mais barato, sem relatório)
python -m cortes status                 # fila, o que precisa de você e últimos Shorts publicados
python -m cortes tarefas                # todas as tarefas abertas, com o diagnóstico
python -m cortes reabrir 12             # depois de resolver o problema, reabre a tarefa 12
python -m cortes cancelar 12            # desiste da tarefa 12 (e do vídeo ou corte dela)
python -m cortes retomar-publicacoes    # tira a pausa que o Gerente colocou
python -m cortes daemon                 # roda sozinho a cada `intervalo_minutos`
```

Comece com `privacidade: private` no `config.yaml`, confira os primeiros Shorts no YouTube Studio e
só então mude para `public`. Em modo `public`, os Shorts são agendados com `intervalo_horas` entre
um e outro.

### Sobre as capas

- O YouTube só aceita capa personalizada em **canais verificados por telefone**
  (youtube.com/verify). Sem isso, o Short sobe normalmente, mas sem a capa (o erro só aparece no log).
- A capa aparece na página do canal, na busca e nos vídeos sugeridos. No feed de rolar Shorts, o
  YouTube costuma mostrar o próprio vídeo, não a capa.
- As capas ficam em `dados/shorts/*.jpg`, ao lado de cada vídeo. Para desligar, use `capa.ativo: false`.

## HyperFrames (edição dos vídeos)

O Editor monta cada Short a partir do modelo `hyperframes/short/index.html`, um arquivo HTML com
animações GSAP: o gancho entra "saltando", a palavra falada acende na cor de destaque e uma barra de
progresso corre embaixo. Para cada corte, o Editor copia o modelo, coloca o trecho do vídeo e os
dados do corte em `corte.js` e chama `hyperframes render`.

Para mudar o visual, edite `hyperframes/short/index.html` e veja ao vivo:

```bash
cd hyperframes
npm run demo      # cria um vídeo de exemplo
npm run preview   # abre o estúdio do HyperFrames no navegador
```

Cada Short leva cerca de 1 minuto de renderização para cada 8 segundos de vídeo em CPU. Se precisar
de velocidade, use `editor.motor: ffmpeg` no `config.yaml` (sem animações, bem mais rápido).

As **skills do HyperFrames** para o Claude Code estão em `.claude/skills/`. Abra o Claude Code na
pasta do projeto e peça, por exemplo, "use /talking-head-recut para colocar cards animados neste
corte" ou "melhore o modelo hyperframes/short com /hyperframes". O HyperFrames envia estatísticas
de uso anônimas; para desligar: `cd hyperframes && npx hyperframes telemetry disable` (no Docker já
vem desligado).

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
  Capista envia 8 frames pequenos por Short (cerca de US$ 0,01). O Caçador, o Revisor, o Gerente e
  o diagnóstico de erros custam centavos por rodada.
- **Transcrição:** roda localmente. Em CPU, o modelo `small` leva mais ou menos o tempo do vídeo.
  Com GPU (`dispositivo: cuda`) é bem mais rápido.

## Estrutura

```
cortes/
  agentes/       cacador, baixador, transcritor, curador, revisor, editor, capista, publicador
  agentes/       gerente, planejador, executor (equipe administrativa)
  trabalhos.py   uma função por tipo de tarefa, chamando o especialista certo
  pipeline.py    ponto de entrada de uma rodada
  llm.py         chamada ao Claude com saída JSON validada
  db.py          estado (SQLite)
  youtube.py     OAuth e cliente da API
  __main__.py    linha de comando
hyperframes/     modelo do Short em HTML (short/index.html) e instalação do HyperFrames
.claude/skills/  skills do HyperFrames para o Claude Code
tests/           testes (inclui edição real com ffmpeg e HyperFrames)
```

Testes: `pip install pytest && python -m pytest`.
