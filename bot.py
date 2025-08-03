import os
import logging
import asyncio
import sqlite3
import time
import hmac
import hashlib
from urllib.parse import urlencode
from aiogram import F
import aiohttp
from datetime import datetime, timedelta
import calendar
from dotenv import load_dotenv
from collections import defaultdict
from itertools import combinations
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

MONTHS_RU = [
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]

MONTHS_RU_GEN = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

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
            position_size REAL,
            leverage REAL,
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
            signal_stars INTEGER,
            mistake_reason TEXT,
            is_deleted INTEGER DEFAULT 0,
            notifications_enabled INTEGER DEFAULT 0
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS auto_reports (
            user_id INTEGER PRIMARY KEY,
            report_time TEXT,
            period_days INTEGER,
            next_run TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS danger_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            day_date TEXT,
            reason TEXT,
            UNIQUE(user_id, day_date)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bybit_keys (
            user_id INTEGER PRIMARY KEY,
            api_key TEXT,
            api_secret TEXT
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
        if "mistake_reason" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN mistake_reason TEXT")
            conn.commit()
        if "is_deleted" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN is_deleted INTEGER DEFAULT 0")
            conn.commit()
        if "position_size" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN position_size REAL")
            conn.commit()
        if "leverage" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN leverage REAL")
            conn.commit()
        if "notifications_enabled" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN notifications_enabled INTEGER DEFAULT 0")
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

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_reports (
                user_id INTEGER PRIMARY KEY,
                report_time TEXT,
                period_days INTEGER,
                next_run TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bybit_keys (
                user_id INTEGER PRIMARY KEY,
                api_key TEXT,
                api_secret TEXT
            )
            """
        )

init_db()
add_missing_columns()      # ← вызов

# ---------- BOT ----------
bot = Bot(BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

CHANNEL_USERNAME = "@CryptoLens_MarketMinds"


async def is_subscribed(uid: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, uid)
        return member.status in {"member", "administrator", "creator"}
    except Exception:
        return False


async def require_subscription(message: types.Message, uid: int) -> bool:
    if await is_subscribed(uid):
        return True
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")]]
    )
    await message.answer(
        "❌ Доступно только для подписчиков. Подпишись на канал @CryptoLens_MarketMinds и нажми кнопку «🔄 Проверить подписку».",
        reply_markup=kb,
    )
    return False


@dp.callback_query(F.data == "check_sub")
async def recheck_subscription(cb: types.CallbackQuery):
    await cb.answer()
    if await is_subscribed(cb.from_user.id):
        await cb.message.answer("✅ Подписка подтверждена. Теперь можно пользоваться этой функцией.")
    else:
        await require_subscription(cb.message, cb.from_user.id)
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
    choosing_reason = State()
    entering_custom_reason = State()

class DeleteTradeState(StatesGroup):
    choosing_trade = State()
    confirming = State()

class ReminderState(StatesGroup):
    entering_time = State()
    choosing_period = State()


class ReminderDelState(StatesGroup):
    choosing_reminder = State()
    confirming = State()


class ClearAllState(StatesGroup):
    confirming = State()


class AutoReportState(StatesGroup):
    entering_time = State()
    choosing_period = State()

class DangerDayState(StatesGroup):
    choosing_reason = State()
    entering_custom = State()


class BybitKeyState(StatesGroup):
    api_key = State()
    api_secret = State()


class BybitImportState(StatesGroup):
    choosing = State()


class AutoStopState(StatesGroup):
    choosing_trade = State()
    choosing_vol = State()
    entering_custom = State()
    confirming = State()


class NotifyState(StatesGroup):
    choosing = State()

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


def fmt_price(val: float) -> str:
    return f"{val:.2f}".rstrip("0").rstrip(".")


async def present_auto_calc(msg: types.Message, state: FSMContext, vol: float) -> None:
    data = await state.get_data()
    entry = data["entry"]
    t_type = data["type"]
    pct = data.get("percent") or 0
    dist = entry * vol / 100
    if t_type.lower() == "long":
        stop = entry - dist
        targets = [entry + 1.5 * dist, entry + 2.5 * dist, entry + 4 * dist]
    else:
        stop = entry + dist
        targets = [entry - 1.5 * dist, entry - 2.5 * dist, entry - 4 * dist]
    risk = calc_risk(entry, stop, pct, t_type)
    await state.update_data(stop=stop, targets=targets, vol=vol, risk=risk)
    t1, t2, t3 = [fmt_price(t) for t in targets]
    text = (
        f"📍 Авторасчёт завершён:\n\n"
        f"🛑 Стоп: {fmt_price(stop)}\n"
        "🎯 Цели:\n"
        f"— 1: {t1} (1.5R)\n"
        f"— 2: {t2} (2.5R)\n"
        f"— 3: {t3} (4R)\n\n"
        "💬 Можешь отредактировать вручную или подтвердить."
    )
    buttons = [
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="astc_save"),
            InlineKeyboardButton(text="✏️ Изменить вручную", callback_data=f"edit_{data['tid']}")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_stops")],
    ]
    await state.set_state(AutoStopState.confirming)
    await msg.answer(text, reply_markup=with_back(InlineKeyboardMarkup(inline_keyboard=buttons)))


def save_danger_day(uid: int, reason: str) -> None:
    date_str = datetime.now().date().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO danger_days (user_id, day_date, reason) VALUES (?,?,?)",
            (uid, date_str, reason),
        )
        conn.commit()


def clock_emoji(time_str: str) -> str:
    hour = int(time_str.split(":")[0])
    clocks = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]
    return clocks[hour % 12]


async def fetch_bybit_positions(api_key: str, api_secret: str) -> tuple[bool, list | str]:
    ts = str(int(time.time() * 1000))
    recv = "5000"
    params = {"category": "linear"}
    query = urlencode(params)
    sign_payload = ts + api_key + recv + query
    sign = hmac.new(api_secret.encode(), sign_payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
    }
    url = "https://api.bybit.com/v5/position/list"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                data = await resp.json()
    except Exception:
        return False, "❌ Не удалось связаться с Bybit"
    if data.get("retCode") != 0:
        return False, "❌ Неверный API-ключ"
    items = [
        {
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "leverage": p.get("leverage"),
            "avgPrice": p.get("avgPrice"),
            "size": p.get("size"),
        }
        for p in data.get("result", {}).get("list", [])
        if float(p.get("size", 0)) != 0
    ]
    return True, items


async def fetch_price(symbol: str) -> float | None:
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear", "symbol": f"{symbol}USDT"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
    except Exception:
        return None
    try:
        return float(data.get("result", {}).get("list", [{}])[0].get("lastPrice"))
    except Exception:
        return None


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

SIGNAL_STARS = {name: stars for name, stars in SIGNAL_OPTIONS}

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
    "\nШкала силы: ≤4 слабая • 5–7 умеренная • 8–11 сильная • 12+ очень сильная"
)

# ---------- MISTAKE REASONS ----------
MISTAKE_OPTIONS = [
    ("❌ Слабые сигналы", "Слабые сигналы"),
    ("⏱ Не дождался ретеста", "Не дождался ретеста"),
    ("🤯 Эмоциональный вход", "Эмоциональный вход"),
    ("🔁 Перезаход", "Перезаход"),
    ("📉 Против тренда", "Против тренда"),
    ("🧠 Не по системе", "Не по системе"),
    ("🕒 Передержал", "Передержал"),
]



def signal_stats(names: list[str]) -> tuple[int, int, int, int]:
    total = sum(SIGNAL_STARS.get(n, 0) for n in names)
    strong = sum(1 for n in names if SIGNAL_STARS.get(n, 0) >= 4)
    medium = sum(1 for n in names if 2 <= SIGNAL_STARS.get(n, 0) <= 3)
    weak = sum(1 for n in names if SIGNAL_STARS.get(n, 0) <= 1)
    return total, strong, medium, weak


def strength_label(total: int) -> str:
    if total <= 4:
        return "Слабая"
    if total <= 7:
        return "Умеренная"
    if total <= 11:
        return "Сильная"
    return "Очень сильная"


def list_open_trades(uid: int) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT symbol, trade_type, entry_price, stop_loss, targets, percent FROM trades WHERE user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        return ""
    lines = []
    for sym, t_type, entry, sl, tgt, pct in rows:
        lines.append(f"{sym} {t_type.upper()} вход {entry} стоп {sl} цели {tgt} {pct}%")
    return "\n".join(lines)


def build_auto_report(uid: int, days: int) -> str:
    start = datetime.now() - timedelta(days=days)
    df = pd.read_sql_query(
        "SELECT symbol, pnl, signals FROM trades WHERE user_id=? AND exit_price IS NOT NULL AND exit_date>=? AND COALESCE(is_deleted,0)=0",
        sqlite3.connect(DB_PATH),
        params=(uid, start.isoformat()),
    )
    if df.empty:
        return "Сделок за период нет."
    count = len(df)
    avg_profit = df["pnl"].mean()
    winrate = (df["pnl"] > 0).sum() / count * 100
    coin_mean = df.groupby("symbol")["pnl"].mean()
    best = coin_mean.idxmax()
    worst = coin_mean.idxmin()
    signal_counts = defaultdict(int)
    for s in df["signals"].dropna():
        for sig in s.split(","):
            sig = sig.strip()
            if sig:
                signal_counts[sig] += 1
    top_signal = max(signal_counts, key=signal_counts.get) if signal_counts else "—"
    period_text = "день" if days == 1 else "неделю"
    text = (
        f"📊 Отчёт за {period_text}\n"
        f"Сделок: {count}\n"
        f"Средний % прибыли: {avg_profit:+.2f}%\n"
        f"Winrate: {winrate:.1f}%\n"
        f"Лучший коин: {best} ({coin_mean[best]:+.1f}%)\n"
        f"Худший коин: {worst} ({coin_mean[worst]:+.1f}%)\n"
        f"Самый частый сетап: {top_signal}"
    )
    return text


async def process_notifications(uid: int) -> None:
    now = datetime.now()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT trade_type, symbol, entry_price, stop_loss, targets, entry_date FROM trades WHERE user_id=? AND exit_price IS NULL AND notifications_enabled=1 AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    for t_type, sym, entry, sl, tgt_str, entry_date in rows:
        price = await fetch_price(sym)
        if price is None:
            continue
        alerts = []
        if sl is not None:
            if abs(price - sl) / sl * 100 <= 2:
                alerts.append("⚠️ Цена близко к тейку/стопу!")
        targets = [float(t) for t in (tgt_str or "").split(",") if t]
        for t in targets:
            if (t_type.lower() == "long" and price >= t) or (t_type.lower() == "short" and price <= t):
                alerts.append("🎯 Цель достигнута")
                break
            if abs(price - t) / t * 100 <= 2:
                alerts.append("⚠️ Цена близко к тейку/стопу!")
                break
        entry_dt = datetime.fromisoformat(entry_date)
        if now - entry_dt >= timedelta(hours=48) and abs(price - entry) / entry * 100 < 1:
            alerts.append("💤 Сделка в стагнации — подумай о действиях")
        for msg in alerts:
            try:
                await bot.send_message(uid, f"{sym} {t_type}: {msg}")
            except Exception:
                pass


async def show_reminders_menu(uid: int, message: types.Message) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, remind_time, period_days, next_run FROM reminders WHERE user_id=?",
            (uid,),
        ).fetchall()
        rep = conn.execute(
            "SELECT report_time, period_days, next_run FROM auto_reports WHERE user_id=?",
            (uid,),
        ).fetchone()
    lines = ["🔔 Твои активные напоминания:"]
    for rid, t, period, next_run in rows:
        lines.append(f"• {clock_emoji(t)} {describe_reminder(t, period, next_run)}")
    if not rows:
        lines.append("У тебя нет активных напоминаний.")
    lines.append("")
    if rep:
        lines.append(
            f"📊 Автоотчёт: {clock_emoji(rep[0])} {describe_reminder(rep[0], rep[1], rep[2])}"
        )
    else:
        lines.append("📊 Автоотчёт: отключен")
    kb_rows = [
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="add_reminder"),
            InlineKeyboardButton(text="📊 Автоотчёт", callback_data="auto_report"),
        ]
    ]
    kb_rows.append([
        InlineKeyboardButton(text="⚠️ Сегодня не трейдить", callback_data="danger_day")
    ])
    if rows:
        kb_rows.append([InlineKeyboardButton(text="❌ Удалить напоминание", callback_data="del_reminder")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await message.answer("\n".join(lines), reply_markup=kb)


async def show_notifications_menu(uid: int, message: types.Message) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, trade_type FROM trades WHERE user_id=? AND exit_price IS NULL AND notifications_enabled=1 AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        kb = with_back(
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")]]
            )
        )
        await message.answer("У тебя нет сделок с уведомлениями.", reply_markup=kb)
        return
    buttons = [
        [InlineKeyboardButton(text=f"{sym} {t_type}", callback_data=f"notif_off_{tid}")]
        for tid, sym, t_type in rows
    ]
    buttons.append([InlineKeyboardButton(text="🔕 Выключить все", callback_data="notif_off_all")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    await message.answer("📋 Сделки с уведомлениями:", reply_markup=kb)


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


async def report_scheduler():
    while True:
        now = datetime.now()
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, period_days, next_run FROM auto_reports WHERE next_run<=?",
                (now.isoformat(),),
            ).fetchall()
        for uid, period, next_run in rows:
            text = build_auto_report(uid, period)
            try:
                await bot.send_message(uid, text)
            except Exception:
                pass
            next_time = datetime.fromisoformat(next_run) + timedelta(days=period)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE auto_reports SET next_run=? WHERE user_id=?",
                    (next_time.isoformat(), uid),
                )
                conn.commit()
        await asyncio.sleep(60)


async def notification_scheduler():
    while True:
        with sqlite3.connect(DB_PATH) as conn:
            uids = [row[0] for row in conn.execute(
                "SELECT DISTINCT user_id FROM trades WHERE exit_price IS NULL AND notifications_enabled=1 AND COALESCE(is_deleted,0)=0"
            ).fetchall()]
        for uid in uids:
            await process_notifications(uid)
        await asyncio.sleep(3600 * 24)

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📈 Профиль", callback_data="profile")],
            [InlineKeyboardButton(text="🏆 Рейтинг трейдеров", callback_data="rating")],
            [
                InlineKeyboardButton(text="📦 Сделки", callback_data="trades_menu"),
                InlineKeyboardButton(text="📊 Отчёты", callback_data="reports"),
            ],
            [
                InlineKeyboardButton(text="📅 Напоминания", callback_data="reminders"),
                InlineKeyboardButton(text="🧹 Очистить всё", callback_data="clear_all"),
            ],
            [InlineKeyboardButton(text="🔧 Оптимизация", callback_data="optimization")],
        ]
    )


def trades_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить сделку", callback_data="add_trade"),
                InlineKeyboardButton(text="✅ Закрыть сделку", callback_data="close_trade"),
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить сделку", callback_data="delete_trade"),
                InlineKeyboardButton(text="📤 Выгрузить сделки", callback_data="export_csv"),
            ],
            [
                InlineKeyboardButton(text="📋 Текущие сделки", callback_data="active"),
                InlineKeyboardButton(text="🧾 История сделок", callback_data="history"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
        ]
    )
    return with_back(kb)


def reports_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📈 Графики", callback_data="charts"),
                InlineKeyboardButton(text="🧠 Сетап-анализ", callback_data="setup_analysis"),
            ],
            [
                InlineKeyboardButton(text="⚔ Битва сетапов", callback_data="setup_battle"),
                InlineKeyboardButton(text="🏅 Топ-5 трейдов", callback_data="top_trades"),
            ],
            [
                InlineKeyboardButton(text="📆 Календарь сделок", callback_data="calendar"),
                InlineKeyboardButton(text="🧹 Очистить отчёты", callback_data="clear_reports"),
            ],
            [InlineKeyboardButton(text="🧹 Очистить сетапы", callback_data="reset_setup_analysis")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
        ]
    )
    return with_back(kb)


def optimization_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔁 Загрузить сделки с Bybit", callback_data="opt_bybit"),
                InlineKeyboardButton(text="🧠 AI-Советник", callback_data="opt_ai"),
            ],
            [
                InlineKeyboardButton(text="🛠️ Авторасчёт стопов", callback_data="opt_stops"),
                InlineKeyboardButton(text="📬 Уведомления", callback_data="opt_notify"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Автоматизация [вкл/выкл]", callback_data="opt_toggle"),
                InlineKeyboardButton(text="🤖 Автотрейдинг по стратегии", callback_data="opt_autotrade"),
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
        ]
    )
    return with_back(kb)


def calendar_keyboard(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    now = datetime.now()
    year, month = now.year, now.month
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT exit_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL AND exit_date LIKE ? AND COALESCE(is_deleted,0)=0",
            (uid, f"{year}-{month:02d}-%"),
        ).fetchall()
        drows = conn.execute(
            "SELECT day_date FROM danger_days WHERE user_id=? AND day_date LIKE ?",
            (uid, f"{year}-{month:02d}-%"),
        ).fetchall()
    days_with_trades = {int(r[0].split("-")[2]) for r in rows if r[0]}
    danger_days = {int(r[0].split("-")[2]) for r in drows if r[0]}
    cal = calendar.Calendar().monthdayscalendar(year, month)
    text = f"📆 {MONTHS_RU[month]} {year}\n\nПн Вт Ср Чт Пт Сб Вс\n"
    for week in cal:
        line = ""
        for day in week:
            if day == 0:
                line += "   "
            else:
                if day in danger_days:
                    icon = "⚠️"
                elif day in days_with_trades:
                    icon = "✅"
                else:
                    icon = "▫️"
                line += f"{day:2d}{icon}"
                line += " "
        text += line.rstrip() + "\n"
    text += "\n✅ — есть сделки\n⚠️ — опасный день\n▫️ — сделок нет\n\nНажми на дату, чтобы посмотреть сделки"

    kb_rows = []
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                if day in danger_days:
                    icon = "⚠️"
                elif day in days_with_trades:
                    icon = "✅"
                else:
                    icon = "▫️"
                date_str = f"{year}-{month:02d}-{day:02d}"
                row.append(InlineKeyboardButton(text=f"{day}{icon}", callback_data=f"day_{date_str}"))
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="reports")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=kb_rows))
    return text, kb



def with_back(kb: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Добавляет кнопку «🏠 Меню» в любую inline-клавиатуру"""
    rows = list(kb.inline_keyboard)
    if rows and len(rows[-1]) == 1 and rows[-1][0].text.startswith(("⬅️", "🔙")):
        rows[-1].append(InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu"))
    else:
        rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def signals_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{name} — {'★'*stars}", callback_data=f"sig_{idx}")]
        for idx, (name, stars) in enumerate(SIGNAL_OPTIONS)
    ]
    buttons.append([InlineKeyboardButton(text="🛑 Завершить выбор", callback_data="signals_done")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=buttons))


def mistake_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=disp, callback_data=f"mist_{i}")]
        for i, (disp, _) in enumerate(MISTAKE_OPTIONS)
    ]
    buttons.append([InlineKeyboardButton(text="✍️ Свой вариант", callback_data="mist_custom")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=buttons))


