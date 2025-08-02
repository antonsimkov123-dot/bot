import os
import logging
import asyncio
import sqlite3
from aiogram import F
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()                    # ← Следите, чтобы ВЫШЕ этого не было кода,
                                 #    использующего os.getenv("BOT_TOKEN")
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------- CONFIG ----------
BOT_TOKEN = "8205192350:AAHUEmqDQK37-5D7dpcTUeMdpA6WpDACMkc"  # поменяй после теста!
DB_PATH = "trades.db"

# ---------- DATABASE ----------
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            trade_type TEXT,
            symbol TEXT,
            entry_price REAL,
            stop_loss REAL,
            targets TEXT,
            percent REAL,
            risk_percent REAL,
            entry_date TEXT,
            exit_price REAL,
            exit_date TEXT,
            pnl REAL,
            profit_percent REAL,
            comment TEXT,
            signals TEXT,
            signal_stars INTEGER

        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            remind_time TEXT,
            period_days INTEGER,
            next_run TEXT
        )
        """
    )
    conn.commit()
    conn.close()

init_db()

def add_missing_columns() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in cur.fetchall()}
        if "comment" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN comment TEXT")
            conn.commit()
        if "exit_date" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN exit_date TEXT")
            conn.commit()
        if "risk_percent" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN risk_percent REAL")
            conn.commit()
        if "signals" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN signals TEXT")
            conn.commit()
        if "signal_stars" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN signal_stars INTEGER")
            conn.commit()

        cur.execute("PRAGMA table_info(reminders)")
        rem_cols = {row[1] for row in cur.fetchall()}
        if "id" not in rem_cols:
            cur.execute("ALTER TABLE reminders RENAME TO reminders_old")
            cur.execute(
                """
                CREATE TABLE reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    remind_time TEXT,
                    period_days INTEGER,
                    next_run TEXT
                )
                """
            )
            cur.execute(
                "INSERT INTO reminders (user_id, remind_time, period_days, next_run) "
                "SELECT user_id, remind_time, period_days, next_run FROM reminders_old"
            )
            cur.execute("DROP TABLE reminders_old")
            conn.commit()

init_db()
add_missing_columns()      # ← вызов

# ---------- BOT ----------
bot = Bot(BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
# -------- RESTART
@dp.callback_query(F.data == "restart")
async def cb_restart(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()                       # закрыли «часики»
    await state.clear()                     # сбросили FSM
    # вызываем вашу функцию главного меню
    await go_home(cb.from_user.id, state)   # или отправь /start-меню вручную

# ---------- STATES ----------
class TradeState(StatesGroup):
    choosing_type = State()
    entering_symbol = State()
    entering_entry = State()
    entering_stop = State()
    entering_targets = State()
    entering_percent = State()
    choosing_date = State()
    entering_date_manual = State()
    entering_comment = State()
    choosing_signals = State()
    confirming = State()
    

class CloseTradeState(StatesGroup):
    choosing_trade = State()
    entering_percent = State()
    entering_exit_price = State()

class DeleteTradeState(StatesGroup):
    choosing_trade = State()
    confirming = State()

class ReminderState(StatesGroup):
    entering_time = State()
    choosing_period = State()


class ReminderDelState(StatesGroup):
    choosing_reminder = State()
    confirming = State()

# ---------- HELPERS ----------
def is_float(text: str) -> bool:
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False


def is_time(text: str) -> bool:
    try:
        datetime.strptime(text.strip(), "%H:%M")
        return True
    except ValueError:
        return False


def calc_risk(entry: float, stop: float, pct: float, t_type: str) -> float:
    if t_type.lower() == "long":
        risk = (entry - stop) / entry * pct
    else:
        risk = (stop - entry) / entry * pct
    return round(risk, 2)


def clock_emoji(time_str: str) -> str:
    hour = int(time_str.split(":")[0])
    clocks = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]
    return clocks[hour % 12]


def describe_reminder(t: str, period: int, next_run: str) -> str:
    if period == 1:
        return f"Каждый день в {t}"
    if period == 2:
        return f"Через день в {t}"
    weekday = datetime.fromisoformat(next_run).weekday()
    names = [
        "понедельникам",
        "вторникам",
        "средам",
        "четвергам",
        "пятницам",
        "субботам",
        "воскресеньям",
    ]
    return f"По {names[weekday]} в {t}"


# ---------- SIGNALS ----------
SIGNAL_OPTIONS = [
    ("Закреп 2–3 свечей", 6),
    ("Дивергенция RSI или MACD на дневке", 6),
    ("Поглощение на дневке", 5),
    ("0.618 FIBO (пробой/отработка)", 5),
    ("Пробой канала или трендовой", 5),
    ("Ретест пробитого уровня на объёмах", 5),
    ("MACD пересекает сигнальную / 0", 3),
    ("Рост объёмов", 3),
    ("Поддержка от мувингов (50/200)", 2),
    ("Боллинджер: выход за границу", 2),
    ("Формация ГиП / инверсная", 2),
    ("Сигналы только на 1H", 1),
    ("Мелкая дивергенция RSI на 1H", 1),
    ("Стагнация объёмов", 1),
    ("Локальные уровни без объёма", 1),
]

SIGNALS_TEXT = (
    "📍 Укажи сигналы, по которым ты входишь в сделку.\n"
    "🔻 Нажимай по одному, сколько нужно.\n\n"
    "🔥 Очень важные (★★★★★ и ★★★★):\n"
    "• Закреп 2–3 свечей — ★★★★★★\n"
    "• Дивергенция RSI или MACD на дневке — ★★★★★★\n"
    "• Поглощение на дневке — ★★★★★\n"
    "• 0.618 FIBO (пробой/отработка) — ★★★★★\n"
    "• Пробой канала или трендовой — ★★★★★\n"
    "• Ретест пробитого уровня на объёмах — ★★★★★\n\n"
    "🟡 Средние (★★★ и ★★):\n"
    "• MACD пересекает сигнальную / 0 — ★★★\n"
    "• Рост объёмов — ★★★\n"
    "• Поддержка от мувингов (50/200) — ★★\n"
    "• Боллинджер: выход за границу — ★★\n"
    "• Формация ГиП / инверсная — ★★\n\n"
    "⚪️ Слабые (★):\n"
    "• Сигналы только на 1H — ★\n"
    "• Мелкая дивергенция RSI на 1H — ★\n"
    "• Стагнация объёмов — ★\n"
    "• Локальные уровни без объёма — ★"
)


def list_open_trades(uid: int) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT symbol, trade_type, entry_price, stop_loss, targets, percent FROM trades WHERE user_id=? AND exit_price IS NULL",
            (uid,),
        ).fetchall()
    if not rows:
        return ""
    lines = []
    for sym, t_type, entry, sl, tgt, pct in rows:
        lines.append(f"{sym} {t_type.upper()} вход {entry} стоп {sl} цели {tgt} {pct}%")
    return "\n".join(lines)


async def show_reminders_menu(uid: int, message: types.Message) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, remind_time, period_days, next_run FROM reminders WHERE user_id=?",
            (uid,),
        ).fetchall()
    lines = ["🔔 Твои активные напоминания:"]
    for rid, t, period, next_run in rows:
        lines.append(f"• {clock_emoji(t)} {describe_reminder(t, period, next_run)}")
    if not rows:
        lines.append("У тебя нет активных напоминаний.")
    kb_rows = [[InlineKeyboardButton(text="➕ Добавить", callback_data="add_reminder")]]
    if rows:
        kb_rows.append([InlineKeyboardButton(text="❌ Удалить напоминание", callback_data="del_reminder")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await message.answer("\n".join(lines), reply_markup=kb)


async def reminder_scheduler():
    while True:
        now = datetime.now()
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT id, user_id, period_days, next_run FROM reminders WHERE next_run<=?",
                (now.isoformat(),),
            ).fetchall()
        for rid, uid, period, next_run in rows:
            trades_text = list_open_trades(uid)
            msg = "Проверь, не пора ли закрыть сделку?"
            if trades_text:
                msg += "\n" + trades_text
            else:
                msg += "\nОткрытых сделок нет."
            try:
                await bot.send_message(uid, msg)
            except Exception:
                pass
            next_time = datetime.fromisoformat(next_run) + timedelta(days=period)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE reminders SET next_run=? WHERE id=?", (next_time.isoformat(), rid))
                conn.commit()
        await asyncio.sleep(60)

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить сделку", callback_data="add_trade")],
            [InlineKeyboardButton(text="✅ Закрыть сделку", callback_data="close_trade")],
            [InlineKeyboardButton(text="🗑 Удалить сделку", callback_data="delete_trade")],
            [InlineKeyboardButton(text="📊 Отчёты", callback_data="reports")],
            [InlineKeyboardButton(text="📈 Графики", callback_data="charts")],
            [InlineKeyboardButton(text="🔔 Напоминания", callback_data="reminders")],
            [InlineKeyboardButton(text="📤 Выгрузить сделки", callback_data="export_csv")],
            [InlineKeyboardButton(text="📂 Текущие сделки", callback_data="active")],
            [InlineKeyboardButton(text="📜 История сделок", callback_data="history")],
        ]
    )



def with_back(kb: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Добавляет кнопку «🏠 Меню» в любую inline-клавиатуру"""
    rows = list(kb.inline_keyboard)
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def signals_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{name} — {'★'*stars}", callback_data=f"sig_{idx}")]
        for idx, (name, stars) in enumerate(SIGNAL_OPTIONS)
    ]
    buttons.append([InlineKeyboardButton(text="🛑 Завершить выбор", callback_data="signals_done")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=buttons))


