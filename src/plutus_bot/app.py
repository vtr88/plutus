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
                InlineKeyboardButton(text="I paid", callback_data=f"{ADD_PAYER_PREFIX}me"),
                InlineKeyboardButton(text="My partner paid", callback_data=f"{ADD_PAYER_PREFIX}partner"),
            ]
        ]
    )


def settlement_direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="I paid my partner", callback_data=f"{SETTLE_DIRECTION_PREFIX}to_partner"),
                InlineKeyboardButton(text="My partner paid me", callback_data=f"{SETTLE_DIRECTION_PREFIX}to_me"),
            ]
        ]
    )


def skip_note_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Skip note", callback_data="settle_skip_note")]
        ]
    )


def build_balance_text(snapshot: BalanceSnapshot, perspective_user_id: int) -> str:
    if snapshot.net_cents_in_favor_of_member1 == 0:
        return "You are square right now. Nobody owes anything."

    creditor = snapshot.member1 if snapshot.net_cents_in_favor_of_member1 > 0 else snapshot.member2
    debtor = snapshot.member2 if snapshot.net_cents_in_favor_of_member1 > 0 else snapshot.member1
    amount = abs(snapshot.net_cents_in_favor_of_member1)

    if perspective_user_id == creditor.id:
        return f"<b>{escape(debtor.first_name)}</b> owes you <b>{format_brl_from_cents(amount)}</b>."
    return f"You owe <b>{escape(creditor.first_name)}</b> <b>{format_brl_from_cents(amount)}</b>."


def build_status_text(bundle: CoupleBundle | None) -> str:
    if not bundle:
        return (
            "You are not paired yet.\n\n"
            "Use /pair to generate an invite code, then ask your partner to send /join CODE."
        )
    if not bundle.partner:
        return (
            "Your invite is ready, but your partner has not joined yet.\n\n"
            f"Invite code: <code>{bundle.couple.invite_code}</code>\n"
            "Ask your partner to send /join with that code."
        )
    return f"You are paired with <b>{escape(bundle.partner.first_name)}</b>."


async def ensure_registered(message: Message, db: Database) -> User:
    return ensure_registered_user(message.from_user, message.chat.id, db)


def ensure_registered_user(telegram_user: TelegramUser | None, chat_id: int, db: Database) -> User:
    if telegram_user is None:
        raise RuntimeError("Message has no sender.")
    return db.upsert_user(
        telegram_user_id=telegram_user.id,
        chat_id=chat_id,
        first_name=telegram_user.first_name,
        username=telegram_user.username,
    )


async def require_full_pair(message: Message, db: Database) -> tuple[User, CoupleBundle] | None:
    user = ensure_registered_user(message.from_user, message.chat.id, db)
    bundle = db.get_couple_bundle_for_user(user.id)
    if not bundle:
        await message.answer("You need to pair first. Use /pair to create an invite code.")
        return None
    if not bundle.partner:
        await message.answer(
            "Your pair is not complete yet.\n\n"
            f"Invite code: <code>{bundle.couple.invite_code}</code>"
        )
        return None
    return user, bundle


async def require_full_pair_from_callback(
    callback: CallbackQuery,
    db: Database,
) -> tuple[User, CoupleBundle] | None:
    message = callback.message
    if message is None:
        return None

    user = ensure_registered_user(callback.from_user, message.chat.id, db)
    bundle = db.get_couple_bundle_for_user(user.id)
    if not bundle:
        await message.answer("You need to pair first. Use /pair to create an invite code.")
        return None
    if not bundle.partner:
        await message.answer(
            "Your pair is not complete yet.\n\n"
            f"Invite code: <code>{bundle.couple.invite_code}</code>"
        )
        return None
    return user, bundle


async def notify_partner(bot: Bot, partner: User | None, text: str) -> None:
    if not partner:
        return
    try:
        await bot.send_message(partner.chat_id, text)
    except Exception:
        logging.exception("Failed to notify partner chat_id=%s", partner.chat_id)