def store_closed_trade(data: dict, reason: str | None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO trades (user_id, trade_type, symbol, entry_price, stop_loss, targets, percent, risk_percent, entry_date, exit_price, exit_date, pnl, profit_percent, comment, signals, signal_stars, mistake_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                data["user_id"],
                data["t_type"],
                data["sym"],
                data["entry_price"],
                data["sl"],
                data["tgt"],
                data["close_pct"],
                data["risk_close"],
                data["entry_date"],
                data["exit_price"],
                data["exit_date"],
                data["pnl"],
                data["profit"],
                data["comment"],
                data["signals"],
                data["sstars"],
                reason,
            ),
        )
        if data["remaining"] <= 0:
            cur.execute("DELETE FROM trades WHERE id=?", (data["trade_id"],))
        else:
            cur.execute(
                "UPDATE trades SET percent=?, risk_percent=? WHERE id=?",
                (data["remaining"], data["risk_remain"], data["trade_id"]),
            )
        conn.commit()


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
    if isinstance(sigs, str):
        sigs = [s for s in sigs.split(';') if s]
    sigs = sigs or []
    lines = [f"• {s} — {'★'*SIGNAL_STARS.get(s, 0)}" for s in sigs] or ["—"]
    total, strong, medium, weak = signal_stats(sigs)
    lines.append(f"Всего звёзд: {total}")
    lines.append(f"Сильные: {strong}, Средние: {medium}, Слабые: {weak}")
    lines.append(f"Сила сделки: {strength_label(total)}")
    lines.append("Шкала: ≤4 Слабая, 5–7 Умеренная, 8–11 Сильная, 12+ Очень сильная")
    text += "\nСигналы:\n" + "\n".join(lines)
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