def format_trade(data: dict) -> str:
    text = (
        f"Тип: {data['trade_type'].upper()}\n"
        f"Тикер: {data['symbol']}\n"
        f"Вход: {data['entry_price']}\n"
        f"Стоп: {data['stop_loss']}\n"
        f"Цели: {data['targets']}\n"
        f"% от депо: {data['percent']}\n"
        f"Риск: {data['risk']}%\n"
        f"Дата: {data['entry_date']}"
    )
    if data.get('comment'):
        text += f"\nКомментарий: {data['comment']}"
    sigs = data.get('signals')
    if sigs:
        if isinstance(sigs, str):
            sigs = [s for s in sigs.split(';') if s]
        if sigs:
            text += "\nСигналы: " + ", ".join(sigs)
    return text

async def go_home(user_id: int, state: FSMContext):
    await state.clear()
    await bot.send_message(user_id, "🏠 Главное меню:", reply_markup=main_menu_kb())

# ---------- COMMON ----------
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await go_home(message.from_user.id, state)

@dp.message(F.text.in_({"меню", "Меню", "/menu", "🏠"}))
async def cmd_menu(message: types.Message, state: FSMContext):
    await go_home(message.from_user.id, state)

@dp.callback_query(lambda c: c.data == "main_menu")
async def cb_menu(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await go_home(cb.from_user.id, state)

# ---------- REMINDER ----------
@dp.callback_query(F.data == "reminders")
async def reminders_overview(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await show_reminders_menu(cb.from_user.id, cb.message)


@dp.callback_query(F.data == "add_reminder")
async def reminder_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer(
        "Введите время напоминания (HH:MM):",
        reply_markup=with_back(InlineKeyboardMarkup(inline_keyboard=[])),
    )
    await state.set_state(ReminderState.entering_time)


@dp.message(ReminderState.entering_time)
async def reminder_time(msg: types.Message, state: FSMContext):
    if not is_time(msg.text):
        await msg.answer("Формат HH:MM")
        return
    await state.update_data(remind_time=msg.text.strip())
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Ежедневно", callback_data="period_1")],
                [InlineKeyboardButton(text="Через день", callback_data="period_2")],
                [InlineKeyboardButton(text="Раз в неделю", callback_data="period_7")],
            ]
        )
    )
    await msg.answer("Периодичность:", reply_markup=kb)
    await state.set_state(ReminderState.choosing_period)


