from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User as TelegramUser
from typing import Optional, Tuple

from plutus_bot.config import load_settings
from plutus_bot.db import BalanceSnapshot, CoupleBundle, Database, User
from plutus_bot.formatting import format_brl_from_cents, parse_amount_to_cents
from plutus_bot.states import ExpenseFlow, SettlementFlow

router = Router()

ADD_PAYER_PREFIX = "add_payer:"
SETTLE_DIRECTION_PREFIX = "settle_direction:"


def expense_payer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Eu paguei", callback_data=f"{ADD_PAYER_PREFIX}me"),
                InlineKeyboardButton(text="Meu par pagou", callback_data=f"{ADD_PAYER_PREFIX}partner"),
            ]
        ]
    )


def settlement_direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Eu paguei meu par", callback_data=f"{SETTLE_DIRECTION_PREFIX}to_partner"),
                InlineKeyboardButton(text="Meu par me pagou", callback_data=f"{SETTLE_DIRECTION_PREFIX}to_me"),
            ]
        ]
    )


def skip_note_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Pular observacao", callback_data="settle_skip_note")]
        ]
    )


def build_balance_text(snapshot: BalanceSnapshot, perspective_user_id: int) -> str:
    if snapshot.net_cents_in_favor_of_member1 == 0:
        return "Voces estao quites agora. Ninguem deve nada."

    creditor = snapshot.member1 if snapshot.net_cents_in_favor_of_member1 > 0 else snapshot.member2
    debtor = snapshot.member2 if snapshot.net_cents_in_favor_of_member1 > 0 else snapshot.member1
    amount = abs(snapshot.net_cents_in_favor_of_member1)

    if perspective_user_id == creditor.id:
        return f"<b>{escape(debtor.first_name)}</b> te deve <b>{format_brl_from_cents(amount)}</b>."
    return f"Voce deve <b>{escape(creditor.first_name)}</b> <b>{format_brl_from_cents(amount)}</b>."


def build_status_text(bundle: Optional[CoupleBundle]) -> str:
    if not bundle:
        return (
            "Voce ainda nao esta pareado.\n\n"
            "Use /parear para gerar um codigo de convite e depois peca para seu par enviar /entrar CODIGO."
        )
    if not bundle.partner:
        return (
            "Seu convite esta pronto, mas seu par ainda nao entrou.\n\n"
            f"Codigo do convite: <code>{bundle.couple.invite_code}</code>\n"
            "Peca para seu par enviar /entrar com esse codigo."
        )
    return f"Voce esta pareado com <b>{escape(bundle.partner.first_name)}</b>."


async def ensure_registered(message: Message, db: Database) -> User:
    return ensure_registered_user(message.from_user, message.chat.id, db)


def ensure_registered_user(telegram_user: Optional[TelegramUser], chat_id: int, db: Database) -> User:
    if telegram_user is None:
        raise RuntimeError("Message has no sender.")
    return db.upsert_user(
        telegram_user_id=telegram_user.id,
        chat_id=chat_id,
        first_name=telegram_user.first_name,
        username=telegram_user.username,
    )


async def require_full_pair(message: Message, db: Database) -> Optional[Tuple[User, CoupleBundle]]:
    user = ensure_registered_user(message.from_user, message.chat.id, db)
    bundle = db.get_couple_bundle_for_user(user.id)
    if not bundle:
        await message.answer("Voce precisa se parear primeiro. Use /parear para criar um codigo.")
        return None
    if not bundle.partner:
        await message.answer(
            "Seu pareamento ainda nao esta completo.\n\n"
            f"Codigo do convite: <code>{bundle.couple.invite_code}</code>"
        )
        return None
    return user, bundle


async def require_full_pair_from_callback(
    callback: CallbackQuery,
    db: Database,
) -> Optional[Tuple[User, CoupleBundle]]:
    message = callback.message
    if message is None:
        return None

    user = ensure_registered_user(callback.from_user, message.chat.id, db)
    bundle = db.get_couple_bundle_for_user(user.id)
    if not bundle:
        await message.answer("Voce precisa se parear primeiro. Use /parear para criar um codigo.")
        return None
    if not bundle.partner:
        await message.answer(
            "Seu pareamento ainda nao esta completo.\n\n"
            f"Codigo do convite: <code>{bundle.couple.invite_code}</code>"
        )
        return None
    return user, bundle


