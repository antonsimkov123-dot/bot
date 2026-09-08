import os
import logging
import asyncio
import sqlite3
from aiogram.client.default import DefaultBotProperties
from datetime import datetime, timedelta
from dotenv import load_dotenv

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
BOT_TOKEN = "8205192350:AAHUEmqDQK37-5D7dpcTUeMdpA6WpDACMkc"  # Получите новый токен в @BotFather
DB_PATH = "trades.db"

# ---------- DATABASE ----------
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
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
                entry_date TEXT,
                exit_price REAL,
                pnl REAL,
                profit_percent REAL,
                comment TEXT
            )
            """
        )
        conn.commit()

def add_missing_columns() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in cur.fetchall()}
        if "comment" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN comment TEXT")
            conn.commit()

init_db()
add_missing_columns()

# ---------- BOT ----------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

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
    confirming = State()

class EditState(StatesGroup):
    choosing_field = State()
    entering_value = State()

class CloseTradeState(StatesGroup):
    choosing_trade = State()
    entering_exit_price = State()

class DeleteTradeState(StatesGroup):
    choosing_trade = State()
    confirming = State()

# ---------- HELPERS ----------
def is_float(text: str) -> bool:
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить сделку", callback_data="add_trade")],
            [InlineKeyboardButton(text="✅ Закрыть сделку", callback_data="close_trade")],
            [InlineKeyboardButton(text="🗑 Удалить сделку", callback_data="delete_trade")],
            [InlineKeyboardButton(text="📊 Отчёты", callback_data="reports")],
            [InlineKeyboardButton(text="📈 Графики", callback_data="charts")],
            [InlineKeyboardButton(text="📤 Выгрузить сделки", callback_data="export_csv")],
            [InlineKeyboardButton(text="📂 Текущие сделки", callback_data="active")],
        ]
    )

def with_back(kb: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = list(kb.inline_keyboard)
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def go_home(user_id: int, state: FSMContext):
    await state.clear()
    await bot.send_message(user_id, "🏠 Главное меню:", reply_markup=main_menu_kb())

# ---------- COMMON ----------
@dp.message(CommandStart())
@dp.message(F.text.in_({"меню", "Меню", "/menu", "🏠"}))
async def cmd_start(message: types.Message, state: FSMContext):
    await go_home(message.from_user.id, state)

@dp.callback_query(F.data == "main_menu")
@dp.callback_query(F.data == "home")
@dp.callback_query(F.data == "restart")
async def cb_menu(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await go_home(cb.from_user.id, state)

# ---------- ACTIVE TRADES & EDIT ----------
@dp.callback_query(F.data == "active")
async def show_active(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, entry_price, stop_loss, targets, percent, entry_date, comment "
            "FROM trades WHERE user_id=? AND exit_price IS NULL",
            (uid,)
        ).fetchall()

    if not rows:
        return await cb.message.answer("У тебя нет активных сделок.")

    ikb = []
    for r in rows:
        tid, sym, entry, sl, tgt, pct, date, comm = r
        caption = f"{sym} | Вход {entry} | Стоп {sl} | Цели {tgt} | {pct}% ({date})"
        if comm:
            caption += f"\n💬 {comm}"
        ikb.append([InlineKeyboardButton(text=caption, callback_data=f"noop_{tid}")])
        ikb.append([
            InlineKeyboardButton(text="📝 Изменить", callback_data=f"edit_{tid}"),
            InlineKeyboardButton(text="🗑 Удалить",  callback_data=f"del_{tid}"),
            InlineKeyboardButton(text="✅ Закрыть", callback_data=f"close_{tid}"),
        ])
    ikb.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])

    await cb.message.answer("📂 Текущие сделки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=ikb))

@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_choose_field(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tid = int(cb.data.split("_")[1])
    await state.update_data(tid=tid)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Цели",   callback_data="field_targets")],
        [InlineKeyboardButton(text="🛑 Стоп",   callback_data="field_sl")],
        [InlineKeyboardButton(text="💼 %",      callback_data="field_pct")],
        [InlineKeyboardButton(text="📆 Дата",   callback_data="field_date")],
        [InlineKeyboardButton(text="💬 Коммент",callback_data="field_comment")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="home")]
    ])
    await cb.message.answer("Что изменить?", reply_markup=kb)
    await state.set_state(EditState.choosing_field)

@dp.callback_query(lambda c: c.data.startswith("field_"))
async def edit_enter_value(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    field = cb.data.split("_")[1]
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

    db_field = {'sl': 'stop_loss', 'pct': 'percent', 'date': 'entry_date'}.get(field, field)

    if field in {"sl", "pct"}:
        try:
            val = float(val.replace(",", "."))
        except ValueError:
            return await msg.answer("Нужно ввести число.")

    if field == "targets":
        val = ",".join(x.strip() for x in val.split(",")[:3])

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(f"UPDATE trades SET {db_field} = ? WHERE id = ?", (val, tid))
        conn.commit()

    await msg.answer("✅ Успешно обновлено.")
    await go_home(msg.from_user.id, state)

# ---------- ADD TRADE ----------
@dp.callback_query(lambda c: c.data == "add_trade")
async def add_trade_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
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
    await cb.answer()
    await state.update_data(trade_type=cb.data.split("_")[1])
    await cb.message.answer("Введите тикер (например BTC):", reply_markup=with_back(InlineKeyboardMarkup(inline_keyboard=[])))
    await state.set_state(TradeState.entering_symbol)

@dp.message(TradeState.entering_symbol)
async def add_trade_entry(msg: types.Message, state: FSMContext):
    await state.update_data(symbol=msg.text.strip().upper())
    await msg.answer("💰 Цена входа:")
    await state.set_state(TradeState.entering_entry)

@dp.message(TradeState.entering_entry)
async def add_trade_stop(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        return await msg.answer("Введите число.")
    await state.update_data(entry_price=float(msg.text.replace(",", ".")))
    await msg.answer("🛑 Стоп:")
    await state.set_state(TradeState.entering_stop)

@dp.message(TradeState.entering_stop)
async def add_trade_targets(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        return await msg.answer("Введите число.")
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
        return await msg.answer("Введите число.")
    await state.update_data(percent=float(msg.text.replace(",", ".")))
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
    await cb.answer()
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
    await show_trade_summary(cb.from_user.id, state)

@dp.message(TradeState.entering_date_manual)
async def add_trade_manual_date(msg: types.Message, state: FSMContext):
    try:
        datetime.strptime(msg.text.strip(), "%Y-%m-%d")
    except ValueError:
        return await msg.answer("Неверный формат даты. Используйте ГГГГ-ММ-ДД.")
    await state.update_data(entry_date=msg.text.strip())
    await show_trade_summary(msg.from_user.id, state)

async def show_trade_summary(uid: int, state: FSMContext):
    data = await state.get_data()
    text = (f"<b>Сводка сделки</b>\n\n"
            f"Тип: {data['trade_type'].upper()}\n"
            f"Тикер: {data['symbol']}\n"
            f"Вход: {data['entry_price']}\n"
            f"Стоп: {data['stop_loss']}\n"
            f"Цели: {data['targets']}\n"
            f"% от депо: {data['percent']}\n"
            f"Дата: {data['entry_date']}")
    kb = with_back(
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_add"),
            InlineKeyboardButton(text="🔁 Изменить", callback_data="add_trade")
        ]])
    )
    await bot.send_message(uid, text, reply_markup=kb)
    await state.set_state(TradeState.confirming)

@dp.callback_query(lambda c: c.data == "confirm_add")
async def add_trade_save(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO trades (user_id, trade_type, symbol, entry_price, stop_loss, "
            "targets, percent, entry_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cb.from_user.id, data['trade_type'], data['symbol'], data['entry_price'],
             data['stop_loss'], data['targets'], data['percent'], data['entry_date'])
        )
    await cb.message.answer("✅ Сделка сохранена.")
    await go_home(cb.from_user.id, state)

# ---------- CLOSE TRADE ----------
@dp.callback_query(lambda c: c.data == "close_trade")
async def close_trade_list(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT id, trade_type, symbol, entry_price FROM trades WHERE user_id=? AND exit_price IS NULL", (uid,)).fetchall()
    if not rows:
        return await cb.message.answer("Нет открытых сделок.")

    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"{sym.upper()} {t.upper()} @ {e}", callback_data=f"close_{tid}")] for tid, t, sym, e in rows]
        )
    )
    await cb.message.answer("Выберите сделку для закрытия:", reply_markup=kb)
    await state.set_state(CloseTradeState.choosing_trade)

@dp.callback_query(lambda c: c.data.startswith("close_"))
async def close_trade_enter(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(trade_id=int(cb.data.split("_")[1]))
    await cb.message.answer("Цена выхода:")
    await state.set_state(CloseTradeState.entering_exit_price)

@dp.message(CloseTradeState.entering_exit_price)
async def close_trade_finish(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        return await msg.answer("Введите число.")
    exit_price = float(msg.text.replace(",", "."))
    tid = (await state.get_data())['trade_id']

    with sqlite3.connect(DB_PATH) as conn:
        entry_price, t_type, percent = conn.execute("SELECT entry_price, trade_type, percent FROM trades WHERE id=?", (tid,)).fetchone()
        pnl = ((exit_price - entry_price) / entry_price) * (100 if t_type.lower() == "long" else -100)
        profit = round(pnl * percent / 100, 2)
        conn.execute("UPDATE trades SET exit_price=?, pnl=?, profit_percent=? WHERE id=?", (exit_price, pnl, profit, tid))

    await msg.answer(f"✅ Закрыта. PNL: {pnl:+.2f}% | Profit: {profit}%")
    await go_home(msg.from_user.id, state)

# ---------- DELETE TRADE ----------
@dp.callback_query(lambda c: c.data == "delete_trade")
async def delete_trade_list(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    uid = cb.from_user.id
    df = pd.read_sql_query("SELECT id, trade_type, symbol, entry_price FROM trades WHERE user_id=?", sqlite3.connect(DB_PATH), params=(uid,))
    if df.empty:
        return await cb.message.answer("Нет сделок для удаления.")
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"{row.id}: {row.trade_type.upper()} {row.symbol} @ {row.entry_price}", callback_data=f"del_{row.id}")] for _, row in df.iterrows()]
        )
    )
    await cb.message.answer("Выберите сделку для удаления:", reply_markup=kb)
    await state.set_state(DeleteTradeState.choosing_trade)

@dp.callback_query(lambda c: c.data.startswith("del_"))
async def delete_trade_confirm(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tid = int(cb.data.split("_")[1])
    await state.update_data(delete_id=tid)
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🗑 Удалить", callback_data="confirm_delete"),
                              InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]]
        )
    )
    await cb.message.answer(f"Удалить сделку #{tid}?", reply_markup=kb)
    await state.set_state(DeleteTradeState.confirming)

@dp.callback_query(lambda c: c.data == "confirm_delete")
async def delete_trade_do(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tid = (await state.get_data())['delete_id']
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM trades WHERE id=?", (tid,))
    await cb.message.answer("Сделка удалена.")
    await go_home(cb.from_user.id, state)

# ---------- EXPORT CSV ----------
@dp.callback_query(lambda c: c.data == "export_csv")
async def export_csv(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    df = pd.read_sql_query("SELECT * FROM trades WHERE user_id=?", sqlite3.connect(DB_PATH), params=(uid,))
    if df.empty:
        return await cb.message.answer("Нет данных.")
    path = f"trades_{uid}.csv"
    df.to_csv(path, index=False)
    await bot.send_document(uid, FSInputFile(path), caption="📤 Твои сделки")

# ---------- REPORTS ----------
@dp.callback_query(lambda c: c.data == "reports")
async def reports(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    df = pd.read_sql_query("SELECT symbol, pnl, entry_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL", sqlite3.connect(DB_PATH), params=(uid,))
    if df.empty:
        return await cb.message.answer("Нет завершённых сделок.")
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    now = datetime.now()
    pnl_week = df[df["entry_date"] >= now - timedelta(days=7)]["pnl"].sum()
    pnl_month = df[df["entry_date"] >= now - timedelta(days=30)]["pnl"].sum()
    wins = (df["pnl"] > 0).sum()
    losses = (df["pnl"] <= 0).sum()
    coin_mean = df.groupby("symbol")["pnl"].mean()
    best = coin_mean.idxmax() if not coin_mean.empty else "—"
    worst = coin_mean.idxmin() if not coin_mean.empty else "—"
    text = (f"📅 Неделя: {pnl_week:+.2f}%\n"
            f"📅 Месяц: {pnl_month:+.2f}%\n"
            f"✅ Побед: {wins} | ❌ Убытков: {losses}\n"
            f"🏆 Лучший: {best} ({coin_mean.max():+.1f}%)\n"
            f"🚨 Худший: {worst} ({coin_mean.min():+.1f}%)")
    await cb.message.answer(text)

# ---------- CHARTS ----------
@dp.callback_query(lambda c: c.data == "charts")
async def charts(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    df = pd.read_sql_query("SELECT trade_type, pnl, entry_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL", sqlite3.connect(DB_PATH), params=(uid,))
    if df.empty:
        return await cb.message.answer("Нет данных.")
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df = df.dropna(subset=["entry_date", "pnl"])
    df["week"] = df["entry_date"].dt.to_period("W").astype(str)

    # Graph 1
    weekly = df.groupby("week")["pnl"].sum()
    fig1, ax1 = plt.subplots()
    weekly.plot(kind="bar", ax=ax1)
    ax1.set_title("📊 PNL по неделям")
    fig1.tight_layout()
    p1 = f"pnl_week_{uid}.png"
    fig1.savefig(p1)
    plt.close(fig1)

    # Graph 2
    df["is_loss"] = df["pnl"] < 0
    stop_freq = df["is_loss"].value_counts(sort=False)
    fig2, ax2 = plt.subplots()
    stop_freq.plot(kind="bar", ax=ax2)
    ax2.set_title("⚠️ Частота стопов")
    labels = ["Прибыль" if idx is False else "Убыток" for idx in stop_freq.index]
    ax2.set_xticklabels(labels, rotation=0)
    fig2.tight_layout()
    p2 = f"stop_freq_{uid}.png"
    fig2.savefig(p2)
    plt.close(fig2)

    # Graph 3
    winrate = (df[df["pnl"] > 0].groupby("trade_type").size() / df.groupby("trade_type").size() * 100).fillna(0)
    fig3, ax3 = plt.subplots()
    winrate.plot(kind="bar", ax=ax3)
    ax3.set_title("🏆 Винрейт по типу")
    ax3.set_ylabel("%")
    fig3.tight_layout()
    p3 = f"winrate_{uid}.png"
    fig3.savefig(p3)
    plt.close(fig3)

    await bot.send_photo(uid, FSInputFile(p1), caption="📊 PNL по неделям")
    await bot.send_photo(uid, FSInputFile(p2), caption="⚠️ Частота стопов")
    await bot.send_photo(uid, FSInputFile(p3), caption="🏆 Винрейт по типу")

    kb_restart = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Главное меню", callback_data="home")]])
    await bot.send_message(cb.from_user.id, "Готово! 😊", reply_markup=kb_restart)

# ---------- RUN ----------
async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