@dp.callback_query(ReminderState.choosing_period, lambda c: c.data.startswith("period_"))
async def reminder_save(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    period = int(cb.data.split("_")[1])
    data = await state.get_data()
    t = datetime.strptime(data["remind_time"], "%H:%M").time()
    now = datetime.now()
    next_run = datetime.combine(now.date(), t)
    if next_run <= now:
        next_run += timedelta(days=period)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO reminders (user_id, remind_time, period_days, next_run) VALUES (?, ?, ?, ?)",
            (cb.from_user.id, data["remind_time"], period, next_run.isoformat()),
        )
        conn.commit()
    names = {1: "ежедневно", 2: "через день", 7: "раз в неделю"}
    await cb.message.answer(
        f"Напоминание на {data['remind_time']} {names[period]} сохранено."
    )
    await state.clear()
    await show_reminders_menu(cb.from_user.id, cb.message)


@dp.callback_query(F.data == "del_reminder")
async def reminder_delete_list(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, remind_time, period_days, next_run FROM reminders WHERE user_id=?",
            (cb.from_user.id,),
        ).fetchall()
    if not rows:
        await cb.message.answer("У тебя нет активных напоминаний.")
        return
    buttons = [
        [InlineKeyboardButton(text=describe_reminder(t, p, nr), callback_data=f"delr_{rid}")]
        for rid, t, p, nr in rows
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="reminders")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.message.answer("Выбери напоминание для удаления:", reply_markup=kb)
    await state.set_state(ReminderDelState.choosing_reminder)


@dp.callback_query(ReminderDelState.choosing_reminder, lambda c: c.data.startswith("delr_"))
async def reminder_delete_confirm(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    rid = int(cb.data.split("_")[1])
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT remind_time, period_days, next_run FROM reminders WHERE id=? AND user_id=?",
            (rid, cb.from_user.id),
        ).fetchone()
    if not row:
        await cb.message.answer("Напоминание не найдено.")
        await state.clear()
        return
    desc = describe_reminder(row[0], row[1], row[2])
    await state.update_data(del_id=rid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delr")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="del_reminder")],
        ]
    )
    kb = with_back(kb)
    await cb.message.answer(
        f"Вы точно хотите удалить напоминание «{desc}»?",
        reply_markup=kb,
    )
    await state.set_state(ReminderDelState.confirming)


