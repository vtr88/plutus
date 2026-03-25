from aiogram.fsm.state import State, StatesGroup


class ExpenseFlow(StatesGroup):
    amount = State()
    description = State()
    payer = State()


class SettlementFlow(StatesGroup):
    direction = State()
    amount = State()
    note = State()