async def notify_partner(bot: Bot, partner: Optional[User], text: str) -> None:
    if not partner:
        return
    try:
        await bot.send_message(partner.chat_id, text)
    except Exception:
        logging.exception("Failed to notify partner chat_id=%s", partner.chat_id)


@router.message(Command(commands=["start", "inicio"]))
async def start_command(message: Message, db: Database) -> None:
    user = await ensure_registered(message, db)
    bundle = db.get_couple_bundle_for_user(user.id)
    await message.answer(
        "Bem-vindo ao Plutus.\n\n"
        "Este bot ajuda duas pessoas a dividir gastos compartilhados e manter um saldo atualizado.\n\n"
        f"{build_status_text(bundle)}\n\n"
        "Comandos: /parear, /entrar, /adicionar, /saldo, /historico, /acerto, /cancelar"
    )


@router.message(Command(commands=["help", "ajuda"]))
async def help_command(message: Message) -> None:
    await message.answer(
        "Comandos:\n"
        "/parear - cria um codigo de convite\n"
        "/entrar CODIGO - entra com o codigo do seu par\n"
        "/adicionar - adiciona um gasto compartilhado\n"
        "/saldo - mostra quem deve para quem\n"
        "/historico - mostra os ultimos lancamentos\n"
        "/acerto - registra um pagamento entre voces\n"
        "/cancelar - interrompe o fluxo atual"
    )


@router.message(Command(commands=["pair", "parear"]))
async def pair_command(message: Message, db: Database) -> None:
    user = await ensure_registered(message, db)
    try:
        couple = db.create_or_reuse_invite_code(user.id)
    except ValueError as exc:
        bundle = db.get_couple_bundle_for_user(user.id)
        await message.answer(f"{exc}\n\n{build_status_text(bundle)}")
        return

    await message.answer(
        "Convite criado.\n\n"
        f"Compartilhe este codigo com seu par: <code>{couple.invite_code}</code>\n"
        "Seu par deve abrir o bot e enviar /entrar com esse codigo."
    )


@router.message(Command(commands=["join", "entrar"]))
async def join_command(message: Message, command: CommandObject, db: Database, bot: Bot) -> None:
    user = await ensure_registered(message, db)
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Use o comando assim: /entrar ABC123")
        return

    try:
        couple = db.join_couple(code, user.id)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    bundle = db.get_couple_bundle_for_user(user.id)
    assert bundle is not None and bundle.partner is not None

    await message.answer(
        f"Voce agora esta pareado com <b>{escape(bundle.partner.first_name)}</b>.\n"
        "Voce ja pode usar /adicionar e /saldo."
    )

    creator = db.get_user_by_id(couple.member1_user_id)
    await notify_partner(
        bot,
        creator,
        f"<b>{escape(user.first_name)}</b> entrou no seu pareamento do Plutus.\n"
        "Agora voces ja podem registrar gastos com /adicionar.",
    )


@router.message(Command(commands=["balance", "saldo"]))
async def balance_command(message: Message, db: Database) -> None:
    user = await ensure_registered(message, db)
    snapshot = db.get_balance_snapshot(user.id)
    if not snapshot:
        bundle = db.get_couple_bundle_for_user(user.id)
        await message.answer(build_status_text(bundle))
        return
    await message.answer(build_balance_text(snapshot, user.id))


@router.message(Command(commands=["history", "historico"]))
async def history_command(message: Message, db: Database) -> None:
    pair = await require_full_pair(message, db)
    if not pair:
        return
    _, bundle = pair
    entries = db.get_recent_activity(bundle.couple.id, limit=10)
    if not entries:
        await message.answer("Ainda nao ha gastos nem acertos.")
        return

    lines = ["Historico recente:"]
    for entry in entries:
        if entry.entry_type == "expense":
            lines.append(
                f"- Gasto pago por <b>{escape(entry.actor_name)}</b>: "
                f"{format_brl_from_cents(entry.amount_cents)} em {escape(entry.description)}"
            )
        else:
            lines.append(
                f"- Acerto <b>{escape(entry.actor_name)}</b>: "
                f"{format_brl_from_cents(entry.amount_cents)}"
                + (f" ({escape(entry.description)})" if entry.description else "")
            )
    await message.answer("\n".join(lines))