@dp.callback_query(ReminderDelState.confirming, F.data == "confirm_delr")
async def reminder_delete(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    rid = data.get("del_id")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM reminders WHERE id=? AND user_id=?",
            (rid, cb.from_user.id),
        )
        conn.commit()
    await cb.message.answer("🗑 Напоминание удалено.")
    await state.clear()
    await show_reminders_menu(cb.from_user.id, cb.message)
# ---------- TRADE -------------
@dp.callback_query(F.data == "active")
async def show_active(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, symbol, entry_price, stop_loss, targets, percent, entry_date, comment, risk_percent "
        "FROM trades WHERE user_id=? AND exit_price IS NULL",
        (uid,)
    ).fetchall()
    conn.close()

    if not rows:
        return await cb.message.answer("У тебя нет активных сделок.")

    # собираем клавиатуру из сделок
    ikb = []
    for r in rows:
        tid, sym, entry, sl, tgt, pct, date, comm, risk = r
        caption = f"{sym} | Вход {entry}  Стоп {sl}  Цели {tgt}  {pct}% (риск {risk}%) ({date})"
        if comm:
            caption += f"\n💬 {comm}"
        ikb.append([
            InlineKeyboardButton(text=caption, callback_data=f"view_{tid}")
        ])
        ikb.append([
            InlineKeyboardButton(text="📝 Изменить", callback_data=f"edit_{tid}"),
            InlineKeyboardButton(text="🗑 Удалить",  callback_data=f"del_{tid}"),
            InlineKeyboardButton(text="✅ Закрыть", callback_data=f"close_{tid}"),
        ])
    keyboard = with_back(InlineKeyboardMarkup(inline_keyboard=ikb))

    await cb.message.answer("📂 Текущие сделки:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith("view_"))
async def show_trade_details(cb: types.CallbackQuery):
    await cb.answer()
    tid = int(cb.data.split("_")[1])
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT trade_type, symbol, entry_price, stop_loss, targets, percent, risk_percent, entry_date, comment, signals "
            "FROM trades WHERE id=?",
            (tid,),
        ).fetchone()
    if not row:
        await cb.message.answer("Сделка не найдена.")
        return
    data = {
        "trade_type": row[0],
        "symbol": row[1],
        "entry_price": row[2],
        "stop_loss": row[3],
        "targets": row[4],
        "percent": row[5],
        "risk": row[6],
        "entry_date": row[7],
        "comment": row[8],
        "signals": row[9],
    }
    text = "<b>Сводка сделки</b>\n\n" + format_trade(data)
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="active")]]
        )
    )
    await cb.message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "history")
async def show_history(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT symbol, trade_type, entry_price, exit_price, pnl, exit_date, comment, risk_percent FROM trades "
            "WHERE user_id=? AND exit_price IS NOT NULL",
            (uid,),
        ).fetchall()
    if not rows:
        await cb.message.answer("История сделок пуста.")
        return
    lines = []
    for sym, t_type, entry, exit_price, pnl, exit_date, comm, risk in rows:
        line = f"{sym} {t_type.upper()} | {entry} → {exit_price} | {pnl:+.2f}% | {exit_date} | Риск {risk}%"
        if comm:
            line += f"\n💬 {comm}"
        lines.append(line)
    text = "\n".join(lines)
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=[]))
    await cb.message.answer("📜 История сделок:\n" + text, reply_markup=kb)
    
    # ───────── Edit-mode FSM ─────────
class EditState(StatesGroup):
    """Состояния редактирования сделки"""
    choosing_field: State = State()
    entering_value: State = State()

@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_choose_field(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tid = int(cb.data.split("_")[1])
    await state.update_data(tid=tid)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT trade_type, symbol, entry_price, stop_loss, targets, percent, risk_percent, entry_date, comment, signals "
            "FROM trades WHERE id=?",
            (tid,),
        ).fetchone()
    if row:
        data = {
            "trade_type": row[0],
            "symbol": row[1],
            "entry_price": row[2],
            "stop_loss": row[3],
            "targets": row[4],
            "percent": row[5],
            "risk": row[6],
            "entry_date": row[7],
            "comment": row[8],
            "signals": row[9],
        }
        text = "<b>Сводка сделки</b>\n\n" + format_trade(data)
        kb_sum = with_back(
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="active")]])
        )
        await cb.message.answer(text, reply_markup=kb_sum)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Цели",   callback_data="field_targets")],
        [InlineKeyboardButton(text="🛑 Стоп",   callback_data="field_sl")],
        [InlineKeyboardButton(text="💼 %",      callback_data="field_pct")],
        [InlineKeyboardButton(text="📆 Дата",   callback_data="field_date")],
        [InlineKeyboardButton(text="💬 Коммент",callback_data="field_comment")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="active")]
    ])
    kb = with_back(kb)
    await cb.message.answer("Что изменить?", reply_markup=kb)
    await state.set_state(EditState.choosing_field)

