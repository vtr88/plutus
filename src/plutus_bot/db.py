from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class User:
    id: int
    telegram_user_id: int
    chat_id: int
    first_name: str
    username: Optional[str]
    created_at: str


@dataclass
class Couple:
    id: int
    invite_code: str
    member1_user_id: int
    member2_user_id: Optional[int]
    created_at: str


@dataclass
class CoupleBundle:
    couple: Couple
    you: User
    partner: Optional[User]


@dataclass
class ActivityEntry:
    entry_type: str
    actor_name: str
    amount_cents: int
    description: str
    created_at: str


@dataclass
class BalanceSnapshot:
    couple: Couple
    member1: User
    member2: User
    net_cents_in_favor_of_member1: int


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    chat_id INTEGER NOT NULL,
                    first_name TEXT NOT NULL,
                    username TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS couples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invite_code TEXT NOT NULL UNIQUE,
                    member1_user_id INTEGER NOT NULL REFERENCES users(id),
                    member2_user_id INTEGER REFERENCES users(id),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    couple_id INTEGER NOT NULL REFERENCES couples(id),
                    paid_by_user_id INTEGER NOT NULL REFERENCES users(id),
                    amount_cents INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settlements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    couple_id INTEGER NOT NULL REFERENCES couples(id),
                    from_user_id INTEGER NOT NULL REFERENCES users(id),
                    to_user_id INTEGER NOT NULL REFERENCES users(id),
                    amount_cents INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def upsert_user(
        self,
        *,
        telegram_user_id: int,
        chat_id: int,
        first_name: str,
        username: Optional[str],
    ) -> User:
        created_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (telegram_user_id, chat_id, first_name, username, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    first_name = excluded.first_name,
                    username = excluded.username
                """,
                (telegram_user_id, chat_id, first_name, username, created_at),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        return self._row_to_user(row)

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_telegram_id(self, telegram_user_id: int) -> Optional[User]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_couple_bundle_for_user(self, user_id: int) -> Optional[CoupleBundle]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM couples
                WHERE member1_user_id = ? OR member2_user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id, user_id),
            ).fetchone()
        if not row:
            return None

        couple = self._row_to_couple(row)
        you = self.get_user_by_id(user_id)
        assert you is not None

        partner_id = couple.member2_user_id if couple.member1_user_id == user_id else couple.member1_user_id
        partner = self.get_user_by_id(partner_id) if partner_id else None
        return CoupleBundle(couple=couple, you=you, partner=partner)

    def get_couple_by_code(self, invite_code: str) -> Optional[Couple]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM couples WHERE invite_code = ?",
                (invite_code.upper(),),
            ).fetchone()
        return self._row_to_couple(row) if row else None

    def create_or_reuse_invite_code(self, user_id: int) -> Couple:
        existing = self.get_couple_bundle_for_user(user_id)
        if existing:
            if existing.couple.member2_user_id is None:
                return existing.couple
            raise ValueError("Voce ja esta pareado com alguem.")

        invite_code = self._generate_invite_code()
        created_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO couples (invite_code, member1_user_id, member2_user_id, created_at)
                VALUES (?, ?, NULL, ?)
                """,
                (invite_code, user_id, created_at),
            )
            row = connection.execute(
                "SELECT * FROM couples WHERE invite_code = ?",
                (invite_code,),
            ).fetchone()
        return self._row_to_couple(row)

    def join_couple(self, invite_code: str, joining_user_id: int) -> Couple:
        couple = self.get_couple_by_code(invite_code)
        if not couple:
            raise ValueError("Esse codigo de convite nao existe.")
        if couple.member1_user_id == joining_user_id:
            raise ValueError("Voce nao pode entrar no seu proprio codigo.")
        if couple.member2_user_id is not None:
            raise ValueError("Esse codigo de convite ja foi usado.")
        if self.get_couple_bundle_for_user(joining_user_id):
            raise ValueError("Voce ja esta pareado com alguem.")

        with self._connect() as connection:
            connection.execute(
                "UPDATE couples SET member2_user_id = ? WHERE id = ?",
                (joining_user_id, couple.id),
            )
            row = connection.execute(
                "SELECT * FROM couples WHERE id = ?",
                (couple.id,),
            ).fetchone()
        return self._row_to_couple(row)

    def add_expense(
        self,
        *,
        couple_id: int,
        paid_by_user_id: int,
        amount_cents: int,
        description: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO expenses (couple_id, paid_by_user_id, amount_cents, description, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (couple_id, paid_by_user_id, amount_cents, description.strip(), utc_now()),
            )

    def add_settlement(
        self,
        *,
        couple_id: int,
        from_user_id: int,
        to_user_id: int,
        amount_cents: int,
        note: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO settlements (couple_id, from_user_id, to_user_id, amount_cents, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (couple_id, from_user_id, to_user_id, amount_cents, note.strip(), utc_now()),
            )

    def get_activity(
        self,
        couple_id: int,
        *,
        limit: Optional[int] = None,
        since_last_settlement: bool = False,
    ) -> List[ActivityEntry]:
        query = """
            SELECT entry_type, actor_name, amount_cents, description, created_at
            FROM (
                SELECT 'expense' AS entry_type, users.first_name AS actor_name, expenses.amount_cents,
                       expenses.description AS description, expenses.created_at AS created_at
                FROM expenses
                JOIN users ON users.id = expenses.paid_by_user_id
                WHERE expenses.couple_id = ?

                UNION ALL

                SELECT 'settlement' AS entry_type,
                       from_user.first_name || ' -> ' || to_user.first_name AS actor_name,
                       settlements.amount_cents AS amount_cents,
                       settlements.note AS description,
                       settlements.created_at AS created_at
                FROM settlements
                JOIN users AS from_user ON from_user.id = settlements.from_user_id
                JOIN users AS to_user ON to_user.id = settlements.to_user_id
                WHERE settlements.couple_id = ?
            )
        """
        params = [couple_id, couple_id]

        if since_last_settlement:
            query += """
                WHERE (
                    COALESCE((SELECT MAX(created_at) FROM settlements WHERE couple_id = ?), '') = ''
                    OR created_at > (SELECT MAX(created_at) FROM settlements WHERE couple_id = ?)
                    OR (
                        created_at = (SELECT MAX(created_at) FROM settlements WHERE couple_id = ?)
                        AND entry_type != 'settlement'
                    )
                )
            """
            params.extend([couple_id, couple_id, couple_id])

        query += """
            ORDER BY created_at DESC
        """

        if limit is not None:
            query += """
                LIMIT ?
            """
            params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            ActivityEntry(
                entry_type=row["entry_type"],
                actor_name=row["actor_name"],
                amount_cents=row["amount_cents"],
                description=row["description"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_balance_snapshot(self, user_id: int) -> Optional[BalanceSnapshot]:
        bundle = self.get_couple_bundle_for_user(user_id)
        if not bundle or not bundle.partner:
            return None

        member1 = self.get_user_by_id(bundle.couple.member1_user_id)
        member2 = self.get_user_by_id(bundle.couple.member2_user_id or 0)
        assert member1 is not None and member2 is not None

        net = 0
        with self._connect() as connection:
            expense_rows = connection.execute(
                """
                SELECT paid_by_user_id, amount_cents
                FROM expenses
                WHERE couple_id = ?
                """,
                (bundle.couple.id,),
            ).fetchall()
            settlement_rows = connection.execute(
                """
                SELECT from_user_id, to_user_id, amount_cents
                FROM settlements
                WHERE couple_id = ?
                """,
                (bundle.couple.id,),
            ).fetchall()

        for row in expense_rows:
            share_owed_to_payer = row["amount_cents"] // 2
            if row["paid_by_user_id"] == member1.id:
                net += share_owed_to_payer
            else:
                net -= share_owed_to_payer

        for row in settlement_rows:
            if row["from_user_id"] == member1.id and row["to_user_id"] == member2.id:
                net += row["amount_cents"]
            elif row["from_user_id"] == member2.id and row["to_user_id"] == member1.id:
                net -= row["amount_cents"]

        return BalanceSnapshot(
            couple=bundle.couple,
            member1=member1,
            member2=member2,
            net_cents_in_favor_of_member1=net,
        )

    def _generate_invite_code(self) -> str:
        while True:
            code = secrets.token_hex(3).upper()
            if not self.get_couple_by_code(code):
                return code

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            telegram_user_id=row["telegram_user_id"],
            chat_id=row["chat_id"],
            first_name=row["first_name"],
            username=row["username"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_couple(row: sqlite3.Row) -> Couple:
        return Couple(
            id=row["id"],
            invite_code=row["invite_code"],
            member1_user_id=row["member1_user_id"],
            member2_user_id=row["member2_user_id"],
            created_at=row["created_at"],
        )
