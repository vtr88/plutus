# Plutus

Private Telegram bot for two people to split shared expenses and keep a running balance.

## What this version does

- Registers each person with `/start`
- Creates a pair with `/pair` and `/join CODE`
- Adds shared expenses with `/add`
- Shows the current net balance with `/balance`
- Lists recent activity with `/history`
- Records repayments with `/settle`
- Notifies the other person whenever an expense or settlement is saved

## Stack

- Python 3.9+
- `aiogram` for the Telegram bot
- SQLite for storage
- Long polling, so you do not need a public webhook for v1

## Setup

1. Create your bot with `@BotFather` and copy the token.
2. Copy `.env.example` to `.env`.
3. Fill in `BOT_TOKEN`.
4. Create and activate a virtual environment.
5. Install dependencies.
6. Run the bot.

Example:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
python -m plutus_bot
```

## Telegram flow

1. You and your wife both open the bot and send `/start`.
2. One person sends `/pair` and shares the code.
3. The other person sends `/join CODE`.
4. After that, either of you can use `/add`, `/balance`, `/history`, and `/settle`.

## Notes

- The bot uses long polling, so it must keep running somewhere to receive messages.
- SQLite is stored at `data/plutus.sqlite3` by default.
- Amount input accepts simple formats like `5`, `5.0`, `5,0`, `5.00`, or `5,00`.
- For odd cent values, the split rounds one cent in favor of the payer. Example: `R$ 10,01` means the other person owes `R$ 5,00`.