@dp.callback_query(lambda c: c.data.startswith("field_"))
async def edit_enter_value(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    field = cb.data.split("_")[1]   # targets / sl / pct / date / comment
    await state.update_data(field=field)
    prompt = {
        "targets": "Новые цели (через запятую):",
        "sl":      "Новый стоп:",
        "pct":     "Новый % от депо:",
        "date":    "Новая дата (ГГГГ-ММ-ДД):",
        "comment": "Новый комментарий:"
    }[field]
    await cb.message.answer(prompt)
    await state.set_state(EditState.entering_value)

@dp.message(EditState.entering_value)
async def edit_save(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    tid, field = data["tid"], data["field"]
    val = msg.text.strip()

    # простая валидация чисел
    if field in {"sl", "pct"}:
        try: val = float(val.replace(",", "."))
        except: return await msg.answer("Нужна цифра.")

    if field == "targets":
        val = ",".join(x.strip() for x in val.split(",")[:3])

    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"UPDATE trades SET { {'sl':'stop_loss','pct':'percent'}.get(field, field) } = ? WHERE id = ?", (val, tid))
    if field in {"sl", "pct"}:
        entry_price, stop_loss, percent, t_type = conn.execute(
            "SELECT entry_price, stop_loss, percent, trade_type FROM trades WHERE id=?",
            (tid,)
        ).fetchone()
        risk = calc_risk(entry_price, stop_loss, percent, t_type)
        conn.execute("UPDATE trades SET risk_percent=? WHERE id=?", (risk, tid))
    conn.commit(); conn.close()

    await msg.answer("✅ Обновлено.")
    await state.clear()
    await go_home(msg.from_user.id, state)

# ---------- ADD TRADE ----------
@dp.callback_query(lambda c: c.data == "add_trade")
async def add_trade_start(cb: types.CallbackQuery, state: FSMContext):
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="LONG", callback_data="type_long"),
                              InlineKeyboardButton(text="SHORT", callback_data="type_short")]]
        )
    )
    await cb.message.answer("Выбери тип сделки:", reply_markup=kb)
    await state.set_state(TradeState.choosing_type)

@dp.callback_query(lambda c: c.data.startswith("type_"))
async def add_trade_symbol(cb: types.CallbackQuery, state: FSMContext):
    await state.update_data(trade_type=cb.data.split("_")[1])
    await bot.send_message(cb.from_user.id, "Введите тикер (например BTC):",
                           reply_markup=with_back(InlineKeyboardMarkup(inline_keyboard=[])))
    await state.set_state(TradeState.entering_symbol)

@dp.message(TradeState.entering_symbol)
async def add_trade_entry(msg: types.Message, state: FSMContext):
    await state.update_data(symbol=msg.text.strip().upper())
    await msg.answer("💰 Цена входа:")
    await state.set_state(TradeState.entering_entry)

@dp.message(TradeState.entering_entry)
async def add_trade_stop(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        await msg.answer("Введите число.")
        return
    await state.update_data(entry_price=float(msg.text.replace(",", ".")))
    await msg.answer("🛑 Стоп:")
    await state.set_state(TradeState.entering_stop)

@dp.message(TradeState.entering_stop)
async def add_trade_targets(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        await msg.answer("Введите число.")
        return
    await state.update_data(stop_loss=float(msg.text.replace(",", ".")))
    await msg.answer("🎯 Цели (до 3, через запятую):")
    await state.set_state(TradeState.entering_targets)

@dp.message(TradeState.entering_targets)
async def add_trade_percent(msg: types.Message, state: FSMContext):
    targets = ",".join(t.strip() for t in msg.text.split(",")[:3])
    await state.update_data(targets=targets)
    await msg.answer("💼 % от депозита:")
    await state.set_state(TradeState.entering_percent)

@dp.message(TradeState.entering_percent)
async def add_trade_date_choice(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        await msg.answer("Введите число.")
        return
    await state.update_data(percent=float(msg.text.replace(",", ".")))
    data = await state.get_data()
    risk = calc_risk(data['entry_price'], data['stop_loss'], data['percent'], data['trade_type'])
    await state.update_data(risk=risk)
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Сегодня", callback_data="date_today"),
                 InlineKeyboardButton(text="Вчера", callback_data="date_yesterday")],
                [InlineKeyboardButton(text="Указать дату", callback_data="date_manual")]
            ]
        )
    )
    await msg.answer("📆 Дата входа:", reply_markup=kb)
    await state.set_state(TradeState.choosing_date)