@dp.callback_query(F.data == "profile")
async def show_profile(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    uid = cb.from_user.id
    df = pd.read_sql_query(
        "SELECT symbol, pnl, signals, entry_date, exit_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
        sqlite3.connect(DB_PATH),
        params=(uid,),
    )
    if df.empty:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
        await cb.message.answer("Нет завершённых сделок.", reply_markup=with_back(kb))
        return
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df["exit_date"] = pd.to_datetime(df["exit_date"], errors="coerce")
    total = len(df)
    wins = (df["pnl"] > 0)
    losses = (df["pnl"] < 0)
    avg_profit = df.loc[wins, "pnl"].mean() if wins.any() else 0
    avg_loss = df.loc[losses, "pnl"].mean() if losses.any() else 0
    winrate = wins.sum() / total * 100 if total else 0
    durations = (df["exit_date"] - df["entry_date"]).dt.days
    avg_duration = durations.mean() if not durations.empty else 0
    signal_counts = defaultdict(int)
    for s in df["signals"].dropna():
        for sig in map(str.strip, s.split(",")):
            if sig:
                signal_counts[sig] += 1
    top_signal = max(signal_counts, key=signal_counts.get) if signal_counts else "—"
    coin_mean = df.groupby("symbol")["pnl"].mean()
    best_coin = coin_mean.idxmax() if not coin_mean.empty else "—"
    if total < 30 or winrate < 30:
        rank = "Новичок"
    elif winrate < 60:
        rank = "Уверенный"
    elif winrate < 75:
        rank = "Снайпер"
    elif winrate < 90:
        rank = "Профи"
    else:
        rank = "БОГ ТРЕЙДА" if total > 50 else "Профи"
    text = (
        "📈 Профиль трейдера:\n"
        f"🧮 Средняя прибыль: {avg_profit:+.2f}%\n"
        f"📉 Средний убыток: {avg_loss:+.2f}%\n"
        f"✅ Винрейт: {winrate:.1f}%\n"
        f"⏳ Средняя длительность сделки: {avg_duration:.1f} дн.\n"
        f"🔢 Количество сделок: {total}\n"
        f"🧠 Самый частый сетап: {top_signal}\n"
        f"💎 Самый прибыльный коин: {best_coin}\n"
        f"🏅 Ранг: {rank}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    await cb.message.answer(text, reply_markup=with_back(kb))


# ---------- RATING ----------
async def build_trader_rating() -> tuple[str, InlineKeyboardMarkup]:
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT user_id,
                   SUM(pnl) AS total_pnl,
                   SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN pnl<=0 THEN 1 ELSE 0 END) AS losses
            FROM trades
            WHERE exit_price IS NOT NULL AND exit_date>=? AND COALESCE(is_deleted,0)=0
            GROUP BY user_id
            ORDER BY total_pnl DESC, wins DESC
            LIMIT 10
            """,
            (month_start,),
        ).fetchall()
    lines = ["🏆 ТОП-10 трейдеров месяца:\n"]
    kb_rows: list[list[InlineKeyboardButton]] = []
    if rows:
        for i, (uid, total, wins, losses) in enumerate(rows, 1):
            chat = await bot.get_chat(uid)
            name = chat.username or chat.full_name or str(uid)
            wl = wins + losses
            winrate = wins * 100 / wl if wl else 0
            lines.append(
                f"{i}. {name} — {total:+.2f}% | ✅ {wins} | ❌ {losses} | {winrate:.0f}%"
            )
            kb_rows.append(
                [InlineKeyboardButton(text=f"{i}. {name}", callback_data=f"rank_{uid}")]
            )
    else:
        lines = ["Нет данных для рейтинга."]
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    return "\n".join(lines), with_back(InlineKeyboardMarkup(inline_keyboard=kb_rows))


async def build_trader_details(uid: int) -> str:
    chat = await bot.get_chat(uid)
    name = chat.username or chat.full_name or str(uid)
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT pnl FROM trades WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
        last = conn.execute(
            """
            SELECT symbol, pnl, exit_date FROM trades
            WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0
            ORDER BY exit_date DESC LIMIT 1
            """,
            (uid,),
        ).fetchone()
    pnls = [r[0] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    avg_profit = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    winrate = len(wins) * 100 / len(pnls) if pnls else 0
    if last:
        lsym, lpnl, ldate = last
        last_text = f"Последняя сделка: {lsym} ({lpnl:+.2f}%) — {ldate}"
    else:
        last_text = "Последняя сделка: —"
    return (
        f"{name}\n"
        f"Средняя прибыль: {avg_profit:+.2f}%\n"
        f"Средний убыток: {avg_loss:+.2f}%\n"
        f"Общий winrate: {winrate:.1f}%\n"
        f"{last_text}"
    )


@dp.callback_query(F.data == "rating")
async def rating_menu(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    text, kb = await build_trader_rating()
    await cb.message.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("rank_"))
async def rating_detail(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    uid = int(cb.data.split("_", 1)[1])
    text = await build_trader_details(uid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="rating")]]
    )
    await cb.message.answer(text, reply_markup=with_back(kb))


@dp.callback_query(F.data == "trades_menu")
async def trades_menu(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await cb.message.answer("📦 Сделки:", reply_markup=trades_menu_kb())

# ---------- REMINDER ----------
@dp.callback_query(F.data == "reminders")
async def reminders_overview(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await show_reminders_menu(cb.from_user.id, cb.message)


@dp.callback_query(F.data == "add_reminder")
async def reminder_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="reminders")]])
    await cb.message.answer(
        "Введите время напоминания (HH:MM):",
        reply_markup=with_back(kb),
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


@dp.callback_query(F.data == "auto_report")
async def auto_report_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="reminders")]])
    await cb.message.answer(
        "Введите время автоотчёта (HH:MM):",
        reply_markup=with_back(kb),
    )
    await state.set_state(AutoReportState.entering_time)


@dp.message(AutoReportState.entering_time)
async def auto_report_time(msg: types.Message, state: FSMContext):
    if not is_time(msg.text):
        await msg.answer("Формат HH:MM")
        return
    await state.update_data(report_time=msg.text.strip())
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Ежедневно", callback_data="arep_1"), InlineKeyboardButton(text="Еженедельно", callback_data="arep_7")],
                [InlineKeyboardButton(text="Отключить", callback_data="arep_off")],
            ]
        )
    )
    await msg.answer("Периодичность автоотчёта:", reply_markup=kb)
    await state.set_state(AutoReportState.choosing_period)


@dp.callback_query(AutoReportState.choosing_period, lambda c: c.data.startswith("arep_"))
async def auto_report_save(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    choice = cb.data.split("_")[1]
    uid = cb.from_user.id
    if choice == "off":
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM auto_reports WHERE user_id=?", (uid,))
            conn.commit()
        await cb.message.answer("Автоотчёты отключены.")
    else:
        period = int(choice)
        data = await state.get_data()
        t = datetime.strptime(data["report_time"], "%H:%M").time()
        now = datetime.now()
        next_run = datetime.combine(now.date(), t)
        if next_run <= now:
            next_run += timedelta(days=period)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO auto_reports (user_id, report_time, period_days, next_run) VALUES (?,?,?,?)",
                (uid, data["report_time"], period, next_run.isoformat()),
            )
            conn.commit()
        names = {1: "ежедневно", 7: "еженедельно"}
        await cb.message.answer(
            f"Автоотчёт в {data['report_time']} {names[period]} включён."
        )
    await state.clear()
    await show_reminders_menu(uid, cb.message)


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
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="reminders")]])
        await cb.message.answer("У тебя нет активных напоминаний.", reply_markup=with_back(kb))
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


@dp.callback_query(F.data == "danger_day")
async def danger_day_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="😵 Эмоционально нестабилен", callback_data="dng_emo"),
                InlineKeyboardButton(text="📉 Рынок неясен", callback_data="dng_market"),
            ],
            [
                InlineKeyboardButton(text="📆 Личный день отдыха", callback_data="dng_dayoff"),
                InlineKeyboardButton(text="💤 Плохой сон / самочувствие", callback_data="dng_sleep"),
            ],
            [InlineKeyboardButton(text="✍️ Другая", callback_data="dng_other")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="reminders")],
        ]
    )
    kb = with_back(kb)
    await cb.message.answer(
        "❗️Ты решил сегодня не входить в сделки.\n\nУкажи причину:",
        reply_markup=kb,
    )
    await state.set_state(DangerDayState.choosing_reason)


@dp.callback_query(DangerDayState.choosing_reason, lambda c: c.data.startswith("dng_"))
async def danger_reason_chosen(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    code = cb.data.split("_")[1]
    reasons = {
        "emo": "Эмоционально нестабилен",
        "market": "Рынок неясен",
        "dayoff": "Личный день отдыха",
        "sleep": "Плохой сон / самочувствие",
    }
    if code == "other":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="danger_day")]]
        )
        kb = with_back(kb)
        await cb.message.answer("Введи свою причину:", reply_markup=kb)
        await state.set_state(DangerDayState.entering_custom)
        return
    reason = reasons.get(code, "")
    save_danger_day(cb.from_user.id, reason)
    await cb.message.answer(f"⚠️ День отмечен как опасный: {reason}")
    await state.clear()
    await show_reminders_menu(cb.from_user.id, cb.message)


@dp.message(DangerDayState.entering_custom)
async def danger_custom_reason(msg: types.Message, state: FSMContext):
    reason = msg.text.strip()
    save_danger_day(msg.from_user.id, reason)
    await msg.answer(f"⚠️ День отмечен как опасный: {reason}")
    await state.clear()
    await show_reminders_menu(msg.from_user.id, msg)
# ---------- TRADE -------------
@dp.callback_query(F.data == "active")
async def show_active(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, symbol, entry_price, stop_loss, targets, percent, entry_date, comment, risk_percent "
        "FROM trades WHERE user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
        (uid,)
    ).fetchall()
    conn.close()

    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="trades_menu")]])
        return await cb.message.answer("У тебя нет активных сделок.", reply_markup=with_back(kb))

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
    ikb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="trades_menu")])
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
            "WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="trades_menu")]])
        await cb.message.answer("История сделок пуста.", reply_markup=with_back(kb))
        return
    lines = []
    for sym, t_type, entry, exit_price, pnl, exit_date, comm, risk in rows:
        line = f"{sym} {t_type.upper()} | {entry} → {exit_price} | {pnl:+.2f}% | {exit_date} | Риск {risk}%"
        if comm:
            line += f"\n💬 {comm}"
        lines.append(line)
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="trades_menu")]])
    kb = with_back(kb)
    await cb.message.answer("📜 История сделок:\n" + text, reply_markup=kb)
    
    # ───────── Edit-mode FSM ─────────
class EditState(StatesGroup):
    """Состояния редактирования сделки"""
    choosing_field: State = State()
    entering_value: State = State()
    choosing_signals: State = State()

async def open_edit_trade(cb: types.CallbackQuery, tid: int, state: FSMContext):
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

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📡 Сигналы", callback_data="field_signals")],
            [InlineKeyboardButton(text="🎯 Цели",   callback_data="field_targets")],
            [InlineKeyboardButton(text="🛑 Стоп",   callback_data="field_sl")],
            [InlineKeyboardButton(text="💼 %",      callback_data="field_pct")],
            [InlineKeyboardButton(text="📆 Дата",   callback_data="field_date")],
            [InlineKeyboardButton(text="💬 Коммент",callback_data="field_comment")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="active")],
        ]
    )
    kb = with_back(kb)
    await cb.message.answer("Что изменить?", reply_markup=kb)
    await state.set_state(EditState.choosing_field)

@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_choose_field(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tid = int(cb.data.split("_")[1])
    await open_edit_trade(cb, tid, state)

@dp.callback_query(lambda c: c.data.startswith("field_"))
async def edit_enter_value(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    field = cb.data.split("_")[1]   # targets / sl / pct / date / comment / signals
    if field == "signals":
        await start_edit_signals(cb, state)
        return
    await state.update_data(field=field)
    prompt = {
        "targets": "Новые цели (через запятую):",
        "sl":      "Новый стоп:",
        "pct":     "Новый % от депо:",
        "date":    "Новая дата (ГГГГ-ММ-ДД):",
        "comment": "Новый комментарий:",
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


async def start_edit_signals(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data.get("tid")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT signals FROM trades WHERE id=?", (tid,)).fetchone()
    existing = [s for s in (row[0] or "").split(";") if s] if row else []
    await state.update_data(signals=existing)
    total, _, _, _ = signal_stats(existing)
    await state.update_data(signals_total=total)
    await cb.message.answer(SIGNALS_TEXT, reply_markup=signals_keyboard())
    await state.set_state(EditState.choosing_signals)


@dp.callback_query(EditState.choosing_signals, lambda c: c.data.startswith("sig_"))
async def edit_add_signal(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    idx = int(cb.data.split("_")[1])
    name, stars = SIGNAL_OPTIONS[idx]
    data = await state.get_data()
    signals = data.get("signals", [])
    if name not in signals:
        signals.append(name)
        await state.update_data(signals=signals)
        total, strong, medium, weak = signal_stats(signals)
        await state.update_data(signals_total=total)
        summary = (
            f"✅ Сигнал добавлен: \"{name}\" ({'★'*stars})\n\n"
            f"Всего: ★{total}\n"
            f"Сильные: {strong}, Средние: {medium}, Слабые: {weak}\n"
            f"Сила сделки: {strength_label(total)}\n"
            "Шкала: ≤4 Слабая, 5–7 Умеренная, 8–11 Сильная, 12+ Очень сильная"
        )
        await cb.message.answer(summary)
    else:
        await cb.message.answer(f"⚠️ Сигнал уже выбран: \"{name}\"")
    await cb.message.answer("Выбирай дальше:", reply_markup=signals_keyboard())


@dp.callback_query(EditState.choosing_signals, F.data == "signals_done")
async def edit_signals_done(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    tid = data.get("tid")
    signals = data.get("signals", [])
    total, _, _, _ = signal_stats(signals)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE trades SET signals=?, signal_stars=? WHERE id=?",
            (";".join(signals), total, tid),
        )
        conn.commit()
    await cb.message.answer("Сигналы обновлены.")
    await open_edit_trade(cb, tid, state)

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


async def start_signals_choice(uid: int, state: FSMContext, reset: bool = False):
    if reset:
        await state.update_data(signals=[], signals_total=0)
    data = await state.get_data()
    signals = data.get("signals", [])
    total, _, _, _ = signal_stats(signals)
    await state.update_data(signals_total=total)
    await bot.send_message(uid, SIGNALS_TEXT, reply_markup=signals_keyboard())
    await state.set_state(TradeState.choosing_signals)

@dp.message(TradeState.entering_comment)
async def add_trade_comment(msg: types.Message, state: FSMContext):
    comment = msg.text.strip()
    if comment == "-" or comment == "":
        comment = None
    await state.update_data(comment=comment, signals=[])
    await start_signals_choice(msg.from_user.id, state, reset=True)


@dp.callback_query(TradeState.choosing_signals, lambda c: c.data.startswith("sig_"))
async def add_signal(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    idx = int(cb.data.split("_")[1])
    name, stars = SIGNAL_OPTIONS[idx]
    data = await state.get_data()
    signals = data.get("signals", [])
    if name not in signals:
        signals.append(name)
        await state.update_data(signals=signals)
        total, strong, medium, weak = signal_stats(signals)
        await state.update_data(signals_total=total)
        summary = (
            f"✅ Сигнал добавлен: \"{name}\" ({'★'*stars})\n\n"
            f"Всего: ★{total}\n"
            f"Сильные: {strong}, Средние: {medium}, Слабые: {weak}\n"
            f"Сила сделки: {strength_label(total)}\n"
            "Шкала: ≤4 Слабая, 5–7 Умеренная, 8–11 Сильная, 12+ Очень сильная"
        )
        await cb.message.answer(summary)
    else:
        await cb.message.answer(f"⚠️ Сигнал уже выбран: \"{name}\"")
    await cb.message.answer("Выбирай дальше:", reply_markup=signals_keyboard())


@dp.callback_query(TradeState.choosing_signals, F.data == "signals_done")
async def signals_done(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await show_trade_summary(cb.from_user.id, state)

async def show_trade_summary(uid: int, state: FSMContext):
    data = await state.get_data()
    text = "<b>Сводка сделки</b>\n\n" + format_trade(data)
    kb = with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💡 Оценить сетап", callback_data="signals_eval")],
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_add"),
                 InlineKeyboardButton(text="🔁 Изменить", callback_data="add_trade")]
            ]
        )
    )
    await bot.send_message(uid, text, reply_markup=kb)
    await state.set_state(TradeState.confirming)


@dp.callback_query(TradeState.confirming, F.data == "signals_eval")
async def evaluate_setup(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    data = await state.get_data()
    signals = data.get("signals", [])
    total = sum(SIGNAL_STARS.get(n, 0) for n in signals)
    strong = sum(1 for n in signals if SIGNAL_STARS.get(n, 0) >= 3)
    risk = data.get("risk")
    parts = [f"⭐️ Звёзд: {total}", f"🔥 Сильных сигналов: {strong}"]
    if risk is not None:
        parts.append(f"🛑 Риск по стопу: {risk:.1f}%")
    text = "\n".join(parts) + "\n\n"
    if strong < 2 or total < 6:
        text += (
            f"⚠️ Внимание: Мало сильных сигналов ({strong} из 3).\n"
            f"Всего {total} звёзд — сделка выглядит слабой.\n"
            "Уверен, что хочешь продолжать?"
        )
    else:
        text += (
            "💡 Отличная сделка: сильные сигналы + адекватный риск.\n"
            "Совет: убедись, что нет сопротивления выше цели."
        )
    await cb.message.answer(text)


async def save_trade(cb: types.CallbackQuery, state: FSMContext) -> int:
    data = await state.get_data()
    signals = data.get('signals', [])
    total, _, _, _ = signal_stats(signals)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO trades (user_id, trade_type, symbol, entry_price, position_size, leverage, stop_loss, targets, percent, risk_percent, entry_date, comment, signals, signal_stars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cb.from_user.id,
                data['trade_type'],
                data['symbol'],
                data['entry_price'],
                None,
                None,
                data['stop_loss'],
                data['targets'],
                data['percent'],
                data['risk'],
                data['entry_date'],
                data.get('comment'),
                ";".join(signals),
                total,
            ),
        )
        conn.commit()
        trade_id = cur.lastrowid
    await cb.message.answer("✅ Сделка сохранена.")
    return trade_id


async def ask_notifications(uid: int, trade_id: int, state: FSMContext) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="notif_yes"),
                InlineKeyboardButton(text="Нет", callback_data="notif_no"),
            ]
        ]
    )
    await bot.send_message(uid, "🔔 Включить уведомления для этой сделки?", reply_markup=kb)
    await state.update_data(notif_trade_id=trade_id)
    await state.set_state(NotifyState.choosing)


@dp.callback_query(TradeState.confirming, lambda c: c.data == "confirm_add")
async def add_trade_confirm(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    signals = data.get('signals', [])
    total, strong, _, _ = signal_stats(signals)
    if strong < 2 or total < 6:
        warn = (
            "⚠️ Внимание!\n"
            "У сделки недостаточно сильных сигналов:\n"
            f"– Сильных: {strong} (нужно 2–3+)\n"
            f"– Всего звёздочек: {total} (рекомендуется 6+)\n"
            "Такой вход может быть рискованным.\n"
            "Вы уверены, что хотите сохранить сделку?"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_force")],
                [InlineKeyboardButton(text="❌ Отменить", callback_data="confirm_cancel")],
            ]
        )
        await cb.message.answer(warn, reply_markup=kb)
    else:
        tid = await save_trade(cb, state)
        await state.clear()
        await ask_notifications(cb.from_user.id, tid, state)


@dp.callback_query(TradeState.confirming, F.data == "confirm_force")
async def add_trade_force(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tid = await save_trade(cb, state)
    await state.clear()
    await ask_notifications(cb.from_user.id, tid, state)


@dp.callback_query(TradeState.confirming, F.data == "confirm_cancel")
async def add_trade_cancel(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await start_signals_choice(cb.from_user.id, state)


@dp.callback_query(NotifyState.choosing, F.data == "notif_yes")
async def notif_yes(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    data = await state.get_data()
    tid = data.get("notif_trade_id")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE trades SET notifications_enabled=1 WHERE id=?", (tid,))
        conn.commit()
    await cb.message.answer("🔔 Уведомления включены.")
    await go_home(cb.from_user.id, state)


@dp.callback_query(NotifyState.choosing, F.data == "notif_no")
async def notif_no(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Ок, уведомления выключены.")
    await go_home(cb.from_user.id, state)

# ---------- CLOSE TRADE ----------
@dp.callback_query(lambda c: c.data == "close_trade")
async def close_trade_list(cb: types.CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
        SELECT id, trade_type, symbol, entry_price
        FROM trades
        WHERE user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0
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
        total_pct = conn.execute("SELECT percent FROM trades WHERE id=? AND COALESCE(is_deleted,0)=0", (tid,)).fetchone()[0]
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
    tid = data["trade_id"]
    close_pct = data["close_percent"]
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        user_id, t_type, sym, entry_price, sl, tgt, percent, entry_date, comment, signals, sstars = cur.execute(
            "SELECT user_id, trade_type, symbol, entry_price, stop_loss, targets, percent, entry_date, comment, signals, signal_stars FROM trades WHERE id=? AND COALESCE(is_deleted,0)=0",
            (tid,),
        ).fetchone()
    pnl = ((exit_price - entry_price) / entry_price) * (100 if t_type.lower() == "long" else -100)
    profit = round(pnl * close_pct / 100, 2)
    exit_date = datetime.now().strftime("%Y-%m-%d")
    risk_close = calc_risk(entry_price, sl, close_pct, t_type)
    remaining = percent - close_pct
    risk_remain = calc_risk(entry_price, sl, remaining, t_type) if remaining > 0 else None
    close_data = dict(
        user_id=user_id,
        t_type=t_type,
        sym=sym,
        entry_price=entry_price,
        sl=sl,
        tgt=tgt,
        entry_date=entry_date,
        comment=comment,
        signals=signals,
        sstars=sstars,
        exit_price=exit_price,
        exit_date=exit_date,
        pnl=pnl,
        profit=profit,
        remaining=remaining,
        close_pct=close_pct,
        risk_close=risk_close,
        risk_remain=risk_remain,
        trade_id=tid,
    )
    if pnl < 0:
        await state.update_data(**close_data)
        await msg.answer("Почему сделка пошла не так? Выберите причину:", reply_markup=mistake_keyboard())
        await state.set_state(CloseTradeState.choosing_reason)
        return
    store_closed_trade(close_data, None)
    await msg.answer(f"Закрыто {close_pct}% | PNL: {pnl:+.2f}% | Profit: {profit}%")
    await go_home(msg.from_user.id, state)

@dp.callback_query(CloseTradeState.choosing_reason, lambda c: c.data.startswith("mist_") and c.data != "mist_custom")
async def choose_mistake(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    idx = int(cb.data.split("_")[1])
    reason = MISTAKE_OPTIONS[idx][1]
    data = await state.get_data()
    store_closed_trade(data, reason)
    await cb.message.answer(f"Закрыто {data['close_pct']}% | PNL: {data['pnl']:+.2f}% | Profit: {data['profit']}%")
    await go_home(cb.from_user.id, state)

@dp.callback_query(CloseTradeState.choosing_reason, F.data == "mist_custom")
async def custom_mistake(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Напиши свою причину:")
    await state.set_state(CloseTradeState.entering_custom_reason)

@dp.message(CloseTradeState.entering_custom_reason)
async def save_custom_mistake(msg: types.Message, state: FSMContext):
    reason = msg.text.strip()
    data = await state.get_data()
    store_closed_trade(data, reason)
    await msg.answer(f"Закрыто {data['close_pct']}% | PNL: {data['pnl']:+.2f}% | Profit: {data['profit']}%")
    await go_home(msg.from_user.id, state)
# ---------- DELETE TRADE ----------
@dp.callback_query(lambda c: c.data == "delete_trade")
async def delete_trade_list(cb: types.CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    df = pd.read_sql_query(
        "SELECT id, trade_type, symbol, entry_price FROM trades WHERE user_id=? AND COALESCE(is_deleted,0)=0",
        sqlite3.connect(DB_PATH), params=(uid,)
    )
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
        conn.execute("UPDATE trades SET is_deleted=1 WHERE id=?", (tid,))
    await cb.message.answer("Сделка удалена.")
    await go_home(cb.from_user.id, state)

# ---------- EXPORT CSV ----------
@dp.callback_query(lambda c: c.data == "export_csv")
async def export_csv(cb: types.CallbackQuery):
    uid = cb.from_user.id
    df = pd.read_sql_query(
        "SELECT * FROM trades WHERE user_id=? AND COALESCE(is_deleted,0)=0",
        sqlite3.connect(DB_PATH), params=(uid,)
    )
    if df.empty:
        await cb.message.answer("Нет данных.")
        return
    path = f"trades_{uid}.csv"
    df.to_csv(path, index=False)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="trades_menu")]])
    kb = with_back(kb)
    await bot.send_document(uid, FSInputFile(path), caption="📤 Твои сделки", reply_markup=kb)

# ---------- REPORTS ----------
@dp.callback_query(lambda c: c.data == "reports")
async def reports(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    uid = cb.from_user.id
    df = pd.read_sql_query(
        "SELECT symbol, pnl, entry_date, exit_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
        sqlite3.connect(DB_PATH), params=(uid,),
    )
    if df.empty:
        await cb.message.answer("Нет завершённых сделок.", reply_markup=reports_menu_kb())
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
    with sqlite3.connect(DB_PATH) as conn:
        drows = conn.execute(
            "SELECT day_date FROM danger_days WHERE user_id=?",
            (uid,),
        ).fetchall()
    danger_dates = [r[0] for r in drows]
    dd_df = df[df["entry_date"].dt.strftime("%Y-%m-%d").isin(danger_dates)]
    dd_total = len(dd_df)
    dd_wins = (dd_df["pnl"] > 0).sum()
    dd_losses = (dd_df["pnl"] <= 0).sum()
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
    if danger_dates:
        text += (
            f"\n\n⚠️ В «опасные» дни ты:\n"
            f"– всё же открыл сделки: {dd_total} раз\n"
            f"– из них: {dd_wins} в плюс | {dd_losses} в минус"
        )
        if dd_total and dd_losses > dd_wins:
            text += "\n– Вывод: ты действительно чаще ошибаешься в такие дни."
        elif dd_total:
            text += "\n– Вывод: дисциплина не страдает."
    await cb.message.answer(text, reply_markup=reports_menu_kb())


# ---------- OPTIMIZATION ----------
@dp.callback_query(F.data == "optimization")
async def optimization_menu(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    await state.clear()
    await cb.message.answer("🔧 Оптимизация:", reply_markup=optimization_menu_kb())


@dp.callback_query(F.data == "opt_bybit")
async def opt_bybit(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT api_key, api_secret FROM bybit_keys WHERE user_id=?",
            (uid,),
        ).fetchone()
    if not row or not row[0] or not row[1]:
        await state.set_state(BybitKeyState.api_key)
        await cb.message.answer(
            "🔐 Введите свой API-ключ и Secret от Bybit (USDT Perpetual)\n\nСначала отправьте API-ключ:",
        )
        return
    ok, res = await fetch_bybit_positions(row[0], row[1])
    if not ok:
        await cb.message.answer(res)
        return
    positions = res
    if not positions:
        await cb.message.answer("❌ У тебя сейчас нет открытых сделок на Bybit")
        return
    buttons = []
    for idx, p in enumerate(positions):
        side = "LONG" if p.get("side") == "Buy" else "SHORT"
        sym = p.get("symbol", "")
        if sym.endswith("USDT"):
            sym = sym[:-4]
        lev = p.get("leverage", "")
        buttons.append([
            InlineKeyboardButton(text=f"{side} {sym} {lev}x", callback_data=f"byimp_{idx}")
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(BybitImportState.choosing)
    await state.update_data(positions=positions)
    await cb.message.answer("Выбери сделку для импорта:", reply_markup=kb)


@dp.callback_query(lambda c: c.data in {
    "opt_toggle",
    "opt_autotrade",
})
async def optimization_stub(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    await cb.message.answer("🔒 Функция в разработке. Следи за обновлениями!")


@dp.callback_query(F.data == "opt_stops")
async def auto_stop_choose_trade(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, trade_type, entry_price, percent FROM trades "
            "WHERE user_id=? AND exit_price IS NULL AND entry_price IS NOT NULL "
            "AND trade_type IS NOT NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="optimization")]])
        await cb.message.answer("❌ Нет сделок с входом для расчёта.", reply_markup=with_back(kb))
        return
    buttons = []
    for tid, sym, ttype, entry, pct in rows:
        side = "Long" if ttype.lower() == "long" else "Short"
        buttons.append([
            InlineKeyboardButton(text=f"🔹 {sym} / {side} / Вход: {fmt_price(entry)}", callback_data=f"ast_{tid}")
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(AutoStopState.choosing_trade)
    await cb.message.answer("Выбери сделку для расчёта:", reply_markup=kb)


@dp.callback_query(AutoStopState.choosing_trade, lambda c: c.data.startswith("ast_"))
async def auto_stop_vol(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tid = int(cb.data.split("_", 1)[1])
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT trade_type, entry_price, percent FROM trades WHERE id=? AND user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (tid, cb.from_user.id),
        ).fetchone()
    if not row:
        await cb.message.answer("❌ Сделка не найдена.")
        await state.clear()
        return
    await state.update_data(tid=tid, type=row[0], entry=row[1], percent=row[2])
    buttons = [
        [InlineKeyboardButton(text="Низкая (2%)", callback_data="astv_2"), InlineKeyboardButton(text="Средняя (4%)", callback_data="astv_4")],
        [InlineKeyboardButton(text="Высокая (6%)", callback_data="astv_6"), InlineKeyboardButton(text="Другая", callback_data="astv_custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_stops")],
    ]
    await state.set_state(AutoStopState.choosing_vol)
    await cb.message.answer(
        "📊 Укажи ожидаемую волатильность (%) или выбери стандарт:",
        reply_markup=with_back(InlineKeyboardMarkup(inline_keyboard=buttons)),
    )


@dp.callback_query(AutoStopState.choosing_vol, lambda c: c.data.startswith("astv_") and c.data != "astv_custom")
async def auto_stop_calc(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    vol = float(cb.data.split("_", 1)[1])
    await present_auto_calc(cb.message, state, vol)


@dp.callback_query(AutoStopState.choosing_vol, F.data == "astv_custom")
async def auto_stop_custom_prompt(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Введи волатильность в %:")
    await state.set_state(AutoStopState.entering_custom)


@dp.message(AutoStopState.entering_custom)
async def auto_stop_custom(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        await msg.answer("Введите число.")
        return
    vol = float(msg.text.replace(",", "."))
    await present_auto_calc(msg, state, vol)


@dp.callback_query(AutoStopState.confirming, F.data == "astc_save")
async def auto_stop_save(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    stop = data["stop"]
    targets = data["targets"]
    tid = data["tid"]
    risk = data.get("risk", 0)
    targets_str = ",".join(fmt_price(t) for t in targets)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE trades SET stop_loss=?, targets=?, risk_percent=? WHERE id=?",
            (stop, targets_str, risk, tid),
        )
        conn.commit()
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="optimization")]])
    await cb.message.answer("✅ Авторасчёт применён.", reply_markup=with_back(kb))


@dp.callback_query(F.data == "opt_ai")
async def ai_advisor_list(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, trade_type, signals, signal_stars FROM trades "
            "WHERE user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")]])
        await cb.message.answer("У тебя нет активных сделок.", reply_markup=with_back(kb))
        return
    buttons = []
    for i, (tid, sym, t_type, sigs, stars) in enumerate(rows, 1):
        total = stars if stars is not None else sum(SIGNAL_STARS.get(s, 0) for s in (sigs or "").split(";") if s)
        buttons.append([
            InlineKeyboardButton(text=f"{i}. {sym} / {t_type.capitalize()} / +{total}★", callback_data=f"ai_{tid}")
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.message.answer("Выбери сделку для анализа:", reply_markup=kb)


@dp.callback_query(lambda c: c.data.startswith("ai_"))
async def ai_advisor_run(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    tid = int(cb.data.split("_")[1])
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT signals, signal_stars, risk_percent FROM trades "
            "WHERE id=? AND user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (tid, uid),
        ).fetchone()
    if not row:
        await cb.message.answer("Сделка не найдена.")
        return
    signals, stars, risk = row
    sig_list = [s for s in (signals or "").split(";") if s]
    if not sig_list or not risk:
        await cb.message.answer(
            "🔒 В этой сделке не указаны сигналы или риск.\n\n❗ Сначала укажи их — нажми \"📝 Изменить\", выбери сигналы и % риска."
        )
        await open_edit_trade(cb, tid, state)
        return
    total, strong, _, _ = signal_stats(sig_list)
    risk = float(risk)
    if strong >= 2 and risk <= 10:
        text = (
            "💡 Сетап оценён!\n\n"
            f"— Сигналы: {strong} сильных | Общий рейтинг: {total}★\n"
            f"— Риск: {risk:.1f}%\n\n"
            "📊 Анализ:\n"
            "✅ Отличное соотношение сигналов и риска.\n"
            "💬 Совет: проверь объёмы на 4H, возможен откат."
        )
    else:
        text = (
            "⚠️ Осторожно: слабый сетап\n\n"
            f"— Сигналы: {strong} сильных | Общий рейтинг: {total}★\n"
            f"— Риск: {risk:.1f}%\n\n"
            "📊 Анализ:\n"
            "❌ Недостаточно подтверждающих сигналов.\n"
            "🔺 Повышенный риск.\n"
            "💬 Совет: дождись ретеста или усиливающего сигнала."
        )
    kb = with_back(InlineKeyboardMarkup([[InlineKeyboardButton(text="🔙 Назад", callback_data="opt_ai")]]))
    await cb.message.answer(text, reply_markup=kb)


@dp.message(BybitKeyState.api_key)
async def bybit_enter_key(msg: types.Message, state: FSMContext):
    await state.update_data(api_key=msg.text.strip())
    await state.set_state(BybitKeyState.api_secret)
    await msg.answer("Теперь введите Secret-ключ:")


@dp.message(BybitKeyState.api_secret)
async def bybit_enter_secret(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    api_key = data.get("api_key")
    api_secret = msg.text.strip()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bybit_keys (user_id, api_key, api_secret) VALUES (?,?,?)",
            (msg.from_user.id, api_key, api_secret),
        )
        conn.commit()
    await state.clear()
    await msg.answer("✅ Ключи сохранены. Нажми «🔁 Загрузить сделки с Bybit» ещё раз.")


@dp.callback_query(BybitImportState.choosing, lambda c: c.data.startswith("byimp_"))
async def import_bybit_trade(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    positions = data.get("positions", [])
    idx = int(cb.data.split("_")[1])
    if idx >= len(positions):
        await cb.message.answer("Сделка не найдена.")
        return
    pos = positions[idx]
    t_type = "Long" if pos.get("side") == "Buy" else "Short"
    sym = pos.get("symbol", "")
    if sym.endswith("USDT"):
        sym = sym[:-4]
    entry = float(pos.get("avgPrice") or 0)
    size = float(pos.get("size") or 0)
    lev = float(pos.get("leverage") or 0)
    entry_date = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO trades (user_id, trade_type, symbol, entry_price, position_size, leverage, stop_loss, targets, percent, risk_percent, entry_date, comment, signals, signal_stars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cb.from_user.id,
                t_type,
                sym,
                entry,
                size,
                lev,
                None,
                None,
                None,
                None,
                entry_date,
                "Импорт из Bybit",
                None,
                None,
            ),
        )
        conn.commit()
        trade_id = cur.lastrowid
    await state.clear()
    await cb.message.answer(
        "✅ Сделка успешно импортирована из Bybit!\nНе забудь указать стоп и цели — нажми \"📝 Изменить\" в текущих сделках."
    )
    await ask_notifications(cb.from_user.id, trade_id, state)


@dp.callback_query(F.data == "opt_notify")
async def opt_notify(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    await process_notifications(cb.from_user.id)
    await show_notifications_menu(cb.from_user.id, cb.message)


@dp.callback_query(lambda c: c.data.startswith("notif_off"))
async def notif_off(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    if cb.data == "notif_off_all":
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE trades SET notifications_enabled=0 WHERE user_id=?", (uid,)
            )
            conn.commit()
        await cb.message.answer("🔕 Уведомления отключены для всех сделок.")
    else:
        tid = int(cb.data.split("_")[2])
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE trades SET notifications_enabled=0 WHERE id=? AND user_id=?",
                (tid, uid),
            )
            conn.commit()
        await cb.message.answer("🔕 Уведомления отключены.")
    await show_notifications_menu(uid, cb.message)


@dp.callback_query(F.data == "clear_reports")
async def clear_reports(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM trades WHERE user_id=?", (uid,))
        conn.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="reports")]])
    await cb.message.answer("Отчёты очищены.", reply_markup=with_back(kb))


@dp.callback_query(F.data == "calendar")
async def show_calendar(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    text, kb = calendar_keyboard(cb.from_user.id)
    await cb.message.answer(text, reply_markup=kb)


@dp.callback_query(lambda c: c.data.startswith("day_"))
async def show_day_trades(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    date_str = cb.data.split("_", 1)[1]
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT symbol, trade_type, pnl, signals, signal_stars FROM trades WHERE user_id=? AND exit_date=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
            (uid, date_str),
        ).fetchall()
    lines = []
    for i, (sym, t_type, pnl, signals, stars) in enumerate(rows, 1):
        sigs = signals.split(";") if signals else []
        lines.append(
            f"{i}. {sym} {t_type.upper()} ({pnl:+.1f}%) — {len(sigs)} сигнала ({stars}⭐️)"
        )
    if lines:
        text = "\n".join(lines)
    else:
        text = "Сделок нет."
    day = int(date_str[-2:])
    month = int(date_str[5:7])
    title = f"{day} {MONTHS_RU_GEN[month]}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="calendar")]])
    await cb.message.answer(f"📅 Сделки {title}:\n" + text, reply_markup=with_back(kb))


@dp.callback_query(F.data == "ignore")
async def ignore_cb(cb: types.CallbackQuery):
    await cb.answer()


@dp.callback_query(F.data == "clear_all")
async def clear_all(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    text = (
        "⚠️ Вы собираетесь удалить ВСЕ данные:\n"
        "– Сделки (все типы)\n"
        "– Отчёты\n"
        "– Сетап-аналитику\n"
        "– Напоминания\n\n"
        "Это действие НЕОБРАТИМО.\n"
        "Чтобы подтвердить, введите: of course\n"
        "Или нажмите кнопку «Отмена»"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_clear_all")]]
    )
    await cb.message.answer(text, reply_markup=with_back(kb))
    await state.set_state(ClearAllState.confirming)


@dp.callback_query(F.data == "cancel_clear_all")
async def cancel_clear_all(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Операция отменена.")
    await go_home(cb.from_user.id, state)


@dp.message(ClearAllState.confirming)
async def clear_all_confirm(msg: types.Message, state: FSMContext):
    if msg.text.strip().lower() == "of course":
        uid = msg.from_user.id
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM trades WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM reminders WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM auto_reports WHERE user_id=?", (uid,))
            conn.commit()
        await msg.answer("Все данные очищены.")
    else:
        await msg.answer("Очистка отменена.")
    await go_home(msg.from_user.id, state)


def build_setup_battle(uid: int) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signals, profit_percent FROM trades
            WHERE user_id=? AND exit_price IS NOT NULL AND signals IS NOT NULL AND signals != '' AND COALESCE(is_deleted,0)=0
            """,
            (uid,),
        )
        rows = cur.fetchall()
    if not rows:
        return "Нет завершённых сделок с сигналами."
    combo_stats: dict[str, dict[str, float]] = {}
    for sig_str, pct in rows:
        sigs = [s for s in sig_str.split(";") if s]
        if len(sigs) < 2:
            continue
        win = pct > 0
        for r in range(2, min(len(sigs), 5) + 1):
            for combo in combinations(sorted(set(sigs)), r):
                key = " + ".join(combo)
                st = combo_stats.setdefault(key, {"count": 0, "profit_sum": 0.0, "wins": 0})
                st["count"] += 1
                st["profit_sum"] += pct
                if win:
                    st["wins"] += 1
    if not combo_stats:
        return "Нет связок сигналов."
    lines = ["⚔ Битва сетапов", ""]
    for st in combo_stats.values():
        st["avg"] = st["profit_sum"] / st["count"]
        st["wr"] = st["wins"] / st["count"] * 100
    best = [item for item in combo_stats.items() if item[1]["avg"] > 0]
    best = sorted(best, key=lambda kv: kv[1]["avg"], reverse=True)[:5]
    if best:
        lines.append("🏆 Топ-5 связок по прибыли:")
        for i, (name, st) in enumerate(best, 1):
            lines.append(
                f"№{i}: {name} — {st['count']} сделок / {st['avg']:+.1f}% / winrate {st['wr']:.0f}%"
            )
    else:
        lines.append("Нет прибыльных связок.")
    worst = [item for item in combo_stats.items() if item[1]["avg"] < 0]
    worst = sorted(worst, key=lambda kv: kv[1]["avg"])[:5]
    if worst:
        lines.append("\n💀 Худшие связки:")
        for i, (name, st) in enumerate(worst, 1):
            lines.append(
                f"№{i}: {name} — {st['count']} сделок / {st['avg']:+.1f}% / winrate {st['wr']:.0f}%"
            )
    else:
        lines.append("\nНет убыточных связок.")
    return "\n".join(lines)


