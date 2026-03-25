# Plutus

Bot privado no Telegram para duas pessoas dividirem gastos compartilhados e acompanharem quem deve para quem.

## O que esta versao faz

- Registra cada pessoa com `/inicio` ou `/start`
- Cria um pareamento com `/parear` e `/entrar CODIGO`
- Adiciona gastos compartilhados com `/adicionar`
- Mostra o saldo atual com `/saldo`
- Lista os ultimos lancamentos com `/historico`
- Registra pagamentos entre voces com `/acerto`
- Notifica a outra pessoa sempre que um gasto ou acerto for salvo

## Stack

- Python 3.9+
- `aiogram` para o bot do Telegram
- SQLite para armazenamento
- Long polling, entao voce nao precisa de webhook publico na v1

## Setup

1. Crie o bot no `@BotFather` e copie o token.
2. Copie `.env.example` para `.env`.
3. Preencha `BOT_TOKEN`.
4. Crie e ative um ambiente virtual.
5. Instale as dependencias.
6. Rode o bot.

Exemplo:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
python -m plutus_bot
```

## Telegram flow

1. Voce e sua esposa abrem o bot e enviam `/start` ou `/inicio`.
2. Uma pessoa envia `/parear` e compartilha o codigo.
3. A outra pessoa envia `/entrar CODIGO`.
4. Depois disso, qualquer um dos dois pode usar `/adicionar`, `/saldo`, `/historico` e `/acerto`.

## Observacoes

- O bot usa long polling, entao precisa ficar rodando em algum lugar para receber mensagens.
- O SQLite fica em `data/plutus.sqlite3` por padrao.
- O valor aceita formatos simples como `5`, `5.0`, `5,0`, `5.00` ou `5,00`.
- Para valores com centavo impar, a divisao arredonda um centavo a favor de quem pagou. Exemplo: `R$ 10,01` faz a outra pessoa dever `R$ 5,00`.

## Rodando Como Servico

Existe uma unidade `systemd` pronta em `deploy/plutus.service`.

No servidor:

```bash
sudo cp deploy/plutus.service /etc/systemd/system/plutus.service
sudo systemctl daemon-reload
sudo systemctl enable --now plutus
sudo systemctl status plutus
```

Logs:

```bash
journalctl -u plutus -f
```