@dp.callback_query(lambda c: c.data.startswith("date_"))
async def add_trade_date(cb: types.CallbackQuery, state: FSMContext):
    choice = cb.data.split("_")[1]
    if choice == "today":
        date_str = datetime.now().strftime("%Y-%m-%d")
    elif choice == "yesterday":
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        await cb.message.answer("Введите дату в формате ГГГГ-MM-ДД:")
        await state.set_state(TradeState.entering_date_manual)
        return
    await state.update_data(entry_date=date_str)
    await bot.send_message(cb.from_user.id, "💬 Комментарий (опционально, или -):")
    await state.set_state(TradeState.entering_comment)

@dp.message(TradeState.entering_date_manual)
async def add_trade_manual_date(msg: types.Message, state: FSMContext):
    try:
        datetime.strptime(msg.text.strip(), "%Y-%m-%d")
    except ValueError:
        await msg.answer("Неверный формат.")
        return
    await state.update_data(entry_date=msg.text.strip())
    await msg.answer("💬 Комментарий (опционально, или -):")
    await state.set_state(TradeState.entering_comment)


async def start_signals_choice(uid: int, state: FSMContext):
    await state.update_data(signals=[], signals_total=0)
    await bot.send_message(uid, SIGNALS_TEXT, reply_markup=signals_keyboard())
    await state.set_state(TradeState.choosing_signals)

@dp.message(TradeState.entering_comment)
async def add_trade_comment(msg: types.Message, state: FSMContext):
    comment = msg.text.strip()
    if comment == "-" or comment == "":
        comment = None
    await state.update_data(comment=comment)
    await start_signals_choice(msg.from_user.id, state)


@dp.callback_query(TradeState.choosing_signals, lambda c: c.data.startswith("sig_"))
async def add_signal(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    idx = int(cb.data.split("_")[1])
    name, stars = SIGNAL_OPTIONS[idx]
    data = await state.get_data()
    signals = data.get("signals", [])
    total = data.get("signals_total", 0)
    if name not in signals:
        signals.append(name)
        total += stars
        await state.update_data(signals=signals, signals_total=total)
        await cb.message.answer(f"✅ Сигнал добавлен: “{name}” ({'★'*stars})")
    else:
        await cb.message.answer(f"⚠️ Сигнал уже выбран: “{name}”")
    await cb.message.answer("Выбирай дальше:", reply_markup=signals_keyboard())


@dp.callback_query(TradeState.choosing_signals, F.data == "signals_done")
async def signals_done(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await show_trade_summary(cb.from_user.id, state)

async def show_trade_summary(uid: int, state: FSMContext):
    data = await state.get_data()
    total = data.get('signals_total', 0)
    if total < 6:
        rating = f"⚠️ Мало сигналов: всего ★{total}. Сделка рискованная."
    elif total < 10:
        rating = f"⚖️ Умеренно сильная сделка: ★{total}"
    else:
        rating = f"🔥 Отличный сетап: ★{total}"
    text = rating + "\n\n<b>Сводка сделки</b>\n\n" + format_trade(data)
    kb = with_back(
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_add"),
                              InlineKeyboardButton(text="🔁 Изменить", callback_data="add_trade")]]
        )
    )
    await bot.send_message(uid, text, reply_markup=kb)
    await state.set_state(TradeState.confirming)

@dp.callback_query(lambda c: c.data == "confirm_add")
async def add_trade_save(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO trades (user_id, trade_type, symbol, entry_price, stop_loss, "
            "targets, percent, risk_percent, entry_date, comment, signals, signal_stars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cb.from_user.id, data['trade_type'], data['symbol'], data['entry_price'],
             data['stop_loss'], data['targets'], data['percent'], data['risk'], data['entry_date'],
             data.get('comment'), ";".join(data.get('signals', [])), data.get('signals_total', 0))
        )
    await cb.message.answer("✅ Сделка сохранена.")
    await go_home(cb.from_user.id, state)