def build_setup_analysis(uid: int) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signals, profit_percent FROM trades
            WHERE user_id=? AND exit_price IS NOT NULL AND signals IS NOT NULL AND signals != '' AND COALESCE(is_deleted,0)=0
            """,
            (uid,),
        )
        rows = cur.fetchall()
    if not rows:
        return "Нет завершённых сделок с сигналами."
    stats: dict[str, dict[str, float]] = {}
    pair_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
    for sig_str, pct in rows:
        sigs = [s for s in sig_str.split(";") if s]
        win = pct > 0
        for s in sigs:
            st = stats.setdefault(s, {"count": 0, "wins": 0, "losses": 0, "profit_sum": 0.0, "loss_sum": 0.0})
            st["count"] += 1
            if win:
                st["wins"] += 1
                st["profit_sum"] += pct
            else:
                st["losses"] += 1
                st["loss_sum"] += pct
        if len(sigs) >= 2:
            for combo in combinations(sorted(set(sigs)), 2):
                key = " + ".join(combo)
                if win:
                    pair_stats[key]["wins"] += 1
                else:
                    pair_stats[key]["losses"] += 1
    lines = ["📊 Аналитика по сетапам:", ""]
    for name, st in sorted(stats.items(), key=lambda kv: kv[1]["count"], reverse=True):
        avg_profit = st["profit_sum"] / st["wins"] if st["wins"] else 0
        avg_loss = st["loss_sum"] / st["losses"] if st["losses"] else 0
        winrate = st["wins"] / st["count"] * 100 if st["count"] else 0
        lines.append(
            f"• {name} — {st['count']} раз, побед {st['wins']}, убытков {st['losses']}, "
            f"ср.прибыль {avg_profit:+.1f}%, ср.убыток {avg_loss:+.1f}%, WR {winrate:.1f}%"
        )
    top_wr = sorted(stats.items(), key=lambda kv: kv[1]["wins"] / kv[1]["count"] if kv[1]["count"] else 0, reverse=True)[:5]
    if top_wr:
        lines.append("\nТОП-5 по винрейту:")
        for name, st in top_wr:
            wr = st["wins"] / st["count"] * 100 if st["count"] else 0
            lines.append(f"{name} — {wr:.1f}% ({st['count']})")
    top_profit = [item for item in stats.items() if item[1]["wins"]]
    top_profit = sorted(top_profit, key=lambda kv: kv[1]["profit_sum"] / kv[1]["wins"], reverse=True)[:5]
    if top_profit:
        lines.append("\nТОП-5 по среднему профиту:")
        for name, st in top_profit:
            lines.append(f"{name} — {st['profit_sum'] / st['wins']:.1f}%")
    top_losses = sorted(stats.items(), key=lambda kv: kv[1]["losses"], reverse=True)[:5]
    if top_losses:
        lines.append("\nТОП-5 по частоте в убыточных:")
        for name, st in top_losses:
            lines.append(f"{name} — {st['losses']}")
    profit_pairs = sorted(pair_stats.items(), key=lambda kv: kv[1]["wins"], reverse=True)[:5]
    if profit_pairs:
        lines.append("\nСвязки в профитных:")
        for pair, st in profit_pairs:
            lines.append(f"{pair} — {st['wins']}")
    loss_pairs = sorted(pair_stats.items(), key=lambda kv: kv[1]["losses"], reverse=True)[:5]
    if loss_pairs:
        lines.append("\nСвязки в убытках:")
        for pair, st in loss_pairs:
            lines.append(f"{pair} — {st['losses']}")
    return "\n".join(lines)


def build_top_trades(uid: int) -> str:
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT symbol, pnl, signals, entry_date, exit_date, signal_stars
            FROM trades
            WHERE user_id=? AND exit_price IS NOT NULL AND exit_date>=? AND COALESCE(is_deleted,0)=0
            ORDER BY pnl DESC
            LIMIT 5
            """,
            (uid, since),
        ).fetchall()
        best = conn.execute(
            """
            SELECT symbol, signal_stars FROM trades
            WHERE user_id=? AND exit_price IS NOT NULL AND exit_date>=? AND signal_stars IS NOT NULL AND COALESCE(is_deleted,0)=0
            ORDER BY signal_stars DESC LIMIT 1
            """,
            (uid, since),
        ).fetchone()
    if not rows:
        return "❌ Недостаточно данных для рейтинга. Попробуй попозже!"
    lines = ["🏅 Топ-5 трейдов за последний месяц:\n"]
    for i, (sym, pnl, sig_str, entry_date, exit_date, _) in enumerate(rows, 1):
        sigs = []
        if sig_str:
            for s in sig_str.split(";"):
                if s:
                    sigs.append(f"{s} — {'★'*SIGNAL_STARS.get(s, 0)}")
        sig_text = "; ".join(sigs) if sigs else "—"
        lines.append(
            f"{i}. 📍 {sym}\n   🎯 {pnl:+.2f}%\n   🧠 {sig_text}\n   🕓 {entry_date} — {exit_date}"
        )
    if best:
        lines.append(
            "\n⭐️ Самый высокозвёздочный сетап месяца:\n"
            f"📍 {best[0]} — {best[1]}★"
        )
    return "\n".join(lines)