@router.message(Command(commands=["add", "adicionar"]))
async def add_command(message: Message, state: FSMContext, db: Database) -> None:
    pair = await require_full_pair(message, db)
    if not pair:
        return
    await state.clear()
    await state.set_state(ExpenseFlow.amount)
    await message.answer("Qual foi o valor? Pode enviar 5, 5.0, 5,0 ou 5.00")


@router.message(ExpenseFlow.amount)
async def add_amount_step(message: Message, state: FSMContext) -> None:
    try:
        amount_cents = parse_amount_to_cents(message.text or "")
    except ValueError:
        await message.answer("Nao consegui entender esse valor. Tente algo como 5, 5.0, 5,0 ou 5.00")
        return

    await state.update_data(amount_cents=amount_cents)
    await state.set_state(ExpenseFlow.description)
    await message.answer("Foi gasto com o que?")


@router.message(ExpenseFlow.description)
async def add_description_step(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if not description:
        await message.answer("Envie uma descricao curta.")
        return

    await state.update_data(description=description)
    await state.set_state(ExpenseFlow.payer)
    await message.answer("Quem pagou isso?", reply_markup=expense_payer_keyboard())


@router.callback_query(ExpenseFlow.payer, F.data.startswith(ADD_PAYER_PREFIX))
async def add_payer_step(callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    message = callback.message
    if message is None:
        await callback.answer()
        return

    pair = await require_full_pair_from_callback(callback, db)
    if not pair:
        await callback.answer()
        await state.clear()
        return
    user, bundle = pair
    assert bundle.partner is not None

    data = await state.get_data()
    payer_choice = callback.data[len(ADD_PAYER_PREFIX):]
    paid_by_user_id = user.id if payer_choice == "me" else bundle.partner.id

    db.add_expense(
        couple_id=bundle.couple.id,
        paid_by_user_id=paid_by_user_id,
        amount_cents=int(data["amount_cents"]),
        description=str(data["description"]),
    )

    snapshot = db.get_balance_snapshot(user.id)
    assert snapshot is not None
    payer_name = user.first_name if paid_by_user_id == user.id else bundle.partner.first_name
    amount_text = format_brl_from_cents(int(data["amount_cents"]))
    description = escape(str(data["description"]))

    await callback.answer("Gasto salvo.")
    await message.answer(
        f"Gasto salvo: <b>{amount_text}</b> em <b>{description}</b>, pago por <b>{escape(payer_name)}</b>.\n"
        f"{build_balance_text(snapshot, user.id)}"
    )

    await notify_partner(
        bot,
        bundle.partner if bundle.partner.id != user.id else user,
        f"Novo gasto compartilhado: <b>{amount_text}</b> em <b>{description}</b>, pago por <b>{escape(payer_name)}</b>.\n"
        f"{build_balance_text(snapshot, bundle.partner.id)}",
    )
    await state.clear()


@router.message(Command(commands=["settle", "acerto"]))
async def settle_command(message: Message, state: FSMContext, db: Database) -> None:
    pair = await require_full_pair(message, db)
    if not pair:
        return
    await state.clear()
    await state.set_state(SettlementFlow.direction)
    await message.answer("Qual acerto aconteceu?", reply_markup=settlement_direction_keyboard())


@router.callback_query(SettlementFlow.direction, F.data.startswith(SETTLE_DIRECTION_PREFIX))
async def settle_direction_step(callback: CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    if message is None:
        await callback.answer()
        return

    direction = callback.data[len(SETTLE_DIRECTION_PREFIX):]
    await state.update_data(direction=direction)
    await state.set_state(SettlementFlow.amount)
    await callback.answer()
    await message.answer("Qual valor foi acertado? Pode enviar 5, 5.0, 5,0 ou 5.00")


@router.message(SettlementFlow.amount)
async def settle_amount_step(message: Message, state: FSMContext) -> None:
    try:
        amount_cents = parse_amount_to_cents(message.text or "")
    except ValueError:
        await message.answer("Nao consegui entender esse valor. Tente algo como 5, 5.0, 5,0 ou 5.00")
        return

    await state.update_data(amount_cents=amount_cents)
    await state.set_state(SettlementFlow.note)
    await message.answer(
        "Quer adicionar uma observacao? Pode escrever agora ou pular.",
        reply_markup=skip_note_keyboard(),
    )


@router.callback_query(SettlementFlow.note, F.data == "settle_skip_note")
async def settle_skip_note(callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    message = callback.message
    if message is None:
        await callback.answer()
        return
    await callback.answer()
    await complete_settlement(message, callback.from_user, state, db, bot, "")


@router.message(SettlementFlow.note)
async def settle_note_step(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await complete_settlement(message, message.from_user, state, db, bot, (message.text or "").strip())


async def complete_settlement(
    message: Message,
    actor: Optional[TelegramUser],
    state: FSMContext,
    db: Database,
    bot: Bot,
    note: str,
) -> None:
    user = ensure_registered_user(actor, message.chat.id, db)
    bundle = db.get_couple_bundle_for_user(user.id)
    pair = (user, bundle) if bundle and bundle.partner else None
    if not pair:
        await message.answer("Voce precisa ter um pareamento completo antes de registrar um acerto.")
        await state.clear()
        return
    user, bundle = pair
    assert bundle.partner is not None

    data = await state.get_data()
    direction = str(data["direction"])
    amount_cents = int(data["amount_cents"])

    if direction == "to_partner":
        from_user_id = user.id
        to_user_id = bundle.partner.id
        summary = f"Voce pagou <b>{escape(bundle.partner.first_name)}</b>"
        partner_summary = f"<b>{escape(user.first_name)}</b> te pagou"
    else:
        from_user_id = bundle.partner.id
        to_user_id = user.id
        summary = f"<b>{escape(bundle.partner.first_name)}</b> te pagou"
        partner_summary = f"Voce pagou <b>{escape(user.first_name)}</b>"

    db.add_settlement(
        couple_id=bundle.couple.id,
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        amount_cents=amount_cents,
        note=note,
    )

    snapshot = db.get_balance_snapshot(user.id)
    assert snapshot is not None
    amount_text = format_brl_from_cents(amount_cents)
    note_text = f"\nObservacao: {escape(note)}" if note else ""

    await message.answer(f"Acerto salvo: {summary} <b>{amount_text}</b>.{note_text}\n{build_balance_text(snapshot, user.id)}")
    await notify_partner(
        bot,
        bundle.partner if bundle.partner.id != user.id else user,
        f"Acerto salvo: {partner_summary} <b>{amount_text}</b>.{note_text}\n"
        f"{build_balance_text(snapshot, bundle.partner.id)}",
    )
    await state.clear()


@router.message(Command(commands=["cancel", "cancelar"]))
async def cancel_command(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Nao existe nenhum fluxo ativo para cancelar.")
        return
    await state.clear()
    await message.answer("Cancelado.")


@router.message()
async def fallback_message(message: Message) -> None:
    await message.answer("Nao entendi isso. Use /ajuda para ver os comandos disponiveis.")


async def configure_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="inicio", description="Mostra o status e registra voce"),
            BotCommand(command="ajuda", description="Lista os comandos do bot"),
            BotCommand(command="parear", description="Cria um codigo de convite"),
            BotCommand(command="entrar", description="Entra com o codigo do seu par"),
            BotCommand(command="adicionar", description="Adiciona um gasto compartilhado"),
            BotCommand(command="saldo", description="Mostra quem deve para quem"),
            BotCommand(command="historico", description="Mostra os ultimos lancamentos"),
            BotCommand(command="acerto", description="Registra um pagamento entre voces"),
            BotCommand(command="cancelar", description="Cancela o fluxo atual"),
        ]
    )


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(level=logging.INFO)

    database = Database(settings.database_path)
    database.initialize()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher["db"] = database

    await configure_bot_commands(bot)
    await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run())