# ---------- CLOSE TRADE ----------
@dp.callback_query(lambda c: c.data == "close_trade")
async def close_trade_list(cb: types.CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
        SELECT id, trade_type, symbol, entry_price
        FROM trades
        WHERE user_id=? AND exit_price IS NULL
""", (uid,)).fetchall()
    if not rows:
        await cb.message.answer("Нет открытых сделок.")
        return
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"{sym.upper()} {t.upper()} @ {e}",
                                                   callback_data=f"close_{tid}")]
                             for tid, t, sym, e in rows]
        )
    )
    await cb.message.answer("Выберите сделку для закрытия:", reply_markup=kb)
    await state.set_state(CloseTradeState.choosing_trade)

@dp.callback_query(lambda c: c.data.startswith("close_"))
async def close_trade_enter(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(trade_id=int(cb.data.split("_")[1]))
    await bot.send_message(cb.from_user.id, "Сколько % закрыть?")
    await state.set_state(CloseTradeState.entering_percent)

@dp.message(CloseTradeState.entering_percent)
async def close_trade_get_percent(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        await msg.answer("Введите число.")
        return
    pct = float(msg.text.replace(",", "."))
    tid = (await state.get_data())["trade_id"]
    with sqlite3.connect(DB_PATH) as conn:
        total_pct = conn.execute("SELECT percent FROM trades WHERE id=?", (tid,)).fetchone()[0]
    if pct <= 0 or pct > total_pct:
        await msg.answer(f"Доступно от 1 до {total_pct}%.")
        return
    await state.update_data(close_percent=pct)
    await msg.answer("Цена выхода:")
    await state.set_state(CloseTradeState.entering_exit_price)

@dp.message(CloseTradeState.entering_exit_price)
async def close_trade_finish(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        await msg.answer("Введите число.")
        return
    exit_price = float(msg.text.replace(",", "."))
    data = await state.get_data()
    tid = data['trade_id']
    close_pct = data['close_percent']
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        user_id, t_type, sym, entry_price, sl, tgt, percent, entry_date, comment, signals, sstars = cur.execute(
            "SELECT user_id, trade_type, symbol, entry_price, stop_loss, targets, percent, entry_date, comment, signals, signal_stars FROM trades WHERE id=?",
            (tid,),
        ).fetchone()
        pnl = ((exit_price - entry_price) / entry_price) * (100 if t_type.lower() == "long" else -100)
        profit = round(pnl * close_pct / 100, 2)
        exit_date = datetime.now().strftime("%Y-%m-%d")
        risk_close = calc_risk(entry_price, sl, close_pct, t_type)
        cur.execute(
            "INSERT INTO trades (user_id, trade_type, symbol, entry_price, stop_loss, targets, percent, risk_percent, entry_date, exit_price, exit_date, pnl, profit_percent, comment, signals, signal_stars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, t_type, sym, entry_price, sl, tgt, close_pct, risk_close, entry_date, exit_price, exit_date, pnl, profit, comment, signals, sstars),
        )
        remaining = percent - close_pct
        if remaining <= 0:
            cur.execute("DELETE FROM trades WHERE id=?", (tid,))
        else:
            risk_remain = calc_risk(entry_price, sl, remaining, t_type)
            cur.execute("UPDATE trades SET percent=?, risk_percent=? WHERE id=?", (remaining, risk_remain, tid))
        conn.commit()
    await msg.answer(f"Закрыто {close_pct}% | PNL: {pnl:+.2f}% | Profit: {profit}%")
    await go_home(msg.from_user.id, state)

# ---------- DELETE TRADE ----------
@dp.callback_query(lambda c: c.data == "delete_trade")
async def delete_trade_list(cb: types.CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    df = pd.read_sql_query("SELECT id, trade_type, symbol, entry_price FROM trades WHERE user_id=?",
                           sqlite3.connect(DB_PATH), params=(uid,))
    if df.empty:
        await cb.message.answer("Нет сделок для удаления.")
        return
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"{row.id}: {row.trade_type.upper()} "
                                                   f"{row.symbol} @ {row.entry_price}",
                                                   callback_data=f"del_{row.id}")]
                             for _, row in df.iterrows()]
        )
    )
    await cb.message.answer("Выберите сделку для удаления:", reply_markup=kb)
    await state.set_state(DeleteTradeState.choosing_trade)

@dp.callback_query(lambda c: c.data.startswith("del_"))
async def delete_trade_confirm(cb: types.CallbackQuery, state: FSMContext):
    tid = int(cb.data.split("_")[1])
    await state.update_data(delete_id=tid)
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🗑 Удалить", callback_data="confirm_delete"),
                              InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]]
        )
    )
    await cb.message.answer(f"Удалить сделку {tid}?", reply_markup=kb)
    await state.set_state(DeleteTradeState.confirming)

@dp.callback_query(lambda c: c.data == "confirm_delete")
async def delete_trade_do(cb: types.CallbackQuery, state: FSMContext):
    tid = (await state.get_data())['delete_id']
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM trades WHERE id=?", (tid,))
    await cb.message.answer("Сделка удалена.")
    await go_home(cb.from_user.id, state)

# ---------- EXPORT CSV ----------
@dp.callback_query(lambda c: c.data == "export_csv")
async def export_csv(cb: types.CallbackQuery):
    uid = cb.from_user.id
    df = pd.read_sql_query("SELECT * FROM trades WHERE user_id=?",
                           sqlite3.connect(DB_PATH), params=(uid,))
    if df.empty:
        await cb.message.answer("Нет данных.")
        return
    path = f"trades_{uid}.csv"
    df.to_csv(path, index=False)
    await bot.send_document(uid, FSInputFile(path), caption="📤 Твои сделки")

# ---------- REPORTS ----------
@dp.callback_query(lambda c: c.data == "reports")
async def reports(cb: types.CallbackQuery):
    uid = cb.from_user.id
    df = pd.read_sql_query(
        "SELECT symbol, pnl, entry_date, exit_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL",
        sqlite3.connect(DB_PATH), params=(uid,))
    if df.empty:
        await cb.message.answer("Нет завершённых сделок.")
        return
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df["exit_date"] = pd.to_datetime(df["exit_date"], errors="coerce")
    now = datetime.now()
    pnl_week = df[df["entry_date"] >= now - timedelta(days=7)]["pnl"].sum()
    pnl_month = df[df["entry_date"] >= now - timedelta(days=30)]["pnl"].sum()
    wins = (df["pnl"] > 0).sum()
    losses = (df["pnl"] <= 0).sum()
    winrate = wins / len(df) * 100 if len(df) else 0
    avg_profit = df[df["pnl"] > 0]["pnl"].mean() if wins else 0
    avg_loss = df[df["pnl"] < 0]["pnl"].mean() if losses else 0
    durations = (df["exit_date"] - df["entry_date"]).dt.days
    avg_duration = durations.mean() if not durations.empty else 0
    coin_mean = df.groupby("symbol")["pnl"].mean()
    best = coin_mean.idxmax() if not coin_mean.empty else "—"
    worst = coin_mean.idxmin() if not coin_mean.empty else "—"
    text = (
        f"📅 Неделя: {pnl_week:+.2f}%\n"
        f"📅 Месяц: {pnl_month:+.2f}%\n"
        f"✅ Побед: {wins} | ❌ Убытков: {losses}\n"
        f"📈 Средний профит: {avg_profit:+.2f}%\n"
        f"📉 Средний убыток: {avg_loss:+.2f}%\n"
        f"🥇 Winrate: {winrate:.1f}%\n"
        f"⏱ Средняя длительность: {avg_duration:.1f} дн.\n"
        f"🏆 Лучший: {best} ({coin_mean.max():+.1f}%)\n"
        f"🚨 Худший: {worst} ({coin_mean.min():+.1f}%)"
    )
    await cb.message.answer(text)

# ---------- CHARTS ----------
@dp.callback_query(lambda c: c.data == "charts")
async def charts(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    df = pd.read_sql_query(
        "SELECT trade_type, pnl, entry_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL",
        sqlite3.connect(DB_PATH), params=(uid,))
    if df.empty:
        await cb.message.answer("Нет данных.")
        return
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df = df.dropna(subset=["entry_date", "pnl"])
    df["week"] = df["entry_date"].dt.to_period("W").astype(str)

    # PNL by week
    weekly = df.groupby("week")["pnl"].sum()
    fig1, ax1 = plt.subplots()
    weekly.plot(kind="bar", ax=ax1)
    ax1.set_title("📊 PNL по неделям")
    fig1.tight_layout()
    p1 = "pnl_week.png"
    fig1.savefig(p1)
    plt.close(fig1)

    # Stop freq
    # === График 2: Частота стопов ===
    df["is_loss"] = df["pnl"] < 0

    # value_counts(sort=False) — если есть только True или только False,
    # всё равно вернётся один столбец, и порядок индекса сохранится
    stop_freq = df["is_loss"].value_counts(sort=False)

    fig2, ax2 = plt.subplots()
    stop_freq.plot(kind="bar", ax=ax2)
    ax2.set_title("⚠️ Частота стопов")

    # подписи равны количеству столбцов
    labels = ["Прибыль" if idx is False else "Убыток" for idx in stop_freq.index]
    ax2.set_xticklabels(labels, rotation=0)

    fig2.tight_layout()
    p2 = "stop_freq.png"
    fig2.savefig(p2)
    plt.close(fig2)

    # Winrate by type
    winrate = (df[df["pnl"] > 0].groupby("trade_type").size()
               / df.groupby("trade_type").size() * 100).fillna(0)
    fig3, ax3 = plt.subplots()
    winrate.plot(kind="bar", ax=ax3)
    ax3.set_title("🏆 Винрейт по типу")
    ax3.set_ylabel("%")
    fig3.tight_layout()
    p3 = "winrate.png"
    fig3.savefig(p3)
    plt.close(fig3)

    await bot.send_photo(uid, FSInputFile(p1), caption="📊 PNL по неделям")
    await bot.send_photo(uid, FSInputFile(p2), caption="⚠️ Частота стопов")
    await bot.send_photo(uid, FSInputFile(p3), caption="🏆 Винрейт по типу")
    # -------- кнопка «Перезапустить» --------
    kb_restart = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Перезапустить бота", callback_data="restart")]
    ]
    
)
    await bot.send_message(cb.from_user.id, "Готово! 😊", reply_markup=kb_restart)

# ---------- RUN ----------
async def main():
    asyncio.create_task(reminder_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