@dp.callback_query(F.data == "setup_battle")
async def setup_battle(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    text = build_setup_battle(uid)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="reports")]])
    await cb.message.answer(text, reply_markup=with_back(kb))


@dp.callback_query(F.data == "setup_analysis")
async def setup_analysis(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    text = build_setup_analysis(uid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧹 Очистить сетапы", callback_data="reset_setup_analysis")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="reports")],
        ]
    )
    await cb.message.answer(text, reply_markup=with_back(kb))


@dp.callback_query(F.data == "top_trades")
async def top_trades(cb: types.CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    text = build_top_trades(uid)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="reports")]])
    await cb.message.answer(text, reply_markup=with_back(kb))


@dp.callback_query(F.data == "reset_setup_analysis")
async def reset_setup_analysis(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE trades SET signals=NULL, signal_stars=NULL WHERE user_id=?",
            (cb.from_user.id,),
        )
        conn.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="reports")]])
    await cb.message.answer("Аналитика по сетапам сброшена.", reply_markup=with_back(kb))

# ---------- CHARTS ----------
@dp.callback_query(lambda c: c.data == "charts")
async def charts(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_subscription(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    df = pd.read_sql_query(
        "SELECT trade_type, pnl, entry_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
        sqlite3.connect(DB_PATH), params=(uid,)
    )
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
    asyncio.create_task(report_scheduler())
    asyncio.create_task(notification_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