@router.message(Command("start"))
async def start_command(message: Message, db: Database) -> None:
    user = await ensure_registered(message, db)
    bundle = db.get_couple_bundle_for_user(user.id)
    await message.answer(
        "Welcome to Plutus.\n\n"
        "This bot helps two people split shared expenses and keep a running balance.\n\n"
        f"{build_status_text(bundle)}\n\n"
        "Commands: /pair, /join, /add, /balance, /history, /settle, /cancel"
    )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Commands:\n"
        "/pair - create an invite code\n"
        "/join CODE - join your partner\n"
        "/add - add a shared expense\n"
        "/balance - see who owes whom\n"
        "/history - show recent entries\n"
        "/settle - record a repayment\n"
        "/cancel - stop the current flow"
    )


@router.message(Command("pair"))
async def pair_command(message: Message, db: Database) -> None:
    user = await ensure_registered(message, db)
    try:
        couple = db.create_or_reuse_invite_code(user.id)
    except ValueError as exc:
        bundle = db.get_couple_bundle_for_user(user.id)
        await message.answer(f"{exc}\n\n{build_status_text(bundle)}")
        return

    await message.answer(
        "Invite created.\n\n"
        f"Share this code with your partner: <code>{couple.invite_code}</code>\n"
        "They should open the bot and send /join with this code."
    )


@router.message(Command("join"))
async def join_command(message: Message, command: CommandObject, db: Database, bot: Bot) -> None:
    user = await ensure_registered(message, db)
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Send the command like this: /join ABC123")
        return

    try:
        couple = db.join_couple(code, user.id)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    bundle = db.get_couple_bundle_for_user(user.id)
    assert bundle is not None and bundle.partner is not None

    await message.answer(
        f"You are now paired with <b>{escape(bundle.partner.first_name)}</b>.\n"
        "You can start using /add and /balance."
    )

    creator = db.get_user_by_id(couple.member1_user_id)
    await notify_partner(
        bot,
        creator,
        f"<b>{escape(user.first_name)}</b> joined your Plutus pair.\n"
        "You can now start tracking expenses with /add.",
    )


@router.message(Command("balance"))
async def balance_command(message: Message, db: Database) -> None:
    user = await ensure_registered(message, db)
    snapshot = db.get_balance_snapshot(user.id)
    if not snapshot:
        bundle = db.get_couple_bundle_for_user(user.id)
        await message.answer(build_status_text(bundle))
        return
    await message.answer(build_balance_text(snapshot, user.id))


@router.message(Command("history"))
async def history_command(message: Message, db: Database) -> None:
    pair = await require_full_pair(message, db)
    if not pair:
        return
    _, bundle = pair
    entries = db.get_recent_activity(bundle.couple.id, limit=10)
    if not entries:
        await message.answer("No expenses or settlements yet.")
        return

    lines = ["Recent activity:"]
    for entry in entries:
        if entry.entry_type == "expense":
            lines.append(
                f"- Expense by <b>{escape(entry.actor_name)}</b>: "
                f"{format_brl_from_cents(entry.amount_cents)} for {escape(entry.description)}"
            )
        else:
            lines.append(
                f"- Settlement <b>{escape(entry.actor_name)}</b>: "
                f"{format_brl_from_cents(entry.amount_cents)}"
                + (f" ({escape(entry.description)})" if entry.description else "")
            )
    await message.answer("\n".join(lines))


@router.message(Command("add"))
async def add_command(message: Message, state: FSMContext, db: Database) -> None:
    pair = await require_full_pair(message, db)
    if not pair:
        return
    await state.clear()
    await state.set_state(ExpenseFlow.amount)
    await message.answer("What was the amount? Example: 42,90")


@router.message(ExpenseFlow.amount)
async def add_amount_step(message: Message, state: FSMContext) -> None:
    try:
        amount_cents = parse_amount_to_cents(message.text or "")
    except ValueError:
        await message.answer("I could not read that amount. Try something like 42,90")
        return

    await state.update_data(amount_cents=amount_cents)
    await state.set_state(ExpenseFlow.description)
    await message.answer("What was it for?")


@router.message(ExpenseFlow.description)
async def add_description_step(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if not description:
        await message.answer("Please send a short description.")
        return

    await state.update_data(description=description)
    await state.set_state(ExpenseFlow.payer)
    await message.answer("Who paid for it?", reply_markup=expense_payer_keyboard())


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
    payer_choice = callback.data.removeprefix(ADD_PAYER_PREFIX)
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

    await callback.answer("Expense saved.")
    await message.answer(
        f"Saved expense: <b>{amount_text}</b> for <b>{description}</b>, paid by <b>{escape(payer_name)}</b>.\n"
        f"{build_balance_text(snapshot, user.id)}"
    )

    await notify_partner(
        bot,
        bundle.partner if bundle.partner.id != user.id else user,
        f"New shared expense: <b>{amount_text}</b> for <b>{description}</b>, paid by <b>{escape(payer_name)}</b>.\n"
        f"{build_balance_text(snapshot, bundle.partner.id)}",
    )
    await state.clear()


@router.message(Command("settle"))
async def settle_command(message: Message, state: FSMContext, db: Database) -> None:
    pair = await require_full_pair(message, db)
    if not pair:
        return
    await state.clear()
    await state.set_state(SettlementFlow.direction)
    await message.answer("Which repayment happened?", reply_markup=settlement_direction_keyboard())


@router.callback_query(SettlementFlow.direction, F.data.startswith(SETTLE_DIRECTION_PREFIX))
async def settle_direction_step(callback: CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    if message is None:
        await callback.answer()
        return

    direction = callback.data.removeprefix(SETTLE_DIRECTION_PREFIX)
    await state.update_data(direction=direction)
    await state.set_state(SettlementFlow.amount)
    await callback.answer()
    await message.answer("What amount was settled? Example: 25,00")


@router.message(SettlementFlow.amount)
async def settle_amount_step(message: Message, state: FSMContext) -> None:
    try:
        amount_cents = parse_amount_to_cents(message.text or "")
    except ValueError:
        await message.answer("I could not read that amount. Try something like 25,00")
        return

    await state.update_data(amount_cents=amount_cents)
    await state.set_state(SettlementFlow.note)
    await message.answer(
        "Optional note? You can type one now, or skip it.",
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
    actor: TelegramUser | None,
    state: FSMContext,
    db: Database,
    bot: Bot,
    note: str,
) -> None:
    user = ensure_registered_user(actor, message.chat.id, db)
    bundle = db.get_couple_bundle_for_user(user.id)
    pair = (user, bundle) if bundle and bundle.partner else None
    if not pair:
        await message.answer("You need a complete pair before recording settlements.")
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
        summary = f"You paid <b>{escape(bundle.partner.first_name)}</b>"
        partner_summary = f"<b>{escape(user.first_name)}</b> paid you"
    else:
        from_user_id = bundle.partner.id
        to_user_id = user.id
        summary = f"<b>{escape(bundle.partner.first_name)}</b> paid you"
        partner_summary = f"You paid <b>{escape(user.first_name)}</b>"

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
    note_text = f"\nNote: {escape(note)}" if note else ""

    await message.answer(f"Settlement saved: {summary} <b>{amount_text}</b>.{note_text}\n{build_balance_text(snapshot, user.id)}")
    await notify_partner(
        bot,
        bundle.partner if bundle.partner.id != user.id else user,
        f"Settlement saved: {partner_summary} <b>{amount_text}</b>.{note_text}\n"
        f"{build_balance_text(snapshot, bundle.partner.id)}",
    )
    await state.clear()


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("There is no active flow to cancel.")
        return
    await state.clear()
    await message.answer("Canceled.")


@router.message()
async def fallback_message(message: Message) -> None:
    await message.answer("I didn't understand that. Use /help to see the available commands.")


async def configure_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Register and show status"),
            BotCommand(command="pair", description="Create an invite code"),
            BotCommand(command="join", description="Join with an invite code"),
            BotCommand(command="add", description="Add a shared expense"),
            BotCommand(command="balance", description="Show who owes whom"),
            BotCommand(command="history", description="Show recent activity"),
            BotCommand(command="settle", description="Record a repayment"),
            BotCommand(command="cancel", description="Cancel the current flow"),
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
