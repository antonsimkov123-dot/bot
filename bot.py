import os
import logging
import asyncio
import sqlite3
import time
import hmac
import hashlib
from urllib.parse import urlencode
import aiohttp
from datetime import datetime, timedelta
import calendar
from dotenv import load_dotenv
from collections import defaultdict, Counter
from itertools import combinations
load_dotenv()
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.chat_action import ChatActionSender
from io import BytesIO

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
import matplotlib.patheffects as pe

plt.rcParams.update({
    "font.family": "sans-serif",
    "text.color": "white",
    "axes.labelcolor": "white",
    "axes.edgecolor": "white",
})


def make_fig() -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(8, 6), facecolor="#1e1e1e")
    ax.set_facecolor("#1e1e1e")
    ax.grid(color="gray", linestyle="--", alpha=0.5)
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color("white")
    return fig, ax


def add_labels(ax: plt.Axes, fmt: str = "{:.1f}") -> None:
    for rect in ax.patches:
        val = rect.get_height()
        ax.text(
            rect.get_x() + rect.get_width() / 2,
            val,
            fmt.format(val),
            ha="center",
            va="bottom" if val >= 0 else "top",
            color="white",
            fontsize=9,
            fontweight="bold",
        )

# ---------- CONFIG ----------
BOT_TOKEN = "8205192350:AAHUEmqDQK37-5D7dpcTUeMdpA6WpDACMkc"  # поменяй после теста!
DB_PATH = "trades.db"
MULTI_SR_MODE = True  # переключатель множественных уровней поддержки/сопротивления
SR_MAX_ZONES = 7  # максимум зон поддержки/сопротивления на график для каждой стороны

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

TREND_WINDOWS = {
    "D": {"global": 50, "local": 20, "scalp": 10},
    "240": {"global": 50, "local": 20, "scalp": 10},
}

TREND_LEVELS = {"global": "Глобальный", "local": "Локальный", "scalp": "Скальп"}

BAND_PCT = {"60": 0.004, "240": 0.006, "D": 0.015}

# -- Recommendation thresholds
MIN_CLOSE_OUTSIDE_ATR = 0.25  # пробой, если закрытие за зоной на долю ATR
RETEST_WINDOW_BARS = 5        # количество свечей для поиска ретеста
VOL_CONFIRM_PERCENTILE = 60   # перцентиль объёма для подтверждения
MIN_RR = 1.5                  # минимально допустимое соотношение риск/профит

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
            notifications_enabled INTEGER DEFAULT 0,
            notify_type TEXT,
            notify_mode TEXT,
            notify_near_pct REAL DEFAULT 0.3,
            notify_stop_sent INTEGER DEFAULT 0,
            notify_target_sent INTEGER DEFAULT 0,
            notify_stagnation_sent INTEGER DEFAULT 0,
            notify_risk_sent INTEGER DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS auto_updates (
            user_id INTEGER PRIMARY KEY,
            update_time TEXT,
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
            api_secret TEXT,
            account_type TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            is_automation_enabled INTEGER DEFAULT 0,
            subscription TEXT DEFAULT 'none',
            notify_stagnation INTEGER DEFAULT 1,
            notify_targets INTEGER DEFAULT 1,
            notify_risk INTEGER DEFAULT 1,
            habit_report_enabled INTEGER DEFAULT 0,
            habit_report_time TEXT DEFAULT '21:00',
            habit_comment_enabled INTEGER DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            trade_id INTEGER,
            symbol TEXT,
            price REAL,
            direction TEXT,
            mode TEXT,
            near_pct REAL,
            triggered INTEGER DEFAULT 0,
            manual INTEGER DEFAULT 0
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
        if "notify_type" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN notify_type TEXT")
            conn.commit()
        if "notify_mode" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN notify_mode TEXT")
            conn.commit()
        if "notify_near_pct" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN notify_near_pct REAL DEFAULT 0.3")
            conn.commit()
        if "notify_stop_sent" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN notify_stop_sent INTEGER DEFAULT 0")
            conn.commit()
        if "notify_target_sent" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN notify_target_sent INTEGER DEFAULT 0")
            conn.commit()
        if "notify_stagnation_sent" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN notify_stagnation_sent INTEGER DEFAULT 0")
            conn.commit()

        if "notify_risk_sent" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN notify_risk_sent INTEGER DEFAULT 0")
            conn.commit()
        cur.execute("PRAGMA table_info(user_settings)")
        us_cols = {row[1] for row in cur.fetchall()}
        if "subscription" not in us_cols:
            cur.execute(
                "ALTER TABLE user_settings ADD COLUMN subscription TEXT DEFAULT 'none'"
            )
            conn.commit()
        if "notify_stagnation" not in us_cols:
            cur.execute("ALTER TABLE user_settings ADD COLUMN notify_stagnation INTEGER DEFAULT 1")
            conn.commit()
        if "notify_targets" not in us_cols:
            cur.execute("ALTER TABLE user_settings ADD COLUMN notify_targets INTEGER DEFAULT 1")
            conn.commit()
        if "notify_risk" not in us_cols:
            cur.execute("ALTER TABLE user_settings ADD COLUMN notify_risk INTEGER DEFAULT 1")
            conn.commit()
        if "habit_report_enabled" not in us_cols:
            cur.execute("ALTER TABLE user_settings ADD COLUMN habit_report_enabled INTEGER DEFAULT 0")
            conn.commit()
        if "habit_report_time" not in us_cols:
            cur.execute("ALTER TABLE user_settings ADD COLUMN habit_report_time TEXT DEFAULT '21:00'")
            conn.commit()
        if "habit_comment_enabled" not in us_cols:
            cur.execute("ALTER TABLE user_settings ADD COLUMN habit_comment_enabled INTEGER DEFAULT 0")
            conn.commit()

        cur.execute("PRAGMA table_info(price_alerts)")
        pa_cols = {row[1] for row in cur.fetchall()}
        if "user_id" not in pa_cols:
            cur.execute("ALTER TABLE price_alerts ADD COLUMN user_id INTEGER")
            conn.commit()
        if "symbol" not in pa_cols:
            cur.execute("ALTER TABLE price_alerts ADD COLUMN symbol TEXT")
            conn.commit()
        if "manual" not in pa_cols:
            cur.execute("ALTER TABLE price_alerts ADD COLUMN manual INTEGER DEFAULT 0")
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
        cur.execute("PRAGMA table_info(bybit_keys)")
        bk_cols = {row[1] for row in cur.fetchall()}
        if "account_type" not in bk_cols:
            cur.execute("ALTER TABLE bybit_keys ADD COLUMN account_type TEXT")
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
            CREATE TABLE IF NOT EXISTS auto_updates (
                user_id INTEGER PRIMARY KEY,
                update_time TEXT,
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
                api_secret TEXT,
                account_type TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                is_automation_enabled INTEGER DEFAULT 0,
                subscription TEXT DEFAULT 'none'
            )
            """
        )

init_db()
add_missing_columns()

# ---------- BOT ----------
bot = Bot(BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

CHANNEL_USERNAME = "@CryptoLens_MarketMinds"
ADMIN_ID = 800029273


async def is_subscribed(uid: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, uid)
        return member.status in {"member", "administrator", "creator"}
    except Exception:
        return False

def get_subscription(uid: int) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT subscription FROM user_settings WHERE user_id=?", (uid,)
        ).fetchone()
    return row[0] if row and row[0] else "none"


def set_subscription(uid: int, sub: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO user_settings (user_id, subscription) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET subscription=excluded.subscription",
            (uid, sub),
        )
        conn.commit()


async def require_subscription(message: types.Message, uid: int) -> bool:
    if not await is_subscribed(uid) or get_subscription(uid) == "none":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")]]
        )
        await message.answer(
            "❌ Доступно только для подписчиков. Подпишись на канал @CryptoLens_MarketMinds и нажми кнопку «🔄 Проверить подписку».",
            reply_markup=kb,
        )
        return False
    return True


async def require_basic(message: types.Message, uid: int) -> bool:
    if not await require_subscription(message, uid):
        return False
    if get_subscription(uid) not in {"basic", "pro"}:
        await message.answer("🔒 Раздел доступен только с платной подпиской (от 500₽)")
        return False
    return True


async def require_pro(message: types.Message, uid: int) -> bool:
    if not await require_basic(message, uid):
        return False
    if get_subscription(uid) != "pro":
        await message.answer("🔒 Доступно только с PRO-подпиской (от 2500₽)")
        return False
    return True


@dp.callback_query(F.data == "check_sub")
async def recheck_subscription(cb: types.CallbackQuery):
    await cb.answer()
    if await is_subscribed(cb.from_user.id):
        await cb.message.answer("✅ Подписка подтверждена. Теперь можно пользоваться этой функцией.")
    else:
        await require_subscription(cb.message, cb.from_user.id)


def is_automation_enabled(uid: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT is_automation_enabled FROM user_settings WHERE user_id=?",
            (uid,),
        ).fetchone()
    return bool(row[0]) if row else False


def set_automation(uid: int, enabled: bool) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO user_settings (user_id, is_automation_enabled) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET is_automation_enabled=excluded.is_automation_enabled",
            (uid, int(enabled)),
        )
        conn.commit()

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
    entering_leverage = State()
    entering_leverage_manual = State()
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


class SubscriptionState(StatesGroup):
    waiting_user = State()


class AutoReportState(StatesGroup):
    entering_time = State()
    choosing_period = State()


class AutoUpdateState(StatesGroup):
    entering_time = State()

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
    ask = State()
    choose_type = State()
    add_type = State()
    choose_mode = State()
    choose_near = State()
    enter_near = State()
    confirm = State()


class HabitNotifyState(StatesGroup):
    time = State()


class AICoinState(StatesGroup):
    enter_symbol = State()


class PriceAlertState(StatesGroup):
    enter_symbol = State()
    waiting_price = State()
    choose_mode = State()
    choose_sensitivity = State()
    enter_custom = State()

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


def calc_risk(entry: float, stop: float, pct: float, t_type: str, leverage: float = 1.0) -> float:
    if t_type.lower() in {"long", "spot"}:
        base = (entry - stop) / entry
    else:
        base = (stop - entry) / entry
    risk = base * pct * (leverage if leverage else 1)
    return round(risk, 2)


def fmt_price(val: float) -> str:
    return f"{val:.2f}".rstrip("0").rstrip(".")


def fmt_targets(val: str | None) -> str:
    if not val:
        return "-"
    parts = [p.strip() for p in str(val).split(",") if p.strip()]
    return " | ".join(parts) if parts else "-"


def fmt_percent(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val:.1f}%"


def fmt_leverage(val: float | None) -> str:
    if not val or val <= 1:
        return ""
    return f"{int(val)}x" if float(val).is_integer() else f"{val}x"


def display_notify_type(ntype: str | None) -> str:
    mapping = {"target": "Цель", "stop": "Стоп", "both": "Стоп и Цель"}
    return mapping.get(ntype or "", "-")

def display_notify_mode(mode: str | None, pct: float | None) -> list[str]:
    if mode == "touch":
        return ["▪️ Способ: При касании"]
    lines: list[str] = []
    if mode in ("near", "both"):
        lines.append(f"▪️ Способ: Приближение (±{(pct or 0.3):.1f}%)")
    if mode in ("touch", "both"):
        lines.append("▪️ При касании: Да")
    else:
        lines.append("▪️ При касании: Нет")
    return lines


def display_pa_mode(mode: str, pct: float | None) -> str:
    if mode == "touch":
        return "При точном достижении"
    if mode == "both":
        return f"При касании и приближении ±{(pct or 0.3):.1f}%"
    return f"Приближение ±{(pct or 0.3):.1f}%"


async def save_price_alert(msg: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    tid = data.get("pa_trade_id")
    symbol = data.get("pa_symbol")
    aid = data.get("pa_edit_id")
    price = data["pa_price"]
    mode = data["pa_mode"]
    direction = data.get("pa_direction", "both")
    near_pct = data.get("pa_near_pct")
    uid = msg.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        if aid:
            conn.execute(
                "UPDATE price_alerts SET price=?, direction=?, mode=?, near_pct=?, triggered=0 WHERE id=?",
                (price, direction, mode, near_pct, aid),
            )
        else:
            conn.execute(
                "INSERT INTO price_alerts(user_id, trade_id, symbol, price, direction, mode, near_pct, manual) VALUES (?,?,?,?,?,?,?,?)",
                (uid, tid, symbol, price, direction, mode, near_pct, 1 if symbol and not tid else 0),
            )
        conn.commit()
    await msg.answer("✅ Уведомление сохранено.")
    await state.clear()
    if tid:
        await send_notif_config(msg, uid, tid)
    else:
        await show_manual_alerts(uid, msg)


async def ask_notify_mode(cb: types.CallbackQuery, state: FSMContext) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚨 При касании", callback_data="notif_mode_touch")],
            [InlineKeyboardButton(text="⚠️ При приближении", callback_data="notif_mode_near")],
            [
                InlineKeyboardButton(
                    text="🔔 При касании и приближении", callback_data="notif_mode_both"
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_notify")],
        ]
    )
    await cb.message.answer("Когда уведомлять?", reply_markup=with_back(kb))
    await state.set_state(NotifyState.choose_mode)


async def present_notif_summary(msg: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    ntype = data["notify_type"]
    mode = data.get("notify_mode")
    pct = data.get("near_pct", 0.3)
    lines = ["Уведомления будут настроены:", f"▪️ Тип: {display_notify_type(ntype)}"]
    lines.extend(display_notify_mode(mode, pct))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Включить уведомления", callback_data="notif_enable")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_notify")],
        ]
    )
    await msg.answer("\n".join(lines), reply_markup=with_back(kb))
    await state.set_state(NotifyState.confirm)


async def present_auto_calc(msg: types.Message, state: FSMContext, vol: float) -> None:
    data = await state.get_data()
    entry = data["entry"]
    t_type = data["type"]
    pct = data.get("percent") or 0
    dist = entry * vol / 100
    if t_type.lower() in {"long", "spot"}:
        stop = entry - dist
        targets = [entry + 1.5 * dist, entry + 2.5 * dist, entry + 4 * dist]
    else:
        stop = entry + dist
        targets = [entry - 1.5 * dist, entry - 2.5 * dist, entry - 4 * dist]
    risk = calc_risk(entry, stop, pct, t_type, data.get("leverage", 1))
    await state.update_data(stop=stop, targets=targets, vol=vol, risk=risk)
    t1, t2, t3 = [fmt_price(t) for t in targets]
    if is_automation_enabled(msg.from_user.id):
        tid = data["tid"]
        targets_str = ",".join(fmt_price(t) for t in targets)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE trades SET stop_loss=?, targets=?, risk_percent=? WHERE id=?",
                (stop, targets_str, risk, tid),
            )
            conn.commit()
        await state.clear()
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="optimization")]]
        )
        text = (
            f"📍 Авторасчёт завершён:\n\n"
            f"🛑 Стоп: {fmt_price(stop)}\n"
            "🎯 Цели:\n"
            f"— 1: {t1} (1.5R)\n"
            f"— 2: {t2} (2.5R)\n"
            f"— 3: {t3} (4R)\n\n"
            "✅ Автоматизация: стоп и цели сохранены."
        )
        await msg.answer(text, reply_markup=with_back(kb))
        return
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


async def fetch_bybit_positions(
    uid: int,
    api_key: str,
    api_secret: str,
    account_type: str | None = None,
) -> tuple[bool, list | str, str]:
    async def _try(acc_type: str) -> tuple[bool, list | str]:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        params = {"category": "linear", "accountType": acc_type, "settleCoin": "USDT"}
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
                    if resp.status == 401:
                        return False, "401"
                    if resp.status != 200:
                        return False, "http"
                    data = await resp.json()
        except Exception:
            return False, "http"
        if data.get("retCode") != 0:
            return False, data.get("retMsg", "")
        items = [
            {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "leverage": p.get("leverage"),
                "entryPrice": p.get("entryPrice") or p.get("avgPrice"),
                "size": p.get("size"),
                "stopLoss": p.get("stopLoss"),
                "takeProfit": p.get("takeProfit"),
            }
            for p in data.get("result", {}).get("list", [])
            if float(p.get("size", 0)) != 0
        ]
        return True, items

    first = account_type or "CONTRACT"
    ok, res = await _try(first)
    if ok:
        save_account_type(uid, first)
        return True, res, first
    if res == "401":
        return False, "401", first
    second = "UNIFIED" if first == "CONTRACT" else "CONTRACT"
    ok2, res2 = await _try(second)
    if ok2:
        save_account_type(uid, second)
        return True, res2, second
    if res2 == "401":
        return False, "401", second
    return (
        False,
        "❌ Не удалось связаться с Bybit: оба типа аккаунта не поддерживаются",
        first,
    )


async def fetch_bybit_spot_history(api_key: str, api_secret: str) -> tuple[bool, list | str]:
    ts = str(int(time.time() * 1000))
    recv = "5000"
    params = {"category": "spot", "limit": "50"}
    query = urlencode(params)
    sign_payload = ts + api_key + recv + query
    sign = hmac.new(api_secret.encode(), sign_payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
    }
    url = "https://api.bybit.com/v5/order/history-trade"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 401:
                    return False, "401"
                if resp.status != 200:
                    return False, "http"
                data = await resp.json()
    except Exception:
        return False, "http"
    if data.get("retCode") != 0:
        return False, data.get("retMsg", "")
    items = [
        {
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "leverage": 1,
            "avgPrice": o.get("avgPrice") or o.get("execPrice"),
            "size": o.get("execQty"),
            "orderType": o.get("orderType"),
            "execTime": o.get("execTime"),
        }
        for o in data.get("result", {}).get("list", [])
        if o.get("orderType") in {"Market", "Limit"}
        and o.get("side") in {"Buy", "Sell"}
    ]
    return True, items


async def fetch_bybit_balance(
    uid: int, api_key: str, api_secret: str, account_type: str | None = None
) -> tuple[
    bool,
    tuple[float, list[tuple[str, float]], list[tuple[str, float, float]]] | str,
]:
    """Fetch wallet balance and return total USD, non-stablecoin USD values,
    and detailed (coin, amount, usd) tuples."""

    async def _try(
        acc_type: str,
    ) -> tuple[
        bool,
        tuple[float, list[tuple[str, float]], list[tuple[str, float, float]]] | str,
    ]:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        params = {"accountType": acc_type}
        query = urlencode(params)
        sign_payload = ts + api_key + recv + query
        sign = hmac.new(api_secret.encode(), sign_payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": sign,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
        }
        url = "https://api.bybit.com/v5/account/wallet-balance"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 401:
                        return False, "401"
                    if resp.status != 200:
                        return False, "http"
                    data = await resp.json()
        except Exception:
            return False, "http"
        if data.get("retCode") != 0:
            return False, data.get("retMsg", "")
        total = 0.0
        extra: list[tuple[str, float]] = []
        details: list[tuple[str, float, float]] = []
        for acc in data.get("result", {}).get("list", []):
            for coin in acc.get("coin", []):
                usd = float(coin.get("usdValue") or 0)
                amt = float(
                    coin.get("equity")
                    or coin.get("walletBalance")
                    or coin.get("free")
                    or 0
                )
                total += usd
                c = coin.get("coin")
                if c != "USDT" and usd:
                    extra.append((c, usd))
                details.append((c, amt, usd))
        return True, (total, extra, details)

    first = account_type or "CONTRACT"
    ok, res = await _try(first)
    if ok:
        save_account_type(uid, first)
        return True, res
    if res == "401":
        return False, "401"
    second = "UNIFIED" if first == "CONTRACT" else "CONTRACT"
    ok2, res2 = await _try(second)
    if ok2:
        save_account_type(uid, second)
        return True, res2
    if res2 == "401":
        return False, "401"
    return False, "🔴 Не удалось связаться с Bybit: оба типа аккаунта не поддерживаются"


def save_account_type(uid: int, account_type: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE bybit_keys SET account_type=? WHERE user_id=?",
            (account_type, uid),
        )
        conn.commit()


async def get_usd_rub_rate() -> float:
    url = "https://api.exchangerate.host/latest?base=USD&symbols=RUB"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                rate = data.get("rates", {}).get("RUB")
                if rate:
                    return float(rate)
    except Exception:
        pass
    return 93.0


def save_imported_trade(uid: int, pos: dict) -> int:
    t_type = "Long" if pos.get("side") in {"Buy", "Long"} else "Short"
    sym = pos.get("symbol", "")
    sym = _base_from_symbol(sym)
    entry = float(pos.get("entryPrice") or pos.get("avgPrice") or 0)
    size = float(pos.get("size") or 0)
    lev = float(pos.get("leverage") or 0)
    sl_val = pos.get("stopLoss")
    sl = float(sl_val) if sl_val not in (None, "", "0") else None
    tp_val = pos.get("takeProfit")
    if tp_val and tp_val != "0":
        tgt = ",".join(t.strip() for t in str(tp_val).split(",") if t.strip() and float(t))
    else:
        tgt = None
    risk = calc_risk(entry, sl, 100.0, t_type, lev) if sl is not None else None
    entry_date = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT id FROM trades WHERE user_id=? AND symbol=? AND trade_type=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (uid, sym, t_type),
        ).fetchone()
        if row:
            tid = row[0]
            cur.execute(
                "UPDATE trades SET entry_price=?, position_size=?, leverage=?, stop_loss=?, targets=?, percent=?, risk_percent=?, entry_date=? WHERE id=?",
                (entry, size, lev, sl, tgt, 100.0, risk, entry_date, tid),
            )
        else:
            cur.execute(
                "INSERT INTO trades (user_id, trade_type, symbol, entry_price, position_size, leverage, stop_loss, targets, percent, risk_percent, entry_date, comment, signals, signal_stars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    t_type,
                    sym,
                    entry,
                    size,
                    lev,
                    sl,
                    tgt,
                    100.0,
                    risk,
                    entry_date,
                    "Импорт из Bybit",
                    None,
                    None,
                ),
            )
            tid = cur.lastrowid
        conn.commit()
        return tid


def process_spot_history(uid: int, orders: list[dict]) -> None:
    stable = {"USDT", "USDC"}
    orders = sorted(orders, key=lambda o: int(o.get("execTime", 0)))
    for o in orders:
        sym = o.get("symbol", "")
        base = _base_from_symbol(sym)
        if not (sym.endswith("USDT") or sym.endswith("USDC")):
            continue
        if base in stable:
            continue
        side = o.get("side")
        price = float(o.get("avgPrice") or o.get("execPrice") or 0)
        qty = float(o.get("size") or o.get("execQty") or 0)
        ts = o.get("execTime")
        dt = (
            datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
            if ts
            else datetime.now().strftime("%Y-%m-%d")
        )
        if side == "Buy":
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                row = cur.execute(
                    "SELECT id, entry_price, position_size, percent FROM trades WHERE user_id=? AND symbol=? AND exit_price IS NULL AND trade_type='SPOT' AND COALESCE(is_deleted,0)=0 ORDER BY entry_date, id LIMIT 1",
                    (uid, base),
                ).fetchone()
                if row:
                    tid, e_price, size, old_pct = row
                    base_sz = (size or 0) / ((old_pct or 100) / 100) if (old_pct or 0) > 0 else (size or 0)
                    if qty >= base_sz:
                        cur.execute(
                            "UPDATE trades SET entry_price=?, position_size=?, percent=100, entry_date=? WHERE id=?",
                            (price, qty, dt, tid),
                        )
                    else:
                        new_size = (size or 0) + qty
                        new_entry = (
                            (e_price or 0) * (size or 0) + price * qty
                        ) / new_size if (size or 0) > 0 else price
                        add_pct = qty / base_sz * 100 if base_sz else 0
                        new_pct = min((old_pct or 0) + add_pct, 100.0)
                        cur.execute(
                            "UPDATE trades SET entry_price=?, position_size=?, percent=? WHERE id=?",
                            (new_entry, new_size, new_pct, tid),
                        )
                else:
                    cur.execute(
                        "INSERT INTO trades (user_id, trade_type, symbol, entry_price, position_size, leverage, stop_loss, targets, percent, risk_percent, entry_date, comment, signals, signal_stars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            uid,
                            "SPOT",
                            base,
                            price,
                            qty,
                            1,
                            None,
                            None,
                            100.0,
                            None,
                            dt,
                            None,
                            None,
                            None,
                        ),
                    )
                conn.commit()
        elif side == "Sell":
            remaining = qty
            while remaining > 0:
                with sqlite3.connect(DB_PATH) as conn:
                    cur = conn.cursor()
                    row = cur.execute(
                        "SELECT id, entry_price, percent, position_size, trade_type, stop_loss, targets, entry_date, comment, signals, signal_stars FROM trades WHERE user_id=? AND symbol=? AND trade_type='SPOT' AND exit_price IS NULL AND COALESCE(is_deleted,0)=0 ORDER BY entry_date, id LIMIT 1",
                        (uid, base),
                    ).fetchone()
                if not row:
                    break
                (
                    tid,
                    entry_price,
                    percent,
                    pos_size,
                    t_type,
                    sl,
                    tgt,
                    entry_date,
                    comment,
                    signals,
                    sstars,
                ) = row
                close_qty = min(remaining, pos_size or 0)
                if close_qty <= 0:
                    break
                close_pct = percent * close_qty / (pos_size or 1)
                pnl = ((price - entry_price) / entry_price) * (
                    100 if t_type.lower() in {"long", "spot"} else -100
                )
                profit = round(pnl * close_pct / 100, 2)
                remain_size = (pos_size or 0) - close_qty
                close_data = dict(
                    user_id=uid,
                    t_type=t_type,
                    sym=base,
                    entry_price=entry_price,
                    sl=sl,
                    tgt=tgt,
                    entry_date=entry_date,
                    comment=comment,
                    signals=signals,
                    sstars=sstars,
                    exit_price=price,
                    exit_date=dt,
                    pnl=pnl,
                    profit=profit,
                    remaining=percent - close_pct,
                    close_pct=close_pct,
                    risk_close=None,
                    risk_remain=None,
                    trade_id=tid,
                    remain_size=remain_size,
                )
                store_closed_trade(close_data, None)
                remaining -= close_qty


async def get_spot_entry_price(api_key: str, api_secret: str, coin: str) -> float | None:
    """Calculate average entry price for a spot coin based on trade history."""
    resolved = await _resolve_symbol(coin, prefer="spot")
    if not resolved:
        return None
    symbol, category = resolved
    if category != "spot":
        return None
    ts = str(int(time.time() * 1000))
    recv = "5000"
    params = {"category": "spot", "symbol": symbol, "limit": "50"}
    query = urlencode(params)
    sign_payload = ts + api_key + recv + query
    sign = hmac.new(api_secret.encode(), sign_payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
    }
    url = "https://api.bybit.com/v5/order/history-trade"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception:
        return None
    if data.get("retCode") != 0:
        return None
    trades = sorted(
        data.get("result", {}).get("list", []),
        key=lambda o: int(o.get("execTime", 0)),
    )
    qty = 0.0
    cost = 0.0
    for o in trades:
        side = o.get("side")
        price = float(o.get("avgPrice") or o.get("execPrice") or 0)
        q = float(o.get("execQty") or 0)
        if side == "Buy":
            cost += price * q
            qty += q
        elif side == "Sell":
            qty -= q
            cost -= price * q
            if qty < 0:
                qty = 0
                cost = 0
    if qty > 0:
        return cost / qty
    return None


async def sync_spot_balances(
    uid: int,
    api_key: str,
    api_secret: str,
    balances: list[tuple[str, float, float]],
) -> None:
    """Ensure trades table reflects spot balances."""
    stable = {"USDT", "USDC"}
    now = datetime.now().strftime("%Y-%m-%d")
    balance_map = {c: (amt, usd) for c, amt, usd in balances if amt > 0}
    for coin, (amt, usd) in balance_map.items():
        if coin in stable:
            continue
        entry = await get_spot_entry_price(api_key, api_secret, coin)
        if entry is None:
            price = await fetch_price(coin)
            entry = price or 0
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT id, entry_price, position_size, percent, trade_type, stop_loss, targets, entry_date, comment, signals, signal_stars FROM trades WHERE user_id=? AND symbol=? AND trade_type='SPOT' AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
                (uid, coin),
            ).fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO trades (user_id, trade_type, symbol, entry_price, position_size, leverage, stop_loss, targets, percent, risk_percent, entry_date, comment, signals, signal_stars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uid,
                        "SPOT",
                        coin,
                        entry,
                        amt,
                        1,
                        None,
                        None,
                        100.0,
                        None,
                        now,
                        None,
                        None,
                        None,
                    ),
                )
                conn.commit()
            else:
                tid, e_price, old_size, percent, t_type, sl, tgt, entry_date, comment, signals, sstars = row
                if amt < (old_size or 0):
                    closed_qty = (old_size or 0) - amt
                    close_pct = percent * closed_qty / ((old_size or 1))
                    exit_price = await fetch_price(coin)
                    pnl = ((exit_price - e_price) / e_price) * 100 if exit_price else 0
                    profit = round(pnl * close_pct / 100, 2) if exit_price else 0
                    close_data = dict(
                        user_id=uid,
                        t_type=t_type,
                        sym=coin,
                        entry_price=e_price,
                        sl=sl,
                        tgt=tgt,
                        entry_date=entry_date,
                        comment=comment,
                        signals=signals,
                        sstars=sstars,
                        exit_price=exit_price or e_price,
                        exit_date=now,
                        pnl=pnl if exit_price else 0,
                        profit=profit if exit_price else 0,
                        remaining=percent - close_pct,
                        close_pct=close_pct,
                        risk_close=None,
                        risk_remain=None,
                        trade_id=tid,
                        remain_size=amt,
                    )
                    store_closed_trade(close_data, None)
                else:
                    new_entry = entry if entry else e_price
                    base_sz = (old_size or 0) / ((percent or 100) / 100) if (percent or 0) > 0 else (old_size or 0)
                    if amt >= base_sz:
                        new_pct = 100.0
                    else:
                        add_pct = (amt - (old_size or 0)) / base_sz * 100 if base_sz else 0
                        new_pct = min((percent or 0) + add_pct, 100.0)
                    cur.execute(
                        "UPDATE trades SET entry_price=?, position_size=?, percent=? WHERE id=?",
                        (new_entry, amt, new_pct, tid),
                    )
                    conn.commit()
    # close trades absent in balance
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, entry_price, percent, position_size, trade_type, stop_loss, targets, entry_date, comment, signals, signal_stars FROM trades WHERE user_id=? AND trade_type='SPOT' AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    for row in rows:
        tid, sym, e_price, percent, pos_size, t_type, sl, tgt, entry_date, comment, signals, sstars = row
        if sym not in balance_map:
            exit_price = await fetch_price(sym)
            pnl = ((exit_price - e_price) / e_price) * 100 if exit_price else 0
            profit = round(pnl * percent / 100, 2) if exit_price else 0
            close_data = dict(
                user_id=uid,
                t_type=t_type,
                sym=sym,
                entry_price=e_price,
                sl=sl,
                tgt=tgt,
                entry_date=entry_date,
                comment=comment,
                signals=signals,
                sstars=sstars,
                exit_price=exit_price or e_price,
                exit_date=now,
                pnl=pnl if exit_price else 0,
                profit=profit if exit_price else 0,
                remaining=0,
                close_pct=percent,
                risk_close=None,
                risk_remain=None,
                trade_id=tid,
                remain_size=0,
            )
            store_closed_trade(close_data, None)


async def sync_futures_positions(uid: int, positions: list[dict]) -> list[dict]:
    """Sync futures positions with trades table.

    Returns positions not yet stored so user can import them manually."""
    now = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, trade_type, entry_price, position_size, leverage, percent, stop_loss, targets, entry_date, comment, signals, signal_stars FROM trades WHERE user_id=? AND trade_type IN ('Long','Short') AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    trade_map: dict[tuple[str, str], dict] = {}
    for r in rows:
        trade_map[(r[1], r[2])] = {
            "id": r[0],
            "symbol": r[1],
            "trade_type": r[2],
            "entry_price": r[3],
            "position_size": r[4] or 0.0,
            "leverage": r[5] or 1.0,
            "percent": r[6] if r[6] is not None else 100.0,
            "stop_loss": r[7],
            "targets": r[8],
            "entry_date": r[9],
            "comment": r[10],
            "signals": r[11],
            "sstars": r[12],
        }
    seen: set[tuple[str, str]] = set()
    new_positions: list[dict] = []
    for pos in positions:
        sym = _base_from_symbol(pos.get("symbol", ""))
        side = "Long" if pos.get("side") in {"Buy", "Long"} else "Short"
        size = float(pos.get("size") or 0)
        entry = float(pos.get("entryPrice") or pos.get("avgPrice") or 0)
        lev = float(pos.get("leverage") or 0)
        key = (sym, side)
        if key in trade_map:
            tr = trade_map[key]
            tid = tr["id"]
            old_size = tr["position_size"]
            old_pct = tr["percent"]
            e_price = tr["entry_price"]
            sl_val = pos.get("stopLoss")
            sl = float(sl_val) if sl_val not in (None, "", "0") else None
            tp_val = pos.get("takeProfit")
            if tp_val and tp_val != "0":
                tgt = ",".join(t.strip() for t in str(tp_val).split(",") if t.strip() and float(t))
            else:
                tgt = None
            base_sz = old_size / ((old_pct or 100) / 100) if (old_pct or 0) > 0 else old_size
            if size < old_size:
                new_pct = min(size / base_sz * 100, 100.0) if base_sz else 0.0
                close_pct = max(old_pct - new_pct, 0)
                exit_price = await fetch_price(sym)
                if side == "Long":
                    pnl = ((exit_price - e_price) / e_price * 100) if exit_price else 0
                else:
                    pnl = ((e_price - exit_price) / e_price * 100) if exit_price else 0
                profit = round(pnl * close_pct / 100, 2) if exit_price else 0
                risk_close = (
                    calc_risk(e_price, tr["stop_loss"], close_pct, side, tr.get("leverage", 1))
                    if tr["stop_loss"] is not None
                    else None
                )
                risk_remain = (
                    calc_risk(entry, sl, new_pct, side, lev) if sl is not None else None
                )
                close_data = dict(
                    user_id=uid,
                    t_type=side,
                    sym=sym,
                    entry_price=e_price,
                    sl=tr["stop_loss"],
                    tgt=tr["targets"],
                    entry_date=tr["entry_date"],
                    comment=tr["comment"],
                    signals=tr["signals"],
                    sstars=tr["sstars"],
                    exit_price=exit_price or e_price,
                    exit_date=now,
                    pnl=pnl if exit_price else 0,
                    profit=profit if exit_price else 0,
                    remaining=new_pct,
                    close_pct=close_pct,
                    risk_close=risk_close,
                    risk_remain=risk_remain,
                    trade_id=tid,
                    remain_size=size,
                    leverage=tr.get("leverage", lev),
                )
                store_closed_trade(close_data, None)
                if new_pct <= 0:
                    seen.add(key)
                    continue
            else:
                if size >= base_sz:
                    new_pct = 100.0
                else:
                    new_pct = min(size / base_sz * 100, 100.0)
                risk_remain = (
                    calc_risk(entry, sl, new_pct, side, lev) if sl is not None else None
                )
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE trades SET entry_price=?, position_size=?, leverage=?, stop_loss=?, targets=?, percent=?, risk_percent=? WHERE id=?",
                    (entry, size, lev, sl, tgt, new_pct, risk_remain, tid),
                )
                conn.commit()
            seen.add(key)
        else:
            new_positions.append(pos)
    for key, tr in trade_map.items():
        if key in seen:
            continue
        tid = tr["id"]
        sym = tr["symbol"]
        side = tr["trade_type"]
        e_price = tr["entry_price"]
        pct = tr["percent"]
        exit_price = await fetch_price(sym)
        if side == "Long":
            pnl = ((exit_price - e_price) / e_price * 100) if exit_price else 0
        else:
            pnl = ((e_price - exit_price) / e_price * 100) if exit_price else 0
        profit = round(pnl * pct / 100, 2) if exit_price else 0
        close_data = dict(
            user_id=uid,
            t_type=side,
            sym=sym,
            entry_price=e_price,
            sl=tr["stop_loss"],
            tgt=tr["targets"],
            entry_date=tr["entry_date"],
            comment=tr["comment"],
            signals=tr["signals"],
            sstars=tr["sstars"],
            exit_price=exit_price or e_price,
            exit_date=now,
            pnl=pnl if exit_price else 0,
            profit=profit if exit_price else 0,
            remaining=0,
            close_pct=pct,
            risk_close=None,
            risk_remain=None,
            trade_id=tid,
            remain_size=0,
            leverage=tr.get("leverage", 1),
        )
        store_closed_trade(close_data, None)
    return new_positions


_symbol_cache: dict[str, tuple[str, str]] = {}


def _base_from_symbol(sym: str) -> str:
    if sym.endswith("USDT") or sym.endswith("USDC"):
        sym = sym[:-4]
    return sym.lstrip("0123456789")


async def _resolve_symbol(base: str, prefer: str | None = None) -> tuple[str, str] | None:
    base = base.upper()
    if base in _symbol_cache:
        return _symbol_cache[base]
    categories = ["linear", "spot"]
    if prefer and prefer in categories:
        categories.remove(prefer)
        categories.insert(0, prefer)
    url = "https://api.bybit.com/v5/market/instruments-info"
    for category in categories:
        params = {"category": category, "baseCoin": base}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    data = await resp.json()
        except Exception:
            continue
        items = data.get("result", {}).get("list") or []
        for item in items:
            if item.get("quoteCoin") == "USDT":
                symbol = item.get("symbol")
                if symbol:
                    _symbol_cache[base] = (symbol, category)
                    return symbol, category
    return None


async def fetch_price(symbol: str) -> float | None:
    resolved = await _resolve_symbol(symbol)
    if not resolved:
        return None
    real, category = resolved
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": category, "symbol": real}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
    except Exception:
        return None
    try:
        price = float(data.get("result", {}).get("list", [{}])[0].get("lastPrice"))
        if price:
            return price
    except Exception:
        return None
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
    "🔥 Очень важные (⭐️⭐️⭐️⭐️⭐️ и ⭐️⭐️⭐️⭐️):\n"
    "• Закреп 2–3 свечей — ⭐️⭐️⭐️⭐️⭐️⭐️\n"
    "• Дивергенция RSI или MACD на дневке — ⭐️⭐️⭐️⭐️⭐️⭐️\n"
    "• Поглощение на дневке — ⭐️⭐️⭐️⭐️⭐️\n"
    "• 0.618 FIBO (пробой/отработка) — ⭐️⭐️⭐️⭐️⭐️\n"
    "• Пробой канала или трендовой — ⭐️⭐️⭐️⭐️⭐️\n"
    "• Ретест пробитого уровня на объёмах — ⭐️⭐️⭐️⭐️⭐️\n\n"
    "🟡 Средние (⭐️⭐️⭐️ и ⭐️⭐️):\n"
    "• MACD пересекает сигнальную / 0 — ⭐️⭐️⭐️\n"
    "• Рост объёмов — ⭐️⭐️⭐️\n"
    "• Поддержка от мувингов (50/200) — ⭐️⭐️\n"
    "• Боллинджер: выход за границу — ⭐️⭐️\n"
    "• Формация ГиП / инверсная — ⭐️⭐️\n\n"
    "⚪️ Слабые (⭐️):\n"
    "• Сигналы только на 1H — ⭐️\n"
    "• Мелкая дивергенция RSI на 1H — ⭐️\n"
    "• Стагнация объёмов — ⭐️\n"
    "• Локальные уровни без объёма — ⭐️"
    "\nШкала силы: ≤4 слабая • 5–7 умеренная • 8–11 сильная • 12+ очень сильная"
)

# ---------- MISTAKE REASONS ----------
MISTAKE_OPTIONS = [
    ("❌ Слабые сигналы", "Слабые сигналы"),
    ("⏱ Не дождался ретеста", "Не дождался ретеста"),
    ("🤯 Эмоциональный вход", "Эмоциональный вход"),
    ("🔁 Перезаход", "Перезаход"),
    ("📉 Против тренда", "Против тренда"),
    ("📊 Игнор объёма", "Игнор объёма"),
    ("📈 Вход на хаях", "Вход на хаях"),
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
        sl_str = fmt_price(float(sl)) if sl is not None else "-"
        tgt_str = fmt_targets(tgt)
        pct_str = fmt_percent(pct)
        if t_type and t_type.upper() == "SPOT":
            lines.append(f"{sym} SPOT | Вход {fmt_price(entry)} {pct_str}")
        else:
            lines.append(f"{sym} {t_type.upper()} | Вход {fmt_price(entry)} Стоп {sl_str} Цели {tgt_str} {pct_str}")
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
            "SELECT id, trade_type, symbol, entry_price, stop_loss, targets, entry_date, notify_type, notify_mode, notify_near_pct, notify_stop_sent, notify_target_sent, notify_stagnation_sent, risk_percent, notify_risk_sent FROM trades WHERE user_id=? AND exit_price IS NULL AND notifications_enabled=1 AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
        prefs = conn.execute(
            "SELECT notify_stagnation, notify_targets, notify_risk FROM user_settings WHERE user_id=?",
            (uid,),
        ).fetchone()
    ns, nt, nr = (prefs if prefs else (1, 1, 1))
    for tid, t_type, sym, entry, sl, tgt_str, entry_date, ntype, nmode, npct, stop_sent, target_sent, stag_sent, risk, risk_sent in rows:
        price = await fetch_price(sym)
        if price is None:
            continue
        long_like = t_type.lower() in {"long", "spot"}
        updated = False
        if nt and ntype in ("stop", "both") and sl is not None and not stop_sent:
            pct = npct or 0.3
            if nmode in ("touch", "both") and ((long_like and price <= sl) or (not long_like and price >= sl)):
                try:
                    await bot.send_message(uid, f"{sym} {t_type}: 🔴 Стоп достигнут")
                except Exception:
                    pass
                stop_sent = 1
                updated = True
            elif nmode in ("near", "both") and abs(price - sl) / sl * 100 <= pct:
                try:
                    await bot.send_message(uid, f"{sym} {t_type}: ⚠️ Цена близко к стопу")
                except Exception:
                    pass
                stop_sent = 1
                updated = True
        if nt and ntype in ("target", "both") and not target_sent:
            targets = [float(t) for t in (tgt_str or "").split(",") if t]
            pct = npct or 0.3
            for t in targets:
                if nmode in ("touch", "both") and ((long_like and price >= t) or (not long_like and price <= t)):
                    try:
                        await bot.send_message(uid, f"{sym} {t_type}: 🎯 Цель достигнута")
                    except Exception:
                        pass
                    target_sent = 1
                    updated = True
                    break
                elif nmode in ("near", "both") and abs(price - t) / t * 100 <= pct:
                    try:
                        await bot.send_message(uid, f"{sym} {t_type}: ⚠️ Цена близко к цели")
                    except Exception:
                        pass
                    target_sent = 1
                    updated = True
                    break
        entry_dt = datetime.fromisoformat(entry_date)
        if ns and (
            now - entry_dt >= timedelta(hours=48)
            and abs(price - entry) / entry * 100 < 1
            and not stag_sent
        ):
            try:
                await bot.send_message(uid, f"{sym} {t_type}: 💤 Сделка в стагнации — подумай о действиях")
            except Exception:
                pass
            stag_sent = 1
            updated = True
        if nr and risk is not None and risk > 10 and not risk_sent:
            try:
                await bot.send_message(uid, f"{sym} {t_type}: ⚠️ Риск {risk:.1f}% превышает допустимый")
            except Exception:
                pass
            risk_sent = 1
            updated = True
        if updated:
            disable = 0
            if ntype == "stop" and stop_sent:
                disable = 1
            elif ntype == "target" and target_sent:
                disable = 1
            elif ntype == "both" and stop_sent and target_sent:
                disable = 1
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE trades SET notify_stop_sent=?, notify_target_sent=?, notify_stagnation_sent=?, notify_risk_sent=?, notifications_enabled=? WHERE id=?",
                    (stop_sent, target_sent, stag_sent, risk_sent, 0 if disable else 1, tid),
                )
                conn.commit()

    with sqlite3.connect(DB_PATH) as conn:
        pa_rows = conn.execute(
            """
            SELECT pa.id, t.symbol, t.trade_type, pa.price, pa.mode, pa.near_pct
            FROM price_alerts pa
            JOIN trades t ON pa.trade_id=t.id
            WHERE t.user_id=? AND t.exit_price IS NULL AND pa.triggered=0 AND COALESCE(t.is_deleted,0)=0
            """,
            (uid,),
        ).fetchall()
    for aid, sym, t_type, target, mode, npct in pa_rows:
        price = await fetch_price(sym)
        if price is None:
            continue
        triggered = False
        pct = abs(price - target) / target * 100
        touched = pct <= 0.02
        if touched and mode in ("touch", "both"):
            triggered = True
        elif mode in ("near", "both") and pct <= (npct or 0.3):
            triggered = True
        if triggered:
            text = f"🔔 {sym} достиг {target}" if touched else f"🔔 {sym} приблизился к {target}"
            text += f"\nТип: {display_pa_mode(mode, npct)}"
            try:
                await bot.send_message(uid, text)
            except Exception:
                pass
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM price_alerts WHERE id=?", (aid,))
                conn.commit()

    with sqlite3.connect(DB_PATH) as conn:
        man_rows = conn.execute(
            "SELECT id, symbol, price, mode, near_pct FROM price_alerts WHERE user_id=? AND manual=1 AND triggered=0",
            (uid,),
        ).fetchall()
    for aid, sym, target, mode, npct in man_rows:
        price = await fetch_price(sym)
        if price is None:
            continue
        pct = abs(price - target) / target * 100
        touched = pct <= 0.02
        triggered = False
        if touched and mode in ("touch", "both"):
            triggered = True
        elif mode in ("near", "both") and pct <= (npct or 0.3):
            triggered = True
        if triggered:
            text = f"🔔 {sym} достиг {target}" if touched else f"🔔 {sym} приблизился к {target}"
            text += f"\nТип: {display_pa_mode(mode, npct)}"
            try:
                await bot.send_message(uid, text)
            except Exception:
                pass
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM price_alerts WHERE id=?", (aid,))
                conn.commit()


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
            """
            SELECT t.id, t.symbol, t.trade_type, t.leverage, t.notifications_enabled,
                   (SELECT COUNT(*) FROM price_alerts pa WHERE pa.trade_id=t.id) AS pa_cnt
            FROM trades t
            WHERE user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0
            """,
            (uid,),
        ).fetchall()
        manual_cnt = conn.execute(
            "SELECT COUNT(*) FROM price_alerts WHERE user_id=? AND manual=1 AND triggered=0",
            (uid,),
        ).fetchone()[0]
        auto_row = conn.execute(
            "SELECT update_time, period_days FROM auto_updates WHERE user_id=?",
            (uid,),
        ).fetchone()
        prefs = conn.execute(
            "SELECT notify_stagnation FROM user_settings WHERE user_id=?",
            (uid,),
        ).fetchone()
    ns = prefs[0] if prefs else 1
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=("💤 Стагнация" if ns else "❌ Стагнация"),
                callback_data="notif_pref_stag",
            )
        ]
    ]
    for tid, sym, t_type, lev, enabled, pa_cnt in rows:
        lev_str = fmt_leverage(lev)
        text = f"{sym} {t_type}" + (f" {lev_str}" if lev_str else "")
        if enabled or pa_cnt:
            text += " 🔔"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"notif_cfg_{tid}")])
    buttons.append([
        InlineKeyboardButton(
            text=(
                "🔔 Вне-сделочные уведомления" + (f" ({manual_cnt})" if manual_cnt else "")
            ),
            callback_data="pa_manual_list",
        )
    ])
    auto_text = "⏱ Автообновление"
    if auto_row:
        mode = "ежедневно" if auto_row[1] == 1 else "еженедельно"
        auto_text += f" ({mode} {auto_row[0]})"
    buttons.append([InlineKeyboardButton(text=auto_text, callback_data="auto_sync")])
    if rows:
        buttons.append([InlineKeyboardButton(text="🔕 Выключить все", callback_data="notif_disable_all")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    head = "🔔 Настройка уведомлений по сделкам:" if rows else "У тебя нет активных сделок."
    await message.answer(head, reply_markup=kb)


@dp.callback_query(F.data == "notif_pref_stag")
async def notif_pref_stag(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (uid,))
        val = conn.execute(
            "SELECT notify_stagnation FROM user_settings WHERE user_id=?",
            (uid,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE user_settings SET notify_stagnation=? WHERE user_id=?",
            (0 if val else 1, uid),
        )
        conn.commit()
    await show_notifications_menu(uid, cb.message)


async def show_manual_alerts(uid: int, message: types.Message) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, price, mode, near_pct FROM price_alerts WHERE user_id=? AND manual=1 AND triggered=0",
            (uid,),
        ).fetchall()
    lines = ["🔔 Вне-сделочные уведомления:"]
    buttons: list[list[InlineKeyboardButton]] = []
    for i, (aid, sym, price, mode, npct) in enumerate(rows, 1):
        mode_txt = display_pa_mode(mode, npct)
        lines.append(f"{i}. {sym} {price} — {mode_txt}")
        buttons.append([
            InlineKeyboardButton(text=f"✏ {price}", callback_data=f"pa_edit_{aid}"),
            InlineKeyboardButton(text="🗑", callback_data=f"pa_del_{aid}"),
        ])
    if rows:
        buttons.append([InlineKeyboardButton(text="🔕 Отключить все", callback_data="pa_manual_disable_all")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить уведомление", callback_data="pa_manual_add")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="opt_notify")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    if not rows:
        lines.append("У тебя нет вне-сделочных уведомлений.")
    await message.answer("\n".join(lines), reply_markup=kb)


@dp.callback_query(F.data == "pa_manual_list")
async def pa_manual_list_cb(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    await show_manual_alerts(cb.from_user.id, cb.message)


@dp.callback_query(F.data == "auto_sync")
async def auto_sync_cb(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🕐 Ежедневно", callback_data="aus_daily")],
            [InlineKeyboardButton(text="📅 Еженедельно", callback_data="aus_weekly")],
            [InlineKeyboardButton(text="❌ Отключить автообновление", callback_data="aus_off")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_notify")],
        ]
    )
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT update_time, period_days FROM auto_updates WHERE user_id=?",
            (uid,),
        ).fetchone()
    if row:
        mode = "ежедневно" if row[1] == 1 else "еженедельно"
        status = f"✅ Автообновление включено: {mode} в {row[0]}"
    else:
        status = "❌ Автообновление отключено"
    await cb.message.answer(status, reply_markup=with_back(kb))


@dp.callback_query(lambda c: c.data in {"aus_daily", "aus_weekly"})
async def auto_sync_choose_time(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    period = 1 if cb.data == "aus_daily" else 7
    await state.update_data(auto_period=period)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="auto_sync")]]
    )
    await cb.message.answer("Введи время в формате HH:MM", reply_markup=with_back(kb))
    await state.set_state(AutoUpdateState.entering_time)


@dp.message(AutoUpdateState.entering_time)
async def auto_sync_set_time(msg: types.Message, state: FSMContext):
    if not await require_basic(msg, msg.from_user.id):
        return
    time_str = msg.text.strip()
    if not is_time(time_str):
        await msg.answer("Неверный формат времени. Введи HH:MM")
        return
    data = await state.get_data()
    period = data.get("auto_period", 1)
    uid = msg.from_user.id
    t = datetime.strptime(time_str, "%H:%M").time()
    now = datetime.now()
    next_run = datetime.combine(now.date(), t)
    if next_run <= now:
        next_run += timedelta(days=period)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO auto_updates (user_id, update_time, period_days, next_run) VALUES (?,?,?,?)",
            (uid, time_str, period, next_run.isoformat()),
        )
        conn.commit()
    await state.clear()
    mode = "ежедневно" if period == 1 else "еженедельно"
    await msg.answer(f"✅ Автообновление включено: {mode} в {time_str}")
    await show_notifications_menu(uid, msg)


@dp.callback_query(F.data == "aus_off")
async def auto_sync_off(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM auto_updates WHERE user_id=?", (uid,))
        conn.commit()
    await state.clear()
    await cb.message.answer("❌ Автообновление отключено")
    await show_notifications_menu(uid, cb.message)


async def run_auto_update(uid: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT api_key, api_secret, account_type FROM bybit_keys WHERE user_id=?",
            (uid,),
        ).fetchone()
    if not row or not row[0] or not row[1]:
        return False
    api_key, api_secret, acc_type = row
    ok_pos, positions, _ = await fetch_bybit_positions(uid, api_key, api_secret, acc_type)
    ok_spot, spot_orders = await fetch_bybit_spot_history(api_key, api_secret)
    ok_bal, balinfo = await fetch_bybit_balance(uid, api_key, api_secret, acc_type)
    if ok_bal:
        _, _, bal_details = balinfo
        await sync_spot_balances(uid, api_key, api_secret, bal_details)
    if ok_spot and spot_orders:
        process_spot_history(uid, spot_orders)
    if ok_pos:
        await sync_futures_positions(uid, positions)
    return ok_pos or ok_spot or ok_bal


async def auto_update_scheduler():
    while True:
        now = datetime.now()
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, period_days, next_run FROM auto_updates WHERE next_run<=?",
                (now.isoformat(),),
            ).fetchall()
        for uid, period, next_run in rows:
            success = await run_auto_update(uid)
            next_time = datetime.fromisoformat(next_run) + timedelta(days=period)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE auto_updates SET next_run=? WHERE user_id=?",
                    (next_time.isoformat(), uid),
                )
                conn.commit()
            if success:
                try:
                    await bot.send_message(uid, "⏱ Автообновление\n🔁 Ваши новые сделки были автоматом подгружены")
                except Exception:
                    pass
        await asyncio.sleep(60)


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
            uids = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT user_id FROM trades WHERE exit_price IS NULL AND notifications_enabled=1 AND COALESCE(is_deleted,0)=0"
                ).fetchall()
            }
            uids.update(
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT t.user_id FROM price_alerts pa JOIN trades t ON pa.trade_id=t.id WHERE pa.triggered=0 AND t.exit_price IS NULL AND COALESCE(t.is_deleted,0)=0"
                ).fetchall()
            )
            uids.update(
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT user_id FROM price_alerts WHERE manual=1 AND triggered=0"
                ).fetchall()
            )
        for uid in uids:
            await process_notifications(uid)
        await asyncio.sleep(60)


async def habit_scheduler():
    while True:
        now = datetime.now()
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, habit_report_time FROM user_settings WHERE habit_report_enabled=1",
            ).fetchall()
        for uid, t in rows:
            try:
                h, m = map(int, (t or "21:00").split(":"))
            except Exception:
                h, m = 21, 0
            if now.hour == h and now.minute == m:
                if get_subscription(uid) not in {"basic", "pro"}:
                    continue
                text = build_habits_report(uid)
                try:
                    await bot.send_message(uid, text)
                except Exception:
                    pass
        await asyncio.sleep(60)

def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    opt_text = (
        "🔧 Оптимизация 🟢" if is_automation_enabled(uid) else "🔧 Оптимизация 🔴"
    )
    rows = [
        [InlineKeyboardButton(text="📈 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🏆 Рейтинг трейдеров", callback_data="rating")],
        [
            InlineKeyboardButton(text="📚 Codex", callback_data="codex"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        ],
        [
            InlineKeyboardButton(text="📦 Сделки", callback_data="trades_menu"),
            InlineKeyboardButton(text="📊 Отчёты", callback_data="reports"),
        ],
        [
            InlineKeyboardButton(text="📅 Напоминания", callback_data="reminders"),
            InlineKeyboardButton(text="🧹 Очистить всё", callback_data="clear_all"),
        ],
    ]
    if uid == ADMIN_ID:
        rows.append([InlineKeyboardButton(text="🛂 Управление подпиской", callback_data="sub_manage")])
    rows.append([InlineKeyboardButton(text=opt_text, callback_data="optimization")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def optimization_menu_kb(uid: int) -> InlineKeyboardMarkup:
    auto_text = (
        "🟢⚙️ Автоматизация [вкл]" if is_automation_enabled(uid) else "🔴⚙️ Автоматизация [выкл]"
    )
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
            [InlineKeyboardButton(text="🤖 Автотрейдинг по стратегии", callback_data="opt_autotrade")],
            [InlineKeyboardButton(text=auto_text, callback_data="opt_toggle")],
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
        [InlineKeyboardButton(text=f"{name} — {'⭐️'*stars}", callback_data=f"sig_{idx}")]
        for idx, (name, stars) in enumerate(SIGNAL_OPTIONS)
    ]
    buttons.append([InlineKeyboardButton(text="🛑 Завершить выбор", callback_data="signals_done")])
    return with_back(InlineKeyboardMarkup(inline_keyboard=buttons))


def leverage_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="1x", callback_data="lev_1"), InlineKeyboardButton(text="2x", callback_data="lev_2")],
        [InlineKeyboardButton(text="3x", callback_data="lev_3"), InlineKeyboardButton(text="5x", callback_data="lev_5")],
        [InlineKeyboardButton(text="10x", callback_data="lev_10"), InlineKeyboardButton(text="20x", callback_data="lev_20")],
        [InlineKeyboardButton(text="50x", callback_data="lev_50"), InlineKeyboardButton(text="Ввести вручную", callback_data="lev_manual")],
    ]
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
            "INSERT INTO trades (user_id, trade_type, symbol, entry_price, stop_loss, targets, percent, risk_percent, entry_date, exit_price, exit_date, pnl, profit_percent, comment, signals, signal_stars, mistake_reason, leverage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                data.get("leverage"),
            ),
        )
        if data["remaining"] <= 0:
            cur.execute("DELETE FROM trades WHERE id=?", (data["trade_id"],))
        else:
            if "remain_size" in data:
                cur.execute(
                    "UPDATE trades SET percent=?, risk_percent=?, position_size=? WHERE id=?",
                    (data["remaining"], data["risk_remain"], data["remain_size"], data["trade_id"]),
                )
            else:
                cur.execute(
                    "UPDATE trades SET percent=?, risk_percent=? WHERE id=?",
                    (data["remaining"], data["risk_remain"], data["trade_id"]),
                )
        conn.commit()


def format_trade(data: dict) -> str:
    pct_str = fmt_percent(data.get("percent"))
    text = (
        f"Тип: {data['trade_type'].upper()}\n"
        f"Тикер: {data['symbol']}\n"
        f"Вход: {fmt_price(float(data['entry_price']))}\n"
        f"% от депо: {pct_str}\n"
        f"Дата: {data['entry_date']}"
    )
    ttype = (data.get('trade_type') or '').upper()
    if ttype != 'SPOT':
        sl_val = data.get('stop_loss')
        sl = fmt_price(float(sl_val)) if sl_val is not None else '-'
        tgt = fmt_targets(data.get('targets'))
        risk = data.get('risk')
        risk_str = fmt_percent(risk)
        lev_str = fmt_leverage(data.get('leverage'))
        text = (
            f"Тип: {ttype}\n"
            f"Тикер: {data['symbol']}\n"
            f"Вход: {fmt_price(float(data['entry_price']))}\n"
            f"Стоп: {sl}\n"
            f"Цели: {tgt}\n"
            f"% от депо: {pct_str}\n"
            f"Плечо: {lev_str or '-'}\n"
            f"Риск: {risk_str}\n"
            f"Дата: {data['entry_date']}"
        )
    if data.get('comment'):
        text += f"\nКомментарий: {data['comment']}"
    sigs = data.get('signals')
    if isinstance(sigs, str):
        sigs = [s for s in sigs.split(';') if s]
    sigs = sigs or []
    lines = [f"• {s} — {'⭐️'*SIGNAL_STARS.get(s, 0)}" for s in sigs] or ["—"]
    total, strong, medium, weak = signal_stats(sigs)
    lines.append(f"Всего звёзд: {total}⭐️")
    lines.append(f"Сильные: {strong}, Средние: {medium}, Слабые: {weak}")
    lines.append(f"Сила сделки: {strength_label(total)}")
    lines.append("Шкала: ≤4 Слабая, 5–7 Умеренная, 8–11 Сильная, 12+ Очень сильная")
    text += "\nСигналы:\n" + "\n".join(lines)
    return text

async def go_home(user_id: int, state: FSMContext):
    await state.clear()
    await bot.send_message(user_id, "🏠 Главное меню:", reply_markup=main_menu_kb(user_id))

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


async def build_profile_text(uid: int, include_balance: bool = False) -> str:
    df = pd.read_sql_query(
        "SELECT symbol, pnl, signals, entry_date, exit_date FROM trades WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
        sqlite3.connect(DB_PATH),
        params=(uid,),
    )
    if df.empty:
        base = "📈 Профиль трейдера:\n"
        if include_balance:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT api_key, api_secret, account_type FROM bybit_keys WHERE user_id=?",
                    (uid,),
                ).fetchone()
            if row:
                ok, balinfo = await fetch_bybit_balance(uid, row[0], row[1], row[2])
                if ok:
                    total, coins, _ = balinfo
                    rate = await get_usd_rub_rate()
                    bal_rub = total * rate
                    rub = f"₽{bal_rub:,.0f}".replace(",", " ")
                    usd = f"${total:.2f}"
                    detail = (
                        " (" + " + ".join(f"{c} ${v:.2f}" for c, v in coins) + ")"
                        if coins
                        else ""
                    )
                    base += f"💰 Баланс: {usd} / {rub}{detail}\n"
        return base + "Нет завершённых сделок."
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
    parts = ["📈 Профиль трейдера:\n"]
    if include_balance:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT api_key, api_secret, account_type FROM bybit_keys WHERE user_id=?",
                (uid,),
            ).fetchone()
        if row:
            ok, balinfo = await fetch_bybit_balance(uid, row[0], row[1], row[2])
            if ok:
                total, coins, _ = balinfo
                rate = await get_usd_rub_rate()
                bal_rub = total * rate
                detail = (
                    " (" + " + ".join(f"{c} ${v:.2f}" for c, v in coins) + ")"
                    if coins
                    else ""
                )
                parts.append(
                    f"💰 Баланс: ${total:.2f} / ₽{bal_rub:,.0f}".replace(",", " ")
                    + detail
                    + "\n"
                )
    parts.extend(
        [
            f"🧮 Средняя прибыль: {avg_profit:+.2f}%\n",
            f"📉 Средний убыток: {avg_loss:+.2f}%\n",
            f"✅ Винрейт: {winrate:.1f}%\n",
            f"⏳ Средняя длительность сделки: {avg_duration:.1f} дн.\n",
            f"🔢 Количество сделок: {total}\n",
            f"🧠 Самый частый сетап: {top_signal}\n",
            f"💎 Самый прибыльный коин: {best_coin}\n",
            f"🏅 Ранг: {rank}",
        ]
    )
    return "".join(parts)


@dp.callback_query(F.data == "profile")
async def show_profile(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    text = await build_profile_text(cb.from_user.id, include_balance=True)
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


    # removed build_trader_details; profile rendering reused for rating detail


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
    text = await build_profile_text(uid, include_balance=False)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="rating")]]
    )
    await cb.message.answer(text, reply_markup=with_back(kb))


def dev_placeholder_kb(back_cb: str) -> InlineKeyboardMarkup:
    return with_back(
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)]]
        )
    )


async def send_dev_placeholder(message: types.Message, back_cb: str) -> None:
    await message.answer(
        "🚧 Функция в разработке.", reply_markup=dev_placeholder_kb(back_cb)
    )


@dp.callback_query(F.data == "codex")
async def show_codex(cb: types.CallbackQuery):
    await cb.answer()
    await send_dev_placeholder(cb.message, "main_menu")


@dp.callback_query(F.data == "help")
async def show_help(cb: types.CallbackQuery):
    await cb.answer()
    await send_dev_placeholder(cb.message, "main_menu")


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
        "SELECT id, symbol, trade_type, entry_price, stop_loss, targets, percent, entry_date, comment, risk_percent, leverage "
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
        tid, sym, ttype, entry, sl, tgt, pct, date, comm, risk, lev = r
        sl = fmt_price(float(sl)) if sl is not None else "-"
        tgt = fmt_targets(tgt)
        pct_str = fmt_percent(pct)
        risk_str = fmt_percent(risk)
        if ttype and ttype.upper() == "SPOT":
            caption = f"{sym} SPOT | Вход {fmt_price(entry)} {pct_str}"
            if comm:
                caption += f"\n💬 {comm}"
        else:
            lev_str = fmt_leverage(lev)
            caption = (
                f"{sym} {ttype.upper()} {lev_str} | Вход {fmt_price(entry)}  Стоп {sl}  Цели {tgt}  {pct_str}"
                f" (риск {risk_str}) ({date})"
            )
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
            "SELECT trade_type, symbol, entry_price, stop_loss, targets, percent, risk_percent, entry_date, comment, signals, leverage "
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
        "leverage": row[10],
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
            "SELECT symbol, trade_type, entry_price, exit_price, pnl, exit_date, comment, risk_percent, leverage FROM trades "
            "WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="trades_menu")]])
        await cb.message.answer("История сделок пуста.", reply_markup=with_back(kb))
        return
    lines = []
    for sym, t_type, entry, exit_price, pnl, exit_date, comm, risk, lev in rows:
        entry_str = fmt_price(float(entry))
        exit_str = fmt_price(float(exit_price)) if exit_price is not None else "-"
        if t_type and t_type.upper() == "SPOT":
            line = f"{sym} SPOT | {entry_str} → {exit_str} | {pnl:+.2f}% | {exit_date}"
        else:
            risk_str = fmt_percent(risk)
            lev_str = fmt_leverage(lev)
            line = f"{sym} {t_type.upper()} {lev_str} | {entry_str} → {exit_str} | {pnl:+.2f}% | {exit_date} | Риск {risk_str}"
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
    choosing_leverage: State = State()
    entering_leverage_manual: State = State()

async def open_edit_trade(cb: types.CallbackQuery, tid: int, state: FSMContext):
    await state.update_data(tid=tid)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT trade_type, symbol, entry_price, stop_loss, targets, percent, risk_percent, entry_date, comment, signals, leverage "
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
            "leverage": row[10],
        }
        text = "<b>Сводка сделки</b>\n\n" + format_trade(data)
        kb_sum = with_back(
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="active")]])
        )
        await cb.message.answer(text, reply_markup=kb_sum)

    # warn about missing fields
    miss = []
    if not row[3]:
        miss.append(("стоп", "field_sl"))
    if not row[4]:
        miss.append(("цели", "field_targets"))
    sigs = [s for s in (row[9] or "").split(";") if s]
    if not sigs:
        miss.append(("сигналы", "field_signals"))
    if (row[10] or 1) <= 1:
        miss.append(("плечо", "field_leverage"))
    if miss:
        miss_text = "⚠️ У вас не указано: " + ", ".join(m for m, _ in miss) + ". Добавить их?"
        miss_buttons = [
            InlineKeyboardButton(text=f"Добавить {label}", callback_data=cb_data)
            for label, cb_data in miss
        ]
        rows = [miss_buttons[i:i+2] for i in range(0, len(miss_buttons), 2)]
        kb_miss = InlineKeyboardMarkup(inline_keyboard=rows)
        kb_miss = with_back(kb_miss)
        await cb.message.answer(miss_text, reply_markup=kb_miss)

    buttons = [
        ("📡 Сигналы", "field_signals"),
        ("🎯 Цели", "field_targets"),
        ("🛑 Стоп", "field_sl"),
        ("💼 %", "field_pct"),
        ("⚖️ Плечо", "field_leverage"),
        ("📆 Дата", "field_date"),
        ("💬 Коммент", "field_comment"),
    ]
    rows = [
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in buttons[i:i+2]]
        for i in range(0, len(buttons), 2)
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="active")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
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
    field = cb.data.split("_")[1]
    if field == "signals":
        await start_edit_signals(cb, state)
        return
    if field == "leverage":
        await start_edit_leverage(cb, state)
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
        entry_price, stop_loss, percent, t_type, lev = conn.execute(
            "SELECT entry_price, stop_loss, percent, trade_type, leverage FROM trades WHERE id=?",
            (tid,)
        ).fetchone()
        risk = calc_risk(entry_price, stop_loss, percent, t_type, lev)
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


async def start_edit_leverage(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("⚖️ Выбери новое плечо:", reply_markup=leverage_keyboard())
    await state.set_state(EditState.choosing_leverage)


@dp.callback_query(EditState.choosing_leverage, lambda c: c.data.startswith("lev_"))
async def edit_choose_leverage(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    val = cb.data.split("_")[1]
    if val == "manual":
        await cb.message.answer("Введи плечо вручную (например, 7.5):")
        await state.set_state(EditState.entering_leverage_manual)
        return
    lev = float(val)
    data = await state.get_data()
    tid = data.get("tid")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE trades SET leverage=? WHERE id=?", (lev, tid))
        entry_price, stop_loss, percent, t_type = conn.execute(
            "SELECT entry_price, stop_loss, percent, trade_type FROM trades WHERE id=?",
            (tid,),
        ).fetchone()
        risk = calc_risk(entry_price, stop_loss, percent, t_type, lev) if stop_loss is not None else None
        conn.execute("UPDATE trades SET risk_percent=? WHERE id=?", (risk, tid))
        conn.commit()
    await cb.message.answer("✅ Обновлено.")
    await state.clear()
    await go_home(cb.from_user.id, state)


@dp.message(EditState.entering_leverage_manual)
async def edit_leverage_manual(msg: types.Message, state: FSMContext):
    try:
        lev = float(msg.text.replace(",", "."))
    except ValueError:
        await msg.answer("Нужно число.")
        return
    data = await state.get_data()
    tid = data.get("tid")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE trades SET leverage=? WHERE id=?", (lev, tid))
        entry_price, stop_loss, percent, t_type = conn.execute(
            "SELECT entry_price, stop_loss, percent, trade_type FROM trades WHERE id=?",
            (tid,),
        ).fetchone()
        risk = calc_risk(entry_price, stop_loss, percent, t_type, lev) if stop_loss is not None else None
        conn.execute("UPDATE trades SET risk_percent=? WHERE id=?", (risk, tid))
        conn.commit()
    await msg.answer("✅ Обновлено.")
    await state.clear()
    await go_home(msg.from_user.id, state)


@dp.callback_query(EditState.choosing_signals, lambda c: c.data.startswith("sig_"))
async def edit_add_signal(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_")[1])
    name, _ = SIGNAL_OPTIONS[idx]
    data = await state.get_data()
    signals = data.get("signals", [])
    if name not in signals:
        signals.append(name)
        await state.update_data(signals=signals)
        total, _, _, _ = signal_stats(signals)
        await state.update_data(signals_total=total)
        await cb.answer("✅ Сигнал добавлен")
    else:
        await cb.answer("⚠️ Уже выбран")


@dp.callback_query(EditState.choosing_signals, F.data == "signals_done")
async def edit_signals_done(cb: types.CallbackQuery, state: FSMContext):
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
    await cb.answer("Сигналы обновлены")
    await state.update_data(signals=[])
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
    risk = calc_risk(data['entry_price'], data['stop_loss'], data['percent'], data['trade_type'], data.get('leverage', 1))
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
    await state.update_data(comment=comment)
    await msg.answer("⚖️ Выбери плечо:", reply_markup=leverage_keyboard())
    await state.set_state(TradeState.entering_leverage)


@dp.callback_query(TradeState.entering_leverage, lambda c: c.data.startswith("lev_"))
async def choose_leverage(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    val = cb.data.split("_")[1]
    if val == "manual":
        await cb.message.answer("Введи плечо вручную (например, 7.5):")
        await state.set_state(TradeState.entering_leverage_manual)
        return
    lev = float(val)
    data = await state.get_data()
    entry, stop, pct, t_type = data["entry_price"], data.get("stop_loss"), data.get("percent"), data["trade_type"]
    risk = calc_risk(entry, stop, pct, t_type, lev) if stop is not None else None
    await state.update_data(leverage=lev, risk=risk, signals=[])
    await start_signals_choice(cb.from_user.id, state, reset=True)


@dp.message(TradeState.entering_leverage_manual)
async def enter_leverage_manual(msg: types.Message, state: FSMContext):
    try:
        lev = float(msg.text.replace(",", "."))
    except ValueError:
        await msg.answer("Нужно число.")
        return
    data = await state.get_data()
    entry, stop, pct, t_type = data["entry_price"], data.get("stop_loss"), data.get("percent"), data["trade_type"]
    risk = calc_risk(entry, stop, pct, t_type, lev) if stop is not None else None
    await state.update_data(leverage=lev, risk=risk, signals=[])
    await start_signals_choice(msg.from_user.id, state, reset=True)


@dp.callback_query(TradeState.choosing_signals, lambda c: c.data.startswith("sig_"))
async def add_signal(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_")[1])
    name, _ = SIGNAL_OPTIONS[idx]
    data = await state.get_data()
    signals = data.get("signals", [])
    if name not in signals:
        signals.append(name)
        await state.update_data(signals=signals)
        total, _, _, _ = signal_stats(signals)
        await state.update_data(signals_total=total)
        await cb.answer("✅ Сигнал добавлен")
    else:
        await cb.answer("⚠️ Уже выбран")


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
    uid = cb.from_user.id
    data = await state.get_data()
    signals = data.get("signals", [])
    total, strong, medium, weak = signal_stats(signals)
    risk = data.get("risk")
    parts = [
        f"⭐️ Звёзд: {total}",
        f"🔥 Сильных сигналов: {strong}",
        f"🟡 Средние: {medium}",
        f"⚪️ Слабые: {weak}",
    ]
    if risk is not None:
        parts.append(f"🛑 Риск по стопу: {risk:.1f}%")
    text = "\n".join(parts)
    sub = get_subscription(uid)
    if sub in {"basic", "pro"}:
        symbol = data.get("symbol")
        ttype = data.get("trade_type")
        trend_text = ""
        reco_block = ""
        levels_block = ""
        if symbol and ttype:
            vol_line, vol_note, vol_short = await _volume_24h(symbol)
            trend_text, d_res, h_res = await _analyze_micro_trend(symbol, ttype, vol_short)
            rec_block, verdict_line, trend_bias = format_trend_recommendations(d_res, h_res)
            levels_block, supports, resistances = await _entry_exit_levels(symbol)
            zone_reco, zone_dir = await _sr_trade_reco(symbol, supports, resistances, bias=trend_bias)
            if zone_dir and trend_bias and (
                (zone_dir == "Short" and trend_bias == "up")
                or (zone_dir == "Long" and trend_bias == "down")
            ):
                zone_word = "сопротивления" if zone_dir == "Short" else "поддержки"
                trend_word = "восходящие" if trend_bias == "up" else "нисходящие"
                verdict_line = (
                    f"⚠️ Вердикт: Цена у {zone_word}. Несмотря на {trend_word} тренды, "
                    f"лучше ждать разворота и искать вход в {zone_dir}."
                )
            vol_block = "\n".join(filter(None, [vol_line, vol_note]))
            reco_block = ""
            if vol_block:
                reco_block += f"\n\n{vol_block}"
            reco_block += f"\n\n{rec_block}\n{verdict_line}"
            if levels_block:
                reco_block += f"\n\n{levels_block}"
            if zone_reco:
                reco_block += f"\n\n{zone_reco}"
        if trend_text:
            text += "\n\n" + trend_text + reco_block
        text += "\n\n" + await _build_ai_advice(uid, signals, strong, total, float(risk or 0), symbol)
    else:
        text += "\n\n🔐 Расширенный анализ доступен только с подпиской Basic. Сейчас отображён упрощённый анализ."
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
                data.get('leverage', 1),
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
    if is_automation_enabled(uid):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE trades SET notifications_enabled=1, notify_type='both', notify_mode='near', notify_near_pct=0.3, notify_stop_sent=0, notify_target_sent=0, notify_stagnation_sent=0, notify_risk_sent=0 WHERE id=?",
                (trade_id,),
            )
            conn.commit()
        return
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
    await state.set_state(NotifyState.ask)


def _analyze_trader_style(uid: int, risk: float, strong: int) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT risk_percent, signal_stars FROM trades WHERE user_id=? AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        return ""
    total = len(rows)
    high_risk = sum(1 for r, _ in rows if (r or 0) > 60)
    weak_conf = sum(1 for r, s in rows if (s or 0) < 6)
    parts = []
    if total and high_risk / total > 0.4:
        parts.append("высоким риском")
    if total and weak_conf / total > 0.4:
        parts.append("без подтверждений")
    if parts and (risk > 60 or strong < 2):
        joined = " и ".join(parts)
        return (
            "⚠️ Ты часто входишь с "
            f"{joined}. Текущий сетап тоже рискованный — подумай, не повторяешь ли ты ту же ошибку?"
        )
    return ""


def _analyze_signal_combos(uid: int, signals: list[str]) -> str:
    if len(signals) < 2:
        return ""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT signals, profit_percent FROM trades "
            "WHERE user_id=? AND signals!='' AND profit_percent IS NOT NULL",
            (uid,),
        ).fetchall()
    stats: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for sigs, prof in rows:
        sig_list = [s for s in sigs.split(";") if s]
        for combo in combinations(sorted(sig_list), 2):
            if prof and prof > 0:
                stats[combo][0] += 1
            else:
                stats[combo][1] += 1
    messages = []
    for combo in combinations(sorted(signals), 2):
        wins, loses = stats.get(combo, (0, 0))
        if wins >= 3 and wins > loses:
            messages.append(
                f"✅ Связка '{combo[0]} + {combo[1]}' у тебя {wins} раз отрабатывала на профит — это хороший знак."
            )
        elif loses >= 3 and loses >= wins:
            messages.append(
                f"❌ А вот '{combo[0]} + {combo[1]}' в {loses} сделках подряд дала минус. Будь осторожен."
            )
    return "\n".join(messages)


def _recommend_setup(strong: int, risk: float) -> str:
    if strong >= 3 and risk < 30:
        return (
            "📊 Сетап близок к сильному. Следи за объёмами, "
            "стоп под локальным минимумом, тейк на 2 уровня выше."
        )
    if risk > 60 or strong < 2:
        return (
            "📊 Сетап пока сырой. Я бы дождался объёма или повторного теста. "
            "Стоп держи коротким."
        )
    return (
        "📊 Можно рассмотреть вход, но усили его объёмом или ретестом. "
        "Стоп под локальным минимумом, тейк на 2 уровня выше."
    )


async def _fetch_kline(symbol: str, interval: str, limit: int = 7) -> list:
    resolved = await _resolve_symbol(symbol)
    if not resolved:
        return []
    real, category = resolved
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": category,
        "symbol": real,
        "interval": interval,
        "limit": limit,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
    except Exception:
        return []
    res = data.get("result", {}).get("list")
    return res or []


async def _volume_24h(symbol: str) -> tuple[str, str, str]:
    candles = await _fetch_kline(symbol, "D", 6)
    if not candles:
        return "", "", ""
    candles = sorted(candles, key=lambda c: int(c[0]))[-6:]
    vols = [float(c[5]) for c in candles]
    cur = vols[-1]
    prev = vols[:-1]
    avg = sum(prev) / len(prev) if prev else 0

    def fmt_vol(v: float) -> str:
        if v >= 1e9:
            return f"${v/1e9:.2f} млрд"
        if v >= 1e6:
            return f"${v/1e6:.2f} млн"
        return f"${v:.0f}"

    base = f"📊 Объём за 24ч: {fmt_vol(cur)}"
    if avg:
        diff = (cur - avg) / avg * 100
        if diff >= 15:
            note = f"🟢 Выше среднего на {diff:.0f}% — сигнал усиливается"
            short = "объёмы выше среднего"
        elif diff <= -15:
            note = f"🔴 Ниже среднего на {abs(diff):.0f}% — возможная слабость сигнала"
            short = "объёмы ниже среднего"
        else:
            note = "⚪ Объёмы на среднем уровне — нейтрально"
            short = "объёмы на среднем уровне"
    else:
        note = "⚪ Объём на текущем уровне — нейтрально"
        short = "объёмы на текущем уровне"
    return base, note, short


async def _micro_trend_tf(symbol: str, interval: str, limit: int = 50) -> dict:
    candles = await _fetch_kline(symbol, interval, limit + 10)
    if not candles:
        return {"trend": "nodata", "used": 0}
    # ensure chronological order and keep latest `limit` candles
    candles = sorted(candles, key=lambda c: int(c[0]))[-limit:]
    used = len(candles)
    if used < limit:
        return {"trend": "nodata", "used": used}

    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    vols = [float(c[5]) for c in candles]
    price = closes[-1]

    # EMA-based slope to smooth noise
    k = 2 / (limit + 1)
    ema_vals: list[float] = []
    ema = closes[0]
    ema_vals.append(ema)
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
        ema_vals.append(ema)
    slope_pct = (ema_vals[-1] - ema_vals[0]) / ema_vals[0] * 100 if ema_vals[0] else 0

    struct_thr = 0.003  # 0.3% difference threshold to filter noise
    up_pairs = down_pairs = 0
    for h1, h2, l1, l2 in zip(highs, highs[1:], lows, lows[1:]):
        diff = max(
            abs(h2 - h1) / h1 if h1 else 0,
            abs(l2 - l1) / l1 if l1 else 0,
        )
        if diff < struct_thr:
            continue
        if h2 > h1 and l2 > l1:
            up_pairs += 1
        elif h2 < h1 and l2 < l1:
            down_pairs += 1
    total_pairs = up_pairs + down_pairs
    if total_pairs == 0:
        total_pairs = 1
    range_ratio = (max(highs) - min(lows)) / price if price else 0

    half = used // 2 or 1
    recent = sum(vols[-half:]) / half
    prev = sum(vols[:half]) / half
    if recent > prev * 1.1:
        vol_dir = "up"
    elif recent < prev * 0.9:
        vol_dir = "down"
    else:
        vol_dir = "flat"

    slope_dir = "up" if slope_pct > 1 else "down" if slope_pct < -1 else "flat"
    struct_raw = (
        "up" if up_pairs > down_pairs else "down" if down_pairs > up_pairs else "mixed"
    )
    up_ratio = up_pairs / total_pairs
    down_ratio = down_pairs / total_pairs
    struct_major = "up" if up_ratio >= 0.6 else "down" if down_ratio >= 0.6 else "mixed"
    # if structure and volume agree but slope contradicts slightly, align with trend
    if struct_major in {"up", "down"} and vol_dir == struct_major:
        if struct_major == "up" and slope_pct < 0:
            slope_pct = abs(slope_pct)
            slope_dir = "up" if slope_pct > 1 else "flat"
        elif struct_major == "down" and slope_pct > 0:
            slope_pct = -abs(slope_pct)
            slope_dir = "down" if slope_pct < -1 else "flat"

    if struct_major in {"up", "down"}:
        trend = struct_major
    else:
        votes_up = (slope_dir == "up") + (struct_raw == "up") + (vol_dir == "up")
        votes_down = (slope_dir == "down") + (struct_raw == "down") + (vol_dir == "down")
        if votes_up >= 2 and votes_up > votes_down:
            trend = "up"
        elif votes_down >= 2 and votes_down > votes_up:
            trend = "down"
        elif abs(slope_pct) < 1 and range_ratio < 0.03:
            trend = "flat"
        else:
            trend = "uncertain"

    return {
        "trend": trend,
        "used": used,
        "price": price,
        "slope": slope_pct,
        "up": up_pairs,
        "down": down_pairs,
        "pairs": total_pairs,
        "vol": vol_dir,
        "struct": struct_raw,
        "struct_major": struct_major,
        "slope_dir": slope_dir,
    }


def _trend_line(name: str, limit: int, data: dict, extra_vol: str = "") -> str:
    trend = data.get("trend")
    used = data.get("used", 0)
    if trend == "nodata":
        return f"🔵 {name} ({used}/{limit} свечей): ❓ Недостаточно данных"

    price = data.get("price", 0)
    slope = data.get("slope", 0)
    up = data.get("up", 0)
    down = data.get("down", 0)
    vol_dir = data.get("vol", "flat")
    struct = data.get("struct", "mixed")
    total_pairs = data.get("pairs", max(used - 1, 1))

    struct_phrase = (
        f"структура: {up}/{total_pairs} HH/HL"
        if struct == "up"
        else f"структура: {down}/{total_pairs} LH/LL"
        if struct == "down"
        else f"структура: {up} HH/HL / {down} LH/LL"
    )

    vol_phrase = {
        "up": "объёмы растут",
        "down": "объёмы падают",
        "flat": "объёмы стабильны",
    }.get(vol_dir, "объёмы без изменений")
    if extra_vol:
        vol_phrase += f", {extra_vol}"

    arrow = {
        "up": "⬆️ Восходящий",
        "down": "⬇️ Нисходящий",
        "flat": "⏸️ Боковик",
        "uncertain": "❓ Неопределённый",
    }.get(trend, "❓ Неопределённый")

    price_str = f"цена: {price:.4f}"
    slope_str = f"наклон: {slope:+.1f}%"

    return f"🔵 {name} ({limit} свечей): {arrow} ({price_str}, {slope_str}, {struct_phrase}, {vol_phrase})"


async def _analyze_micro_trend(symbol: str, ttype: str, vol_short: str = "") -> tuple[str, dict, dict]:
    d_res: dict[str, dict] = {}
    for lvl, lim in TREND_WINDOWS["D"].items():
        d_res[lvl] = await _micro_trend_tf(symbol, "D", lim)
    h_res: dict[str, dict] = {}
    for lvl, lim in TREND_WINDOWS["240"].items():
        h_res[lvl] = await _micro_trend_tf(symbol, "240", lim)

    d_lines = ["📈 Тренд по 1D:"]
    for lvl, lim in TREND_WINDOWS["D"].items():
        d_lines.append(_trend_line(TREND_LEVELS[lvl], lim, d_res[lvl], vol_short))

    h_lines = ["📉 Тренд по 4H:"]
    for lvl, lim in TREND_WINDOWS["240"].items():
        h_lines.append(_trend_line(TREND_LEVELS[lvl], lim, h_res[lvl], vol_short))

    if d_res["global"].get("trend") == "down" and h_res["global"].get("trend") == "up":
        h_lines[1] = h_lines[1].replace("⬆️ Восходящий", "⬆️ Восходящий откат в нисходящем тренде")
    elif d_res["global"].get("trend") == "up" and h_res["global"].get("trend") == "down":
        h_lines[1] = h_lines[1].replace("⬇️ Нисходящий", "⬇️ Нисходящий откат в восходящем тренде")

    text = "\n".join(d_lines) + "\n\n" + "\n".join(h_lines)
    return text, d_res, h_res


def format_trend_recommendations(d_res: dict, h_res: dict) -> tuple[str, str, str | None]:
    LEVEL_EMOJI = {"global": "🔵", "local": "🟢", "scalp": "🟣"}
    LEVEL_NAME = {
        "global": "Глобальный тренд",
        "local": "Локальный тренд",
        "scalp": "Скальп",
    }
    DIR_TEXT = {
        "up": "↗ Восходящий",
        "down": "↘ Нисходящий",
        "flat": "⏸ Боковик",
        "uncertain": "🔄 Неопределённый",
    }

    def rec_line(tf: str, lvl: str, data: dict) -> str:
        trend = data.get("trend", "uncertain")
        vol = data.get("vol", "flat")
        struct = data.get("struct_major", "mixed")
        arrow = DIR_TEXT.get(trend, "🔄 Неопределённый")
        if tf == "4H" and lvl == "global":
            d_gl = d_res.get("global", {}).get("trend")
            if trend in {"up", "down"} and d_gl in {"up", "down"} and trend != d_gl:
                arrow = "↗ Откат" if trend == "up" else "↘ Откат"
                action = "следи за разворотом вниз" if trend == "up" else "следи за разворотом вверх"
                return f"{LEVEL_EMOJI[lvl]} {LEVEL_NAME[lvl]} ({tf}): {arrow} — {action}"
        if trend not in {"up", "down"} or struct == "mixed":
            action = "🟡 Тренд неопределён — наблюдай, жди подтверждений"
        elif vol == trend:
            if trend == "down":
                action = "🟢 Ищи вход на Long — возможно дно, смотри уровни"
            else:
                action = "🔴 Ищи вход на Short — возможно вершина"
        else:
            action = (
                "Жди подтверждения для входа в Long (ищи разворот у поддержки)"
                if trend == "up"
                else "Жди подтверждения для входа в Short (ищи разворот у сопротивления)"
            )
        return f"{LEVEL_EMOJI[lvl]} {LEVEL_NAME[lvl]} ({tf}): {arrow} — {action}"

    lines = ["📊 Рекомендации по трендам:"]
    for tf, res in [("1D", d_res), ("4H", h_res)]:
        for lvl in ("global", "local", "scalp"):
            lines.append(rec_line(tf, lvl, res.get(lvl, {})))

    dirs = []
    for res in (d_res, h_res):
        for lvl in ("global", "local", "scalp"):
            tr = res.get(lvl, {}).get("trend")
            if tr in {"up", "down"}:
                dirs.append(tr)
    up_count = dirs.count("up")
    down_count = dirs.count("down")
    if up_count and down_count:
        ver_dir = "Long" if up_count > down_count else "Short" if down_count > up_count else None
        if ver_dir:
            verdict = f"⚠️ Вердикт: Тренды противоречат — жди подтверждения для входа в {ver_dir}"
        else:
            verdict = "⚠️ Вердикт: Тренды противоречат — жди подтверждения"
    elif dirs and len(set(dirs)) == 1:
        direction = "Long" if dirs[0] == "up" else "Short"
        verdict = f"✅ Вердикт: Тренды совпадают — возможен уверенный вход в {direction}"
    else:
        verdict = "⚠️ Вердикт: Тренды неопределены — наблюдай"

    bias_dir: str | None = None
    if up_count > down_count:
        bias_dir = "up"
    elif down_count > up_count:
        bias_dir = "down"

    return "\n".join(lines), verdict, bias_dir


async def _entry_exit_levels_old(
    symbol: str, entry: float | None = None, interval: str = "240"
) -> tuple[str, float | None, float | None]:
    candles = await _fetch_kline(symbol, interval, 60)
    if not candles or len(candles) < 50:
        msg = "📊 Уровни входа/выхода:\n— Недостаточно данных для уровней."
        return msg, None, None
    candles = sorted(candles, key=lambda c: int(c[0]))[-50:]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    vols = [float(c[5]) for c in candles]
    avg_vol = sum(vols) / len(vols) if vols else 0

    swing_highs: list[tuple[float, float]] = []
    swing_lows: list[tuple[float, float]] = []
    for i in range(2, len(highs) - 2):
        h = highs[i]
        l = lows[i]
        if (
            h >= highs[i - 1]
            and h >= highs[i - 2]
            and h >= highs[i + 1]
            and h >= highs[i + 2]
            and (entry is None or h >= entry)
        ):
            swing_highs.append((h, vols[i]))
        if (
            l <= lows[i - 1]
            and l <= lows[i - 2]
            and l <= lows[i + 1]
            and l <= lows[i + 2]
            and (entry is None or l <= entry)
        ):
            swing_lows.append((l, vols[i]))
    if not swing_highs and entry is not None:
        swing_highs = [(highs[i], vols[i]) for i in range(len(highs))]
    if not swing_lows and entry is not None:
        swing_lows = [(lows[i], vols[i]) for i in range(len(lows))]
    if not swing_highs or not swing_lows:
        msg = "📊 Уровни входа/выхода:\n— Недостаточно данных для уровней."
        return msg, None, None
    high_val, high_vol = max(swing_highs, key=lambda x: x[0])
    low_val, low_vol = min(swing_lows, key=lambda x: x[0])
    high_touch = sum(1 for v in highs if abs(v - high_val) / high_val < 0.002)
    low_touch = sum(1 for v in lows if abs(v - low_val) / low_val < 0.002)
    basis = entry if entry is not None else max(high_val, low_val)
    step = 10 if basis >= 1000 else 5
    hi_lvl = int(round(high_val / step) * step)
    lo_lvl = int(round(low_val / step) * step)
    if hi_lvl <= lo_lvl:
        msg = "📊 Уровни входа/выхода:\n— Недостаточно данных для уровней."
        return msg, None, None
    lines: list[str] = []
    desc = f"Следи за зоной {lo_lvl}–{hi_lvl}. Пробой вверх — можно входить."
    notes = []
    if high_vol and high_vol >= avg_vol * 1.5:
        notes.append("сопротивление усилено объёмом")
    if low_vol and low_vol >= avg_vol * 1.5:
        notes.append("поддержка подтверждена объёмом")
    if high_touch > 1:
        notes.append(f"верх тестировался {high_touch} раза")
    if low_touch > 1:
        notes.append(f"низ тестировался {low_touch} раза")
    if notes:
        desc += " " + ", ".join(notes) + "."
    lines.append("— " + desc)
    lines.append(f"— Жди закрепа выше {hi_lvl} — для уверенного входа.")
    lines.append(f"— Пробой вниз ниже {lo_lvl} — лучше не входить (риск усилится).")
    msg = "📊 Уровни входа/выхода:\n" + "\n".join(lines)
    return msg, float(lo_lvl), float(hi_lvl)


async def _entry_exit_levels(
    symbol: str, entry: float | None = None, interval: str = "240",
) -> tuple[str, list[dict], list[dict]]:
    # Use the same lookback window for daily and four-hour analyses so
    # support/resistance logic behaves identically across timeframes.
    # Hourly charts keep a shorter window to stay responsive.
    limit = 200 if interval in ("D", "240") else 120
    candles = await _fetch_kline(symbol, interval, limit)
    if not candles or len(candles) < 50:
        msg = "📊 Уровни входа/выхода:\n— Недостаточно данных для уровней."
        return msg, [], []
    candles = sorted(candles, key=lambda c: int(c[0]))
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    vols = [float(c[5]) for c in candles]
    avg_vol = sum(vols) / len(vols) if vols else 0
    cur_price = closes[-1]

    swing_highs: list[tuple[float, float]] = []
    swing_lows: list[tuple[float, float]] = []
    for i in range(2, len(highs) - 2):
        h = highs[i]
        l = lows[i]
        if (
            h >= highs[i - 1]
            and h >= highs[i - 2]
            and h >= highs[i + 1]
            and h >= highs[i + 2]
            and (entry is None or h >= entry)
        ):
            swing_highs.append((h, vols[i]))
        if (
            l <= lows[i - 1]
            and l <= lows[i - 2]
            and l <= lows[i + 1]
            and l <= lows[i + 2]
            and (entry is None or l <= entry)
        ):
            swing_lows.append((l, vols[i]))
    top_idx = max(range(len(highs)), key=lambda i: highs[i])
    top_high = highs[top_idx]
    top_vol = vols[top_idx]
    top_close = closes[top_idx]
    future_slice = lows[top_idx + 1 : min(len(lows), top_idx + 3)]
    future_low = min(future_slice) if future_slice else lows[top_idx]
    wick_ratio = (top_high - top_close) / top_high
    drop_ratio = (top_high - future_low) / top_high
    top_reject = wick_ratio >= 0.006 or drop_ratio >= 0.01
    # всегда учитываем самый высокий экстремум как сопротивление, даже без повторных тестов
    swing_highs.append((top_high, top_vol))

    bottom_idx = min(range(len(lows)), key=lambda i: lows[i])
    bottom_low = lows[bottom_idx]
    bottom_vol = vols[bottom_idx]
    # всегда учитываем самый низкий экстремум как поддержку
    swing_lows.append((bottom_low, bottom_vol))
    if not swing_highs and entry is not None:
        swing_highs = [(highs[i], vols[i]) for i in range(len(highs))]
    if not swing_lows and entry is not None:
        swing_lows = [(lows[i], vols[i]) for i in range(len(lows))]
    if not swing_highs or not swing_lows:
        msg = "📊 Уровни входа/выхода:\n— Недостаточно данных для уровней."
        return msg, [], []

    basis = entry if entry is not None else cur_price
    if basis >= 1000:
        step = 10
    elif basis >= 100:
        step = 5
    elif basis >= 10:
        step = 1
    elif basis >= 1:
        step = 0.1
    elif basis >= 0.1:
        step = 0.01
    else:
        step = 0.001
    top_lvl = round(top_high / step) * step
    bottom_lvl = round(bottom_low / step) * step
    if interval == "D":
        # Daily charts cover a wide price range, so merge levels that fall within
        # roughly six percent of each other to avoid clutter.
        close_pct = 0.06
    elif interval == "240":
        close_pct = 0.02
    else:
        close_pct = 0.015

    def _prepare_levels(swings: list[tuple[float, float]], arr: list[float]) -> list[dict]:
        levels: dict[float, dict] = {}
        for val, vol in swings:
            lvl = round(val / step) * step
            rec = levels.setdefault(lvl, {"vol": 0.0})
            rec["vol"] = max(rec["vol"], vol)
        res: list[dict] = []
        for lvl, rec in levels.items():
            denom = lvl if lvl else step
            touches = sum(1 for v in arr if abs(v - lvl) / denom < 0.004)
            vol_ratio = rec["vol"] / avg_vol if avg_vol else 0
            dist = abs(cur_price - lvl)
            res.append(
                {
                    "level": float(lvl),
                    "touches": touches,
                    "vol": vol_ratio,
                    "dist": dist,
                }
            )
        # избегаем квадратичной фильтрации: уровни обрабатываются по приоритету,
        # а проверки близости выполняются через двоичный поиск
        from bisect import bisect_left

        # приоритет: сначала число касаний, затем величина объёма,
        # затем близость к текущей цене
        res.sort(key=lambda x: (x["touches"], x["vol"], -x["dist"]), reverse=True)
        kept: list[dict] = []
        prices: list[float] = []
        for lvl in res:
            pos = bisect_left(prices, lvl["level"])
            near_prev = (
                pos > 0
                and abs(lvl["level"] - prices[pos - 1]) / min(lvl["level"], prices[pos - 1])
                < close_pct
            )
            near_next = (
                pos < len(prices)
                and abs(lvl["level"] - prices[pos]) / min(lvl["level"], prices[pos])
                < close_pct
            )
            if near_prev or near_next:
                continue
            prices.insert(pos, lvl["level"])
            kept.insert(pos, lvl)
        return kept

    res_levels = _prepare_levels(swing_highs, highs)
    sup_levels = _prepare_levels(swing_lows, lows)
    if not res_levels or not sup_levels:
        msg = "📊 Уровни входа/выхода:\n— Недостаточно данных для уровней."
        return msg, [], []

    top_found = False
    for lvl in res_levels:
        if abs(lvl["level"] - top_lvl) < step / 2:
            top_found = True
            lvl["top"] = True
            if top_reject or lvl["vol"] >= 1.5:
                lvl["reject"] = True
            break
    if not top_found:
        denom = top_lvl if top_lvl else step
        touches = sum(1 for v in highs if abs(v - top_lvl) / denom < 0.004)
        top_rec = {
            "level": float(top_lvl),
            "touches": touches,
            "vol": top_vol / avg_vol if avg_vol else 0,
            "dist": abs(cur_price - top_lvl),
            "top": True,
        }
        if top_reject or top_rec["vol"] >= 1.5:
            top_rec["reject"] = True
        res_levels.append(top_rec)

    bottom_found = False
    for lvl in sup_levels:
        if abs(lvl["level"] - bottom_lvl) < step / 2:
            bottom_found = True
            lvl["bottom"] = True
            break
    if not bottom_found:
        denom = bottom_lvl if bottom_lvl else step
        touches = sum(1 for v in lows if abs(v - bottom_lvl) / denom < 0.004)
        sup_levels.append(
            {
                "level": float(bottom_lvl),
                "touches": touches,
                "vol": bottom_vol / avg_vol if avg_vol else 0,
                "dist": abs(cur_price - bottom_lvl),
                "bottom": True,
            }
        )

    def _select_zones(
        sups: list[dict], ress: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Return up to SR_MAX_ZONES support and resistance levels."""

        def prio(l: dict) -> tuple[int, float, float]:
            return (l["touches"], l["vol"], -l["dist"])

        sups_sorted = sorted(sups, key=prio, reverse=True)
        ress_sorted = sorted(ress, key=prio, reverse=True)

        def ensure(levels: list[dict], flag: str) -> list[dict]:
            selected = levels[:SR_MAX_ZONES]
            ext = next((l for l in levels if l.get(flag)), None)
            if ext and ext not in selected:
                selected.append(ext)
                selected = sorted(selected, key=prio, reverse=True)[:SR_MAX_ZONES]
            return selected

        sups_final = ensure(sups_sorted, "bottom")
        ress_final = ensure(ress_sorted, "top")
        return sups_final, ress_final

    sup_levels, res_levels = _select_zones(sup_levels, res_levels)

    if not sup_levels or not res_levels:
        msg = (
            "📊 Уровни входа/выхода:\n"
            "❌ Не удалось построить уровни: нет подходящих зон поддержки/сопротивления"
        )
        return msg, [], []

    def _importance(level: dict) -> str:
        score = 0
        if level["touches"] >= 3:
            score += 2
        elif level["touches"] == 2:
            score += 1
        if level["vol"] >= 1.5:
            score += 1
        dist_pct = level["dist"] / cur_price if cur_price else 1
        if dist_pct <= 0.02:
            score += 2
        elif dist_pct <= 0.05:
            score += 1
        if score >= 4:
            return "strong"
        if score >= 2:
            return "medium"
        return "weak"

    for lvl in sup_levels:
        lvl["importance"] = _importance(lvl)
    for lvl in res_levels:
        lvl["importance"] = _importance(lvl)

    if not MULTI_SR_MODE:
        res_levels = res_levels[:1]
        sup_levels = sup_levels[:1]

    hi_lvl = res_levels[0]["level"]
    lo_lvl = sup_levels[0]["level"]
    if hi_lvl <= lo_lvl:
        msg = "📊 Уровни входа/выхода:\n— Недостаточно данных для уровней."
        return msg, [], []

    lines: list[str] = []
    desc = f"Следи за зоной {int(lo_lvl)}–{int(hi_lvl)}. Пробой вверх — можно входить."
    notes = []
    if res_levels[0]["vol"] >= 1.5:
        notes.append("сопротивление усилено объёмом")
    if sup_levels[0]["vol"] >= 1.5:
        notes.append("поддержка подтверждена объёмом")
    if res_levels[0]["touches"] > 1:
        notes.append(f"верх тестировался {res_levels[0]['touches']} раза")
    if res_levels[0].get("top"):
        notes.append("верхний экстремум")
    if sup_levels[0]["touches"] > 1:
        notes.append(f"низ тестировался {sup_levels[0]['touches']} раза")
    if notes:
        desc += " " + ", ".join(notes) + "."
    lines.append("— " + desc)
    lines.append(f"— Жди закрепа выше {int(hi_lvl)} — для уверенного входа.")
    lines.append(f"— Пробой вниз ниже {int(lo_lvl)} — лучше не входить (риск усилится).")
    msg = "📊 Уровни входа/выхода:\n" + "\n".join(lines)

    if MULTI_SR_MODE:
        tbl: list[str] = []
        if sup_levels:
            tbl.append(f"Поддержки ({'1D' if interval == 'D' else '4H'}):")
            for s in sup_levels:
                vol_txt = (
                    f", объём {s['vol']:.1f}×" if s["vol"] >= 1.5 else ", слабый объём"
                )
                tbl.append(f"— {s['level']:.2f} ({s['touches']} теста{vol_txt})")
        if res_levels:
            tbl.append(f"Сопротивления ({'1D' if interval == 'D' else '4H'}):")
            for r in res_levels:
                vol_txt = (
                    f", объём {r['vol']:.1f}×" if r["vol"] >= 1.5 else ", слабый объём"
                )
                extra = ", верхний экстремум" if r.get("top") else ""
                tbl.append(
                    f"— {r['level']:.2f} ({r['touches']} касания{vol_txt}{extra})"
                )
        if tbl:
            msg += "\n\n" + "\n".join(tbl)

    return msg, sup_levels, res_levels


async def _sr_trade_reco(
    symbol: str,
    supports: list[dict],
    resistances: list[dict],
    bias: str | None = None,
    interval: str = "240",
) -> tuple[str, str | None]:
    """Form recommendation based on nearest support/resistance zone.

    Returns text message and dominant side ("Long"/"Short") if price
    trades near a strong zone.
    """
    if not supports and not resistances:
        return "", None
    candles = await _fetch_kline(symbol, interval, 60)
    if not candles:
        return ""
    candles = sorted(candles, key=lambda c: int(c[0]))
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]
    cur_price = closes[-1]

    def _atr() -> float:
        if len(highs) < 2:
            return 0.0
        trs = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        period = min(14, len(trs))
        return sum(trs[-period:]) / period if period else 0.0

    atr = _atr()
    vol_thresh = np.percentile(volumes, VOL_CONFIRM_PERCENTILE)
    cur_vol = volumes[-1]
    band_pct = BAND_PCT.get(interval, 0.004)

    zones: list[dict] = []
    for s in supports:
        band = s["level"] * band_pct
        zones.append(
            {
                "type": "S",
                "low": s["level"] - band,
                "high": s["level"] + band,
                "mid": s["level"],
                "touches": s.get("touches", 0),
                "vol": s.get("vol", 0),
                "strength": s.get("touches", 0) + (1 if s.get("vol", 0) >= 1.5 else 0),
            }
        )
    for r in resistances:
        band = r["level"] * band_pct
        zones.append(
            {
                "type": "R",
                "low": r["level"] - band,
                "high": r["level"] + band,
                "mid": r["level"],
                "touches": r.get("touches", 0),
                "vol": r.get("vol", 0),
                "strength": r.get("touches", 0) + (1 if r.get("vol", 0) >= 1.5 else 0),
            }
        )
    zones.sort(key=lambda z: (abs(cur_price - z["mid"]), -z["strength"]))
    z = zones[0]
    zone_txt = f"{z['low']:.2f}–{z['high']:.2f}"
    midpoint = z["mid"]
    band = (z["high"] - z["low"]) / 2
    near = abs(cur_price - midpoint) <= band
    side = "Long" if z["type"] == "S" else "Short"
    strong_zone = z.get("touches", 0) >= 4 and z.get("vol", 0) >= 2 and z.get("strength", 0) >= 4
    trends_align = (bias == "up" and side == "Long") or (bias == "down" and side == "Short")
    pattern_txt = (
        "бычий паттерн, close выше середины зоны, объём ↑"
        if side == "Long"
        else "медвежий паттерн, close ниже середины зоны, объём ↑"
    )
    bias_note = ""
    note_has_zone = False
    if z["type"] == "R" and near and z["strength"] >= 2:
        details = []
        if z.get("touches"):
            details.append(f"{z['touches']} касаний")
        if z.get("vol"):
            details.append(f"объём {z['vol']:.1f}x")
        extra = f" ({', '.join(details)})" if details else ""
        bias_note = f"Цена у зоны сопротивления {zone_txt}{extra}. "
        if bias == "up" and cur_vol >= vol_thresh:
            bias_note += (
                "Рост ослабевает у зоны сопротивления. Возможен откат вниз. "
                "Не спеши с Long, жди разворотного паттерна. "
            )
        note_has_zone = True
    elif z["type"] == "S" and near and z["strength"] >= 2:
        details = []
        if z.get("touches"):
            details.append(f"{z['touches']} касаний")
        if z.get("vol"):
            details.append(f"объём {z['vol']:.1f}x")
        extra = f" ({', '.join(details)})" if details else ""
        bias_note = f"Цена у сильной поддержки {zone_txt}{extra}. "
        if bias == "down" and cur_vol >= vol_thresh:
            bias_note += (
                "Падение ослабевает у зоны. Не спеши с Short, жди разворотного паттерна. "
            )
        note_has_zone = True

    state = ""
    if z["low"] <= cur_price <= z["high"]:
        state = "inside_zone"
    elif z["type"] == "R":
        if cur_price > z["high"] and cur_price - z["high"] > MIN_CLOSE_OUTSIDE_ATR * atr and cur_vol >= vol_thresh:
            state = "breakout_up"
        else:
            state = "approach_to_R" if cur_price < z["high"] else "approach_to_R"
    else:  # support
        if cur_price < z["low"] and z["low"] - cur_price > MIN_CLOSE_OUTSIDE_ATR * atr and cur_vol >= vol_thresh:
            state = "breakout_down"
        else:
            state = "approach_to_S" if cur_price > z["low"] else "approach_to_S"

    stop = None
    target = None
    if z["type"] == "R":
        stop = z["high"] + 0.3 * atr
        opp = [s["level"] for s in supports if s["level"] < midpoint]
        target = opp[0] if opp else midpoint - 1.5 * atr
    else:
        stop = z["low"] - 0.3 * atr
        opp = [r["level"] for r in resistances if r["level"] > midpoint]
        target = opp[0] if opp else midpoint + 1.5 * atr
    rr = abs(target - midpoint) / abs(midpoint - stop) if stop and target else 0

    zone_dir = side if near and z["strength"] >= 2 else None
    if state == "inside_zone":
        if strong_zone and trends_align:
            msg = (
                bias_note
                + f"Тренды совпадают. Ищи вход в {side} при появлении сигнала ("
                + f"{pattern_txt}). Стоп: {('под' if side=='Long' else 'за')} "
                + (f"{z['low']:.2f}-0.3 ATR" if side == 'Long' else f"{z['high']:.2f}+0.3 ATR")
                + ". Цели: ближайшее сопротивление / следующая зона."
            )
        else:
            base = "" if note_has_zone else f"Цена пилит внутри сильной зоны {zone_txt}. "
            msg = (
                bias_note
                + base
                + "Нейтрально. Торгуем только пробой/ретест с подтверждением. Без сигнала — пропуск."
            )
    elif state == "breakout_up":
        msg = (
            bias_note
            + f"Пробили R {zone_txt} телом ≥0.25 ATR на повышенном объёме. "
            f"План: Long по ретесту зоны, стоп за серединой зоны. TP: {target:.2f}. RR ≈ {rr:.2f}"
        )
    elif state == "breakout_down":
        msg = (
            bias_note
            + f"Пробили S {zone_txt} телом ≥0.25 ATR на повышенном объёме. "
            f"План: Short по ретесту зоны, стоп за серединой зоны. TP: {target:.2f}. RR ≈ {rr:.2f}"
        )
    else:
        base = (
            "" if note_has_zone else f"Зона {('S' if z['type']=='S' else 'R')} {zone_txt}. Подходим {'снизу' if z['type']=='R' else 'сверху'}. "
        )
        msg = bias_note + base
        if strong_zone and trends_align:
            msg += (
                f"Тренды совпадают. Ищи вход в {side} при появлении сигнала ({pattern_txt}). "
                + (
                    f"Стоп: под {z['low']:.2f}-0.3 ATR. Цели: ближайшее сопротивление / следующая зона."
                    if side == "Long"
                    else f"Стоп: за {z['high']:.2f}+0.3 ATR. Цели: ближайшая поддержка / следующая зона."
                )
            )
        else:
            if z["type"] == "R":
                msg += (
                    "Жди подтверждения Short: медвежий отказ сверху, close ниже середины зоны, объём ↑. "
                    f"Стоп: за {z['high']:.2f}+0.3 ATR. Цели: ближайшая поддержка / следующая зона."
                )
            else:
                msg += (
                    "Жди подтверждения Long: бычий отказ снизу, close выше середины зоны, объём ↑. "
                    f"Стоп: под {z['low']:.2f}-0.3 ATR. Цели: ближайшее сопротивление / следующая зона."
                )
        if rr and rr < MIN_RR:
            msg += f" RR ≈ {rr:.2f} — сделка невыгодна, пропуск."

    zone_label = "поддержки" if z["type"] == "S" else "сопротивления"
    vol_phrase = "объёмы выше нормы" if cur_vol >= vol_thresh else "объёмы падают"
    trend_phrase = (
        "тренды глобально растущие"
        if bias == "up"
        else "тренды глобально падающие" if bias == "down" else "тренды неопределённые"
    )
    if strong_zone and trends_align:
        summary = (
            f"📊 Резюме: цена находится у {zone_label} {zone_txt}, тренды совпадают, {vol_phrase} — "
            f"ищи вход в {side} при сигнале ({pattern_txt})."
        )
    else:
        summary = (
            f"📊 Резюме: цена находится у {zone_label}, {trend_phrase}, {vol_phrase} — "
            f"жди разворотного паттерна для входа в {side}."
        )
    return msg + "\n\n" + summary, zone_dir


async def _generate_price_chart(
    symbol: str,
    interval: str,
    supports: list[dict],
    resistances: list[dict],
    label: str,
    limit: int = 120,
) -> BufferedInputFile | None:
    candles = await _fetch_kline(symbol, interval, limit)
    if not candles:
        return None
    candles = sorted(candles, key=lambda c: int(c[0]))
    opens = [float(c[1]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]
    cur_price = closes[-1]

    band_pct = BAND_PCT.get(interval, 0.004)

    n = len(candles)
    fig_width = 8 if n <= 120 else 8 * n / 120
    fig, (ax, ax_v) = plt.subplots(
        2,
        1,
        figsize=(fig_width, 6),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
        facecolor="#1e1e1e",
    )
    for a in (ax, ax_v):
        a.set_facecolor("#1e1e1e")
        a.grid(color="gray", linestyle="--", alpha=0.3)
        a.tick_params(colors="white")
        for spine in a.spines.values():
            spine.set_color("white")

    width = 0.6
    for i, (o, h, l, c, v) in enumerate(zip(opens, highs, lows, closes, volumes)):
        color = "#00e676" if c >= o else "#ff1744"
        ax.plot([i, i], [l, h], color=color, linewidth=1, zorder=1)
        rect = Rectangle(
            (i - width / 2, min(o, c)),
            width,
            abs(c - o) or 0.001,
            facecolor=color,
            edgecolor=color,
            alpha=0.9,
            zorder=2,
        )
        rect.set_path_effects([
            pe.withStroke(linewidth=2, foreground="black", alpha=0.3)
        ])
        ax.add_patch(rect)
        ax_v.bar(i, v, width=width, color=color, alpha=0.5)

    ax.set_xlim(-0.5, len(candles) - 0.5)

    if not supports or not resistances:
        return None
    main_sup = supports[0]["level"]
    main_res = resistances[0]["level"]
    lo, hi = sorted([main_sup, main_res])
    gradient = np.linspace(0, 1, 256).reshape(-1, 1)
    ax.imshow(
        gradient,
        extent=[-0.5, len(candles) - 0.5, lo, hi],
        cmap="Greys",
        alpha=0.05,
        aspect="auto",
        zorder=0,
    )
    def _draw_levels(levels: list[dict], is_support: bool) -> None:
        icon = "🟩" if is_support else "🟥"
        colors = (
            {"strong": "#00FF00", "medium": "#90EE90", "weak": "#00FFFF"}
            if is_support
            else {"strong": "#FF0000", "medium": "#FFA500", "weak": "#FFFF00"}
        )
        for idx, lvl in enumerate(levels):
            color = colors.get(lvl.get("importance"), list(colors.values())[1])
            base_alpha = 0.25 * (0.7 ** idx)
            if lvl["importance"] == "weak":
                base_alpha *= 0.6
            if lvl["vol"] >= 1.5 or lvl["touches"] >= 3:
                base_alpha += 0.15
            alpha = min(base_alpha, 0.7)
            band = min(lvl["level"] * band_pct, cur_price * 0.015)
            lo_zone = lvl["level"] - band
            hi_zone = lvl["level"] + band
            ax.axhspan(lo_zone, hi_zone, color=color, alpha=alpha)
            name = "Поддержка" if is_support else "Сопротивление"
            text = f"{icon} {name}: {lo_zone:.2f}–{hi_zone:.2f}"
            info: list[str] = []
            if lvl["touches"] > 1:
                info.append(f"{lvl['touches']} касания")
            if lvl["vol"] >= 1.5:
                info.append(f"объём {lvl['vol']:.1f}×")
            if lvl.get("top"):
                info.append("верхний экстремум")
            importance_words = {
                "strong": "сильная",
                "medium": "средняя",
                "weak": "слабая",
            }
            info.append(f"важность: {importance_words.get(lvl.get('importance'), 'средняя')}")
            text += " • " + ", ".join(info)
            ax.text(
                len(candles) + 0.5,
                (lo_zone + hi_zone) / 2,
                text,
                color=color,
                va="center",
                alpha=min(alpha + 0.2, 1),
                fontsize=8,
            )

    _draw_levels(supports, True)
    _draw_levels(resistances, False)

    ax.set_title(f"{symbol} {interval}")
    plt.setp(ax.get_xticklabels(), visible=False)
    ax_v.tick_params(axis="x", colors="white")
    ax_v.set_ylabel("Vol", color="white")

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return BufferedInputFile(buf.getvalue(), filename=f"{symbol}_{interval}.png")


async def _send_sr_charts(
    chat_id: int,
    symbol: str,
    entry: float | None = None,
) -> None:
    _, sup_1d, res_1d = await _entry_exit_levels(symbol, entry, interval="D")
    _, sup_4h, res_4h = await _entry_exit_levels(symbol, entry, interval="240")
    _, sup_1h, res_1h = await _entry_exit_levels(symbol, entry, interval="60")
    for label, interval, sup_list, res_list in (
        ("1D", "D", sup_1d, res_1d),
        ("4H", "240", sup_4h, res_4h),
        ("1H", "60", sup_1h, res_1h),
    ):
        if not sup_list or not res_list:
            continue
        file = await _generate_price_chart(symbol, interval, sup_list, res_list, label, 300)
        if not file:
            continue
        sup_vals = ", ".join(f"{lvl['level']:.2f}" for lvl in sup_list)
        res_vals = ", ".join(f"{lvl['level']:.2f}" for lvl in res_list)
        caption = (
            f"{label}:\n"
            f"🟩 Поддержка {label}: {sup_vals}\n"
            f"🟥 Сопротивление {label}: {res_vals}"
        )
        await bot.send_photo(chat_id, file, caption=caption)


def _similar_trades_summary(
    uid: int,
    symbol: str,
    ttype: str,
    lev: float | None,
    stars: int,
    cur_signals: list[str],
    risk: float,
) -> str:
    lev_val = lev or 0
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT profit_percent, signals, risk_percent FROM trades "
            "WHERE user_id=? AND symbol=? AND trade_type=? "
            "AND ABS(COALESCE(leverage,0)-?)<=2 "
            "AND ABS(COALESCE(signal_stars,0)-?)<=2 "
            "AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
            (uid, symbol, ttype, lev_val, stars),
        ).fetchall()
    if not rows:
        return "Недостаточно похожих сделок для анализа. Собираем статистику…"

    wins: list[float] = []
    losses: list[float] = []
    win_sigs: Counter[str] = Counter()
    miss_sigs: Counter[str] = Counter()
    high_risk_losses = 0
    for pct, sigs, r in rows:
        sig_list = [s for s in (sigs or "").split(";") if s]
        if pct and pct > 0:
            wins.append(pct)
            for s in sig_list:
                win_sigs[s] += 1
        else:
            losses.append(pct or 0)
            if r and r > 30:
                high_risk_losses += 1
            for s in cur_signals:
                if s not in sig_list:
                    miss_sigs[s] += 1

    parts = ["📊 История похожих сделок:"]
    lev_str = fmt_leverage(lev_val)
    base = f"{symbol} / {ttype.capitalize()}"
    if lev_str:
        base += f" / {lev_str}"
    base += f" / {stars}⭐"
    if wins:
        avg_profit = sum(wins) / len(wins)
        parts.append(
            f"✅ {len(wins)} похожие сделки с {base} дали профит в среднем {avg_profit:+.1f}%"
        )
        for sig, cnt in win_sigs.most_common(2):
            parts.append(f"— {cnt} из них были с сигналом {sig}")
    if losses:
        parts.append(f"❌ {len(losses)} сделки дали убыток")
        if high_risk_losses:
            parts.append(f"— {high_risk_losses} из них были с риском > 30%")
        if miss_sigs:
            msig, cnt = max(miss_sigs.items(), key=lambda kv: kv[1])
            parts.append(f"— {cnt} из них были без сигнала {msig}")

    if win_sigs:
        top_sig, _ = win_sigs.most_common(1)[0]
        if top_sig not in cur_signals:
            parts.append(
                f"\n📌 Сейчас: не хватает сигнала {top_sig}, риск {risk:.1f}%. Подумай, стоит ли входить."
            )
            return "\n".join(parts)

    parts.append(
        f"\n📌 Сейчас: риск {risk:.1f}%. Подумай, стоит ли входить."
    )
    return "\n".join(parts)


def build_habits_report(uid: int) -> str:
    now = datetime.now()
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_week = now - timedelta(days=7)
    with sqlite3.connect(DB_PATH) as conn:
        today_rows = conn.execute(
            "SELECT signal_stars, risk_percent FROM trades WHERE user_id=? AND entry_date>=? AND COALESCE(is_deleted,0)=0",
            (uid, start_today.isoformat()),
        ).fetchall()
        week_rows = conn.execute(
            "SELECT signal_stars, risk_percent, mistake_reason, targets FROM trades WHERE user_id=? AND entry_date>=? AND COALESCE(is_deleted,0)=0",
            (uid, start_week.isoformat()),
        ).fetchall()
        combo_rows = conn.execute(
            "SELECT signals, profit_percent FROM trades WHERE user_id=? AND exit_price IS NOT NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    today_no_conf = sum(1 for s, r in today_rows if (s or 0) < 6)
    today_high_risk = sum(1 for s, r in today_rows if (r or 0) > 25)
    week_no_conf = sum(1 for s, r, _, _ in week_rows if (s or 0) < 6)
    week_high_risk = sum(1 for s, r, _, _ in week_rows if (r or 0) > 25)
    err_counts = defaultdict(int)
    for s, r, m, t in week_rows:
        errs = set()
        if (s or 0) < 6:
            errs.add("Слабые сигналы")
        if r and r > 50:
            errs.add("Риск выше 50%")
        if not t:
            errs.add("Отсутствует тейк")
        if m == "Не дождался ретеста":
            errs.add("Вход без ретеста")
        elif m == "Против тренда":
            errs.add("Игнор тренда")
        elif m == "Вход на хаях":
            errs.add("Вход на хаях")
        elif m == "Игнор объёма":
            errs.add("Игнор объёма")
        for e in errs:
            err_counts[e] += 1
    combo_stats: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for sigs, prof in combo_rows:
        if not sigs:
            continue
        sig_list = [s for s in sigs.split(";") if s]
        for combo in combinations(sorted(sig_list), 2):
            if prof and prof > 0:
                combo_stats[combo][0] += 1
            else:
                combo_stats[combo][1] += 1
    worst_combo = None
    worst_diff = 0
    for combo, (w, l) in combo_stats.items():
        if w + l >= 3 and l > w and (l - w) > worst_diff:
            worst_diff = l - w
            worst_combo = (combo, l)
    lines = [
        f"📊 За сегодня: без подтверждений — {today_no_conf}, риск>25% — {today_high_risk}",
        f"📉 За 7 дней: без подтверждений — {week_no_conf}, риск>25% — {week_high_risk}",
    ]
    if today_no_conf >= 2:
        lines.append(
            f"⚠️ Ты часто входишь без подтверждений — сегодня уже {today_no_conf} раза. Подумай, не повторяешь ли одну и ту же ошибку?"
        )
    if week_high_risk >= 3:
        lines.append(
            f"📉 Сделки с риском выше 25% — {week_high_risk} раза за неделю. Пересмотри управление рисками."
        )
    if worst_combo:
        combo, losses = worst_combo
        lines.append(
            f"🔁 Повторяешь неудачную связку: '{combo[0]} + {combo[1]}' — уже {losses} убытков подряд."
        )
    if err_counts:
        lines.append("❌ Повторяешь ошибки:")
        for name, cnt in sorted(err_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"– «{name}» — {cnt} раз{'' if cnt==1 else 'а'}")
    else:
        lines.append("✅ Ошибок не найдено — продолжаем в том же духе.")
    return "\n".join(lines)


async def _market_signal_balance() -> str:
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
        tickers = data.get("result", {}).get("list", [])
    except Exception:
        return ""

    tickers.sort(key=lambda t: float(t.get("turnover24h", 0) or 0), reverse=True)
    tickers = tickers[:30]

    long_count = short_count = 0
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = _base_from_symbol(sym)
        res = await _micro_trend_tf(base, "D", 50)
        if res.get("trend") not in {"up", "down"}:
            res = await _micro_trend_tf(base, "240", 50)
        trend = res.get("trend")
        vol_dir = res.get("vol")
        if trend == "up" and vol_dir != "down":
            long_count += 1
        elif trend == "down" and vol_dir != "up":
            short_count += 1

    total = long_count + short_count
    if total < 5:
        return ""
    long_pct = long_count / total * 100
    short_pct = short_count / total * 100
    line = f"📊 Рыночный баланс: {long_pct:.0f}% Long / {short_pct:.0f}% Short"
    if long_pct >= 65:
        line += " — Толпа в лонгах. Возможен откат вниз."
    elif short_pct >= 65:
        line += " — Толпа в шортах. Возможен отскок вверх."
    else:
        line += " — Баланс нейтральный."
    return line


async def _build_ai_advice(uid: int, signals: list[str], strong: int, total: int, risk: float, symbol: str | None = None) -> str:
    sig_names = ", ".join(signals) if signals else "—"
    header = (
        f"— Сигналы: {strong} сильных | Общий рейтинг: {total}⭐️\n"
        f"— Риск: {risk:.1f}%\n"
        f"📍 Сигналы в сделке: {sig_names}\n\n"
        "📊 Анализ:\n"
    )
    if risk > 60 and strong < 2:
        body = "❌ Сетап опасный. Я бы не входил. Подожди подтверждений."
    elif 30 <= risk <= 50 and strong >= 2:
        body = (
            "⚠️ Сетап нестабильный, но может выстрелить. "
            "Входи только частично и со стопом."
        )
    elif risk < 30 and strong >= 3:
        body = (
            "✅ Сетап сильный. Хороший шанс на профит. "
            "Следи за объёмами."
        )
    else:
        body = (
            "🤔 Пока выглядит слабо. Я бы подождал более "
            "чётких сигналов."
        )
    parts = [header + body]
    style = _analyze_trader_style(uid, risk, strong)
    combos = _analyze_signal_combos(uid, signals)
    rec = _recommend_setup(strong, risk)
    market = await _market_signal_balance() if symbol else ""
    for extra in (style, combos, rec, market):
        if extra:
            parts.append(extra)
    return "\n\n".join(parts)


async def maybe_send_ai_advice(uid: int, tid: int) -> None:
    if not is_automation_enabled(uid):
        return
    if get_subscription(uid) not in {"basic", "pro"}:
        return
    with sqlite3.connect(DB_PATH) as conn:
        pref = conn.execute(
            "SELECT habit_comment_enabled FROM user_settings WHERE user_id=?",
            (uid,),
        ).fetchone()
        if not pref or not pref[0]:
            return
        row = conn.execute(
            "SELECT signals, signal_stars, risk_percent, symbol, trade_type FROM trades WHERE id=? AND user_id=?",
            (tid, uid),
        ).fetchone()
    if not row:
        return
    signals, stars, risk, symbol, ttype = row
    sig_list = [s for s in (signals or "").split(";") if s]
    if not sig_list or not risk or not symbol or not ttype:
        return
    total, strong, _, _ = signal_stats(sig_list)
    risk = float(risk)
    vol_line, vol_note, vol_short = await _volume_24h(symbol)
    trend_text, d_res, h_res = await _analyze_micro_trend(symbol, ttype, vol_short)
    rec_block, verdict_line, trend_bias = format_trend_recommendations(d_res, h_res)
    levels_block, supports, resistances = await _entry_exit_levels(symbol)
    zone_reco, zone_dir = await _sr_trade_reco(symbol, supports, resistances, bias=trend_bias)
    if zone_dir and trend_bias and (
        (zone_dir == "Short" and trend_bias == "up")
        or (zone_dir == "Long" and trend_bias == "down")
    ):
        zone_word = "сопротивления" if zone_dir == "Short" else "поддержки"
        trend_word = "восходящие" if trend_bias == "up" else "нисходящие"
        verdict_line = (
            f"⚠️ Вердикт: Цена у {zone_word}. Несмотря на {trend_word} тренды, "
            f"лучше ждать разворота и искать вход в {zone_dir}."
        )
    vol_block = "\n".join(filter(None, [vol_line, vol_note]))
    trend_block = trend_text
    if vol_block:
        trend_block += f"\n\n{vol_block}"
    trend_block += f"\n\n{rec_block}\n{verdict_line}"
    if levels_block:
        trend_block += f"\n\n{levels_block}"
    if zone_reco:
        trend_block += f"\n\n{zone_reco}"
    text = "💡 Сетап оценён!\n\n" + trend_block + "\n\n" + await _build_ai_advice(uid, sig_list, strong, total, risk, symbol)
    await bot.send_message(uid, text)
    if supports and resistances:
        await _send_sr_charts(uid, symbol)


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
        await maybe_send_ai_advice(cb.from_user.id, tid)


@dp.callback_query(TradeState.confirming, F.data == "confirm_force")
async def add_trade_force(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tid = await save_trade(cb, state)
    await state.clear()
    await ask_notifications(cb.from_user.id, tid, state)
    await maybe_send_ai_advice(cb.from_user.id, tid)


@dp.callback_query(TradeState.confirming, F.data == "confirm_cancel")
async def add_trade_cancel(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await start_signals_choice(cb.from_user.id, state)


@dp.callback_query(NotifyState.ask, F.data == "notif_yes")
async def notif_yes(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Цель", callback_data="notif_type_target")],
            [InlineKeyboardButton(text="🔴 Стоп", callback_data="notif_type_stop")],
            [InlineKeyboardButton(text="🟡 Цель и Стоп", callback_data="notif_type_both")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_notify")],
        ]
    )
    await cb.message.answer("Что отслеживать?", reply_markup=with_back(kb))
    await state.set_state(NotifyState.choose_type)


@dp.callback_query(NotifyState.ask, F.data == "notif_no")
async def notif_no(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Ок, уведомления выключены.")
    await go_home(cb.from_user.id, state)


@dp.callback_query(NotifyState.choose_type, lambda c: c.data.startswith("notif_type_"))
async def notif_choose_type(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    ntype = cb.data.split("_")[2]
    await state.update_data(notify_type=ntype)
    if ntype in ("target", "stop"):
        other = "стопа" if ntype == "target" else "цели"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Да", callback_data="notif_add_yes")],
                [InlineKeyboardButton(text="Нет", callback_data="notif_add_no")],
            ]
        )
        await cb.message.answer(
            f"✅ Уведомление настроено для: {display_notify_type(ntype)}\nДобавить отслеживание для {other}?",
            reply_markup=kb,
        )
        await state.set_state(NotifyState.add_type)
    else:
        await ask_notify_mode(cb, state)


@dp.callback_query(NotifyState.choose_mode, lambda c: c.data.startswith("notif_mode_"))
async def notif_choose_mode(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    mode = cb.data.split("_")[2]
    await state.update_data(notify_mode=mode)
    if mode in ("near", "both"):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="±0.1%", callback_data="near_pct_0.1")],
                [InlineKeyboardButton(text="±0.3%", callback_data="near_pct_0.3")],
                [InlineKeyboardButton(text="±0.5%", callback_data="near_pct_0.5")],
                [InlineKeyboardButton(text="Ввести вручную", callback_data="near_pct_custom")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_notify")],
            ]
        )
        await cb.message.answer("Насколько близко уведомлять?", reply_markup=with_back(kb))
        await state.set_state(NotifyState.choose_near)
    else:
        await present_notif_summary(cb.message, state)


@dp.callback_query(NotifyState.add_type, F.data == "notif_add_yes")
async def notif_add_yes_cb(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(notify_type="both")
    await ask_notify_mode(cb, state)


@dp.callback_query(NotifyState.add_type, F.data == "notif_add_no")
async def notif_add_no_cb(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await ask_notify_mode(cb, state)


@dp.callback_query(NotifyState.choose_near, lambda c: c.data.startswith("near_pct_"))
async def notif_choose_near(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    val = cb.data.split("_")[2]
    if val == "custom":
        await cb.message.answer("Введите значение в процентах (например 0.7):")
        await state.set_state(NotifyState.enter_near)
        return
    await state.update_data(near_pct=float(val))
    await present_notif_summary(cb.message, state)


@dp.message(NotifyState.enter_near)
async def notif_enter_near(msg: types.Message, state: FSMContext):
    txt = msg.text.replace(",", ".")
    if not is_float(txt):
        await msg.answer("Введите число, например 0.7")
        return
    await state.update_data(near_pct=float(txt))
    await present_notif_summary(msg, state)


@dp.callback_query(NotifyState.confirm, F.data == "notif_enable")
async def notif_enable(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    data = await state.get_data()
    tid = data["notif_trade_id"]
    ntype = data["notify_type"]
    nmode = data["notify_mode"]
    near = data.get("near_pct", 0.3)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE trades SET notifications_enabled=1, notify_type=?, notify_mode=?, notify_near_pct=?, notify_stop_sent=0, notify_target_sent=0, notify_stagnation_sent=0, notify_risk_sent=0 WHERE id=?",
            (ntype, nmode, near, tid),
        )
        conn.commit()
    lines = ["Уведомления активированы:", f"▪️ Тип: {display_notify_type(ntype)}"]
    lines.extend(display_notify_mode(nmode, near))
    await cb.message.answer("\n".join(lines))
    await state.clear()
    await show_notifications_menu(cb.from_user.id, cb.message)

# ---------- CLOSE TRADE ----------
@dp.callback_query(lambda c: c.data == "close_trade")
async def close_trade_list(cb: types.CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, trade_type, symbol, entry_price, percent, leverage FROM trades "
            "WHERE user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        await cb.message.answer("Нет открытых сделок.")
        return
    ikb = []
    for tid, t, sym, e, p, lev in rows:
        pct_str = fmt_percent(p)
        if t and t.upper() == "SPOT":
            text = f"{sym} SPOT | Вход {fmt_price(e)} {pct_str}"
        else:
            lev_str = fmt_leverage(lev)
            text = f"{sym.upper()} {t.upper()} {lev_str} @ {fmt_price(e)} {pct_str}"
        ikb.append([InlineKeyboardButton(text=text, callback_data=f"close_{tid}")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=ikb))
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
        total_pct = conn.execute(
            "SELECT percent FROM trades WHERE id=? AND COALESCE(is_deleted,0)=0",
            (tid,),
        ).fetchone()[0]
    # если процент не указан (NULL), считаем что доступно 100%
    if total_pct is None:
        total_pct = 100.0
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
        user_id, t_type, sym, entry_price, sl, tgt, percent, entry_date, comment, signals, sstars, lev = cur.execute(
            "SELECT user_id, trade_type, symbol, entry_price, stop_loss, targets, percent, entry_date, comment, signals, signal_stars, leverage FROM trades WHERE id=? AND COALESCE(is_deleted,0)=0",
            (tid,),
        ).fetchone()
    # если процент не был задан, считаем, что изначально открыто 100%
    percent = percent if percent is not None else 100.0
    pnl = ((exit_price - entry_price) / entry_price) * (
        100 if t_type.lower() in {"long", "spot"} else -100
    )
    profit = round(pnl * close_pct / 100, 2)
    exit_date = datetime.now().strftime("%Y-%m-%d")
    risk_close = calc_risk(entry_price, sl, close_pct, t_type, lev) if sl is not None else None
    remaining = percent - close_pct
    risk_remain = (
        calc_risk(entry_price, sl, remaining, t_type, lev) if sl is not None and remaining > 0 else None
    )
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
        leverage=lev,
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
        "SELECT id, trade_type, symbol, entry_price, percent, leverage FROM trades WHERE user_id=? AND COALESCE(is_deleted,0)=0",
        sqlite3.connect(DB_PATH), params=(uid,)
    )
    if df.empty:
        await cb.message.answer("Нет сделок для удаления.")
        return
    ikb = []
    for _, row in df.iterrows():
        pct_str = fmt_percent(row.percent)
        if row.trade_type and row.trade_type.upper() == "SPOT":
            text = f"{row.id}: {row.symbol} SPOT | Вход {fmt_price(row.entry_price)} {pct_str}"
        else:
            lev_str = fmt_leverage(row.leverage)
            text = f"{row.id}: {row.trade_type.upper()} {row.symbol} {lev_str} @ {fmt_price(row.entry_price)} {pct_str}"
        ikb.append([InlineKeyboardButton(text=text, callback_data=f"del_{row.id}")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=ikb))
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
    if not await require_basic(cb.message, cb.from_user.id):
        return
    await state.clear()
    await cb.message.answer(
        "🔧 Оптимизация:", reply_markup=optimization_menu_kb(cb.from_user.id)
    )


@dp.callback_query(F.data == "opt_toggle")
async def toggle_automation(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    enabled = is_automation_enabled(uid)
    set_automation(uid, not enabled)
    text = (
        "✅ Автоматизация включена. Теперь бот будет выполнять действия автоматически (если это возможно)."
        if not enabled
        else "🔕 Автоматизация отключена. Теперь все функции работают только вручную."
    )
    try:
        await cb.message.edit_reply_markup(optimization_menu_kb(uid))
    except Exception:
        pass
    await cb.message.answer(text)


@dp.callback_query(F.data == "opt_bybit")
async def opt_bybit(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT api_key, api_secret, account_type FROM bybit_keys WHERE user_id=?",
            (uid,),
        ).fetchone()
    if not row or not row[0] or not row[1]:
        await state.set_state(BybitKeyState.api_key)
        await cb.message.answer(
            "🔐 Введите свой API-ключ и Secret от Bybit (USDT Perpetual)\n\nСначала отправьте API-ключ:",
        )
        return
    ok_pos, positions, acc_type = await fetch_bybit_positions(uid, row[0], row[1], row[2])
    ok_spot, spot_orders = await fetch_bybit_spot_history(row[0], row[1])
    ok_bal, balinfo = await fetch_bybit_balance(uid, row[0], row[1], row[2])
    bal_details: list[tuple[str, float, float]] = []
    if ok_bal:
        _, _, bal_details = balinfo
        await sync_spot_balances(uid, row[0], row[1], bal_details)
    if ok_spot and spot_orders:
        process_spot_history(uid, spot_orders)
    if ok_pos:
        positions = await sync_futures_positions(uid, positions)
    if not ok_pos:
        if positions == "401":
            await cb.message.answer("❌ Неверный API-ключ или Secret")
        else:
            await cb.message.answer(
                "❌ Не удалось связаться с Bybit: оба типа аккаунта не поддерживаются"
            )
        return
    if not positions:
        await cb.message.answer("❌ У тебя сейчас нет открытых фьючерсных сделок")
        return
    if is_automation_enabled(uid):
        for p in positions:
            tid = save_imported_trade(uid, p)
            await ask_notifications(uid, tid, state)
            await maybe_send_ai_advice(uid, tid)
        await state.clear()
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="optimization")]]
        )
        await cb.message.answer(
            "✅ Сделки автоматически импортированы с фьючерсного аккаунта Bybit!\nСтопы, цели и риск подтянуты автоматически.",
            reply_markup=with_back(kb),
        )
        return
    buttons = []
    for idx, p in enumerate(positions):
        side = "LONG" if p.get("side") == "Buy" else "SHORT"
        sym = _base_from_symbol(p.get("symbol", ""))
        lev = p.get("leverage")
        entry = p.get("entryPrice") or p.get("avgPrice")
        text = f"{side} {sym}"
        if lev:
            text += f" {lev}x"
        if entry:
            text += f" @ {entry}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"byimp_{idx}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(BybitImportState.choosing)
    await state.update_data(positions=positions)
    await cb.message.answer("Выбери сделку для импорта:", reply_markup=kb)


@dp.callback_query(F.data == "opt_autotrade")
async def optimization_stub(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_pro(cb.message, cb.from_user.id):
        return
    await cb.message.answer("🔒 Функция в разработке. Следи за обновлениями!")


@dp.callback_query(F.data == "opt_stops")
async def auto_stop_choose_trade(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, trade_type, entry_price, percent, leverage FROM trades "
            "WHERE user_id=? AND exit_price IS NULL AND entry_price IS NOT NULL "
            "AND trade_type IS NOT NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="optimization")]])
        await cb.message.answer("❌ Нет сделок с входом для расчёта.", reply_markup=with_back(kb))
        return
    buttons = []
    for tid, sym, ttype, entry, pct, lev in rows:
        if ttype.lower() == "short":
            side = "Short"
        elif ttype.lower() == "spot":
            side = "SPOT"
        else:
            side = "Long"
        lev_str = fmt_leverage(lev) if ttype.lower() != "spot" else ""
        label = f"🔹 {sym} / {side}"
        if lev_str:
            label += f" / {lev_str}"
        label += f" / Вход: {fmt_price(entry)}"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"ast_{tid}")
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
            "SELECT trade_type, entry_price, percent, leverage FROM trades WHERE id=? AND user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (tid, cb.from_user.id),
        ).fetchone()
    if not row:
        await cb.message.answer("❌ Сделка не найдена.")
        await state.clear()
        return
    await state.update_data(tid=tid, type=row[0], entry=row[1], percent=row[2], leverage=row[3])
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
async def ai_menu(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧠 AI-Советник", callback_data="ai_trades")],
            [InlineKeyboardButton(text="📊 Мои привычки", callback_data="ai_habits")],
            [InlineKeyboardButton(text="🔔 Уведомления", callback_data="ai_notif")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="optimization")],
        ]
    )
    await cb.message.answer("Что тебя интересует?", reply_markup=with_back(kb))


@dp.callback_query(F.data == "ai_coin")
async def ai_coin_prompt(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    await cb.message.answer("Введи тикер монеты (например, BTC):")
    await state.set_state(AICoinState.enter_symbol)


@dp.message(AICoinState.enter_symbol)
async def ai_coin_analyze(msg: types.Message, state: FSMContext):
    raw = (msg.text or "").strip().upper()
    base = _base_from_symbol(raw)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="opt_ai")]]
    )
    price = await fetch_price(base)
    if price is None:
        await msg.answer(
            f"❌ Монета {base} не найдена. Убедитесь, что она торгуется на Bybit и введите корректный тикер.",
            reply_markup=with_back(kb),
        )
        await state.clear()
        return
    await msg.answer("💬 Идёт анализ…")
    async with ChatActionSender.typing(bot, msg.chat.id):
        vol_line, vol_note, vol_short = await _volume_24h(base)
        trend_text, d_res, h_res = await _analyze_micro_trend(base, "LONG", vol_short)
        rec_block, verdict_line, trend_bias = format_trend_recommendations(d_res, h_res)
        levels_block, supports, resistances = await _entry_exit_levels(base, price)
        zone_reco, zone_dir = await _sr_trade_reco(base, supports, resistances, bias=trend_bias)
        if zone_dir and trend_bias and (
            (zone_dir == "Short" and trend_bias == "up")
            or (zone_dir == "Long" and trend_bias == "down")
        ):
            zone_word = "сопротивления" if zone_dir == "Short" else "поддержки"
            trend_word = "восходящие" if trend_bias == "up" else "нисходящие"
            verdict_line = (
                f"⚠️ Вердикт: Цена у {zone_word}. Несмотря на {trend_word} тренды, "
                f"лучше ждать разворота и искать вход в {zone_dir}."
            )
        vol_block = "\n".join(filter(None, [vol_line, vol_note]))
        trend_block = trend_text
        if vol_block:
            trend_block += f"\n\n{vol_block}"
        trend_block += f"\n\n{rec_block}\n{verdict_line}"
        if levels_block:
            trend_block += f"\n\n{levels_block}"
        if zone_reco:
            trend_block += f"\n\n{zone_reco}"
        advice = await _build_ai_advice(msg.from_user.id, [], 0, 0, 0, base)
        await msg.answer(trend_block + "\n\n" + advice, reply_markup=with_back(kb))
        if supports and resistances:
            await _send_sr_charts(msg.chat.id, base, entry=price)
    await state.clear()


@dp.callback_query(F.data == "ai_trades")
async def ai_advisor_list(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, symbol, trade_type, signals, signal_stars, leverage FROM trades "
            "WHERE user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (uid,),
        ).fetchall()
    if not rows:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Проанализировать монету", callback_data="ai_coin")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_ai")],
            ]
        )
        await cb.message.answer("У тебя нет активных сделок.", reply_markup=with_back(kb))
        return
    buttons = []
    for i, (tid, sym, t_type, sigs, stars, lev) in enumerate(rows, 1):
        total = stars if stars is not None else sum(
            SIGNAL_STARS.get(s, 0) for s in (sigs or "").split(";") if s
        )
        lev_str = fmt_leverage(lev) if t_type.lower() != "spot" else ""
        label = f"{i}. {sym} / {t_type.capitalize()}"
        if lev_str:
            label += f" / {lev_str}"
        label += f" / +{total}⭐️"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"aix_{tid}")
        ])
    buttons.append([InlineKeyboardButton(text="🤖 Проанализировать монету", callback_data="ai_coin")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="opt_ai")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.message.answer("Выбери сделку для анализа:", reply_markup=kb)


@dp.callback_query(lambda c: c.data.startswith("aix_"))
async def ai_advisor_run(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    tid = int(cb.data.split("_")[1])
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT symbol, trade_type, leverage, signals, signal_stars, risk_percent FROM trades "
            "WHERE id=? AND user_id=? AND exit_price IS NULL AND COALESCE(is_deleted,0)=0",
            (tid, uid),
        ).fetchone()
    if not row:
        await cb.message.answer("Сделка не найдена.")
        return
    symbol, t_type, lev, signals, stars, risk = row
    sig_list = [s for s in (signals or "").split(";") if s]
    if not sig_list or not risk:
        await cb.message.answer(
            "🔒 В этой сделке не указаны сигналы или риск.\n\n❗ Сначала укажи их — нажми \"📝 Изменить\", выбери сигналы и % риска."
        )
        await open_edit_trade(cb, tid, state)
        return
    await cb.message.answer("💬 Идёт анализ…")
    async with ChatActionSender.typing(bot, cb.message.chat.id):
        total, strong, medium, weak = signal_stats(sig_list)
        risk = float(risk)
        parts = [
            f"⭐️ Звёзд: {total}",
            f"🔥 Сильных сигналов: {strong}",
            f"🟡 Средние: {medium}",
            f"⚪️ Слабые: {weak}",
            f"🛑 Риск по стопу: {risk:.1f}%",
        ]
        vol_line, vol_note, vol_short = await _volume_24h(symbol)
        trend_text, d_res, h_res = await _analyze_micro_trend(symbol, t_type, vol_short)
        rec_block, verdict_line, trend_bias = format_trend_recommendations(d_res, h_res)
        levels_block, supports, resistances = await _entry_exit_levels(symbol)
        zone_reco, zone_dir = await _sr_trade_reco(symbol, supports, resistances, bias=trend_bias)
        if zone_dir and trend_bias and (
            (zone_dir == "Short" and trend_bias == "up")
            or (zone_dir == "Long" and trend_bias == "down")
        ):
            zone_word = "сопротивления" if zone_dir == "Short" else "поддержки"
            trend_word = "восходящие" if trend_bias == "up" else "нисходящие"
            verdict_line = (
                f"⚠️ Вердикт: Цена у {zone_word}. Несмотря на {trend_word} тренды, "
                f"лучше ждать разворота и искать вход в {zone_dir}."
            )
        vol_block = "\n".join(filter(None, [vol_line, vol_note]))
        trend_block = trend_text
        if vol_block:
            trend_block += f"\n\n{vol_block}"
        trend_block += f"\n\n{rec_block}\n{verdict_line}"
        if levels_block:
            trend_block += f"\n\n{levels_block}"
        if zone_reco:
            trend_block += f"\n\n{zone_reco}"
        text = (
            "\n".join(parts)
            + "\n\n"
            + trend_block
            + "\n\n"
            + await _build_ai_advice(uid, sig_list, strong, total, risk, symbol)
        )
        text += "\n\n" + _similar_trades_summary(
            uid, symbol, t_type, lev, total, sig_list, risk
        )
        kb = with_back(
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="ai_trades")]]
            )
        )
        await cb.message.answer(text, reply_markup=kb)
        if supports and resistances:
            await _send_sr_charts(cb.message.chat.id, symbol)


@dp.callback_query(F.data == "ai_habits")
async def ai_habits(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    text = build_habits_report(uid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="opt_ai")]]
    )
    await cb.message.answer(text, reply_markup=with_back(kb))


@dp.callback_query(F.data == "ai_notif")
async def ai_notif_menu(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    await _show_ai_notif(cb)


async def _show_ai_notif(cb: types.CallbackQuery) -> None:
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT habit_report_enabled, habit_report_time, habit_comment_enabled FROM user_settings WHERE user_id=?",
            (uid,),
        ).fetchone()
    rep, time_str, comm = row if row else (0, "21:00", 0)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("⏰ Автоотчёт о привычках: вкл" if rep else "⏰ Автоотчёт о привычках: выкл"),
                    callback_data="ai_notif_toggle",
                )
            ],
            [InlineKeyboardButton(text=f"⏱ Время отправки: {time_str}", callback_data="ai_notif_time")],
            [
                InlineKeyboardButton(
                    text=("💬 Комментарии: вкл" if comm else "💬 Комментарии: выкл"),
                    callback_data="ai_notif_comments",
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="opt_ai")],
        ]
    )
    await cb.message.answer("Настройки уведомлений:", reply_markup=with_back(kb))


@dp.callback_query(F.data == "ai_notif_toggle")
async def ai_notif_toggle(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (uid,))
        row = conn.execute(
            "SELECT habit_report_enabled FROM user_settings WHERE user_id=?", (uid,)
        ).fetchone()
        enabled = row[0] if row else 0
        conn.execute(
            "UPDATE user_settings SET habit_report_enabled=? WHERE user_id=?",
            (0 if enabled else 1, uid),
        )
        conn.commit()
    await _show_ai_notif(cb)


@dp.callback_query(F.data == "ai_notif_comments")
async def ai_notif_comments(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (uid,))
        row = conn.execute(
            "SELECT habit_comment_enabled FROM user_settings WHERE user_id=?", (uid,)
        ).fetchone()
        enabled = row[0] if row else 0
        conn.execute(
            "UPDATE user_settings SET habit_comment_enabled=? WHERE user_id=?",
            (0 if enabled else 1, uid),
        )
        conn.commit()
    await _show_ai_notif(cb)


@dp.callback_query(F.data == "ai_notif_time")
async def ai_notif_time(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    await state.set_state(HabitNotifyState.time)
    await cb.message.answer("Отправь время в формате ЧЧ:ММ")


@dp.message(HabitNotifyState.time)
async def ai_notif_set_time(msg: types.Message, state: FSMContext):
    if not await require_basic(msg, msg.from_user.id):
        await state.clear()
        return
    text = msg.text.strip()
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError:
        await msg.answer("Введи время в формате ЧЧ:ММ.")
        return
    uid = msg.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (uid,))
        conn.execute(
            "UPDATE user_settings SET habit_report_time=? WHERE user_id=?",
            (text, uid),
        )
        conn.commit()
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="ai_notif")]]
    )
    await msg.answer("Время автоотчёта обновлено.", reply_markup=with_back(kb))


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
            "INSERT OR REPLACE INTO bybit_keys (user_id, api_key, api_secret, account_type) VALUES (?,?,?,?)",
            (msg.from_user.id, api_key, api_secret, None),
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
    trade_id = save_imported_trade(cb.from_user.id, pos)
    await state.clear()
    await cb.message.answer(
        "✅ Сделка успешно импортирована с фьючерсного аккаунта Bybit!\nСтопы, цели и риск подтянуты автоматически. При необходимости отредактируй через \"📝 Изменить\" в текущих сделках."
    )
    await ask_notifications(cb.from_user.id, trade_id, state)
    await maybe_send_ai_advice(cb.from_user.id, trade_id)


@dp.callback_query(F.data == "opt_notify")
async def opt_notify(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    await state.clear()
    await process_notifications(cb.from_user.id)
    await show_notifications_menu(cb.from_user.id, cb.message)


async def send_notif_config(message: types.Message, uid: int, tid: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT symbol, trade_type, notifications_enabled, notify_type, notify_mode, notify_near_pct FROM trades WHERE id=? AND user_id=?",
            (tid, uid),
        ).fetchone()
        alerts = conn.execute(
            "SELECT id, price, mode, near_pct FROM price_alerts WHERE trade_id=?",
            (tid,),
        ).fetchall()
    if not row:
        await message.answer("Сделка не найдена.")
        return
    sym, t_type, enabled, ntype, nmode, npct = row
    lines = [f"Уведомления для {sym} {t_type}:"]
    if enabled:
        lines.append(f"Тип: {display_notify_type(ntype)}")
        lines.extend(display_notify_mode(nmode, npct))
    else:
        lines.append("Стоп/цель: выключены")
    if alerts:
        lines.append("Ценовые уведомления:")
        for i, (_, price, mode, apct) in enumerate(alerts, 1):
            mode_txt = display_pa_mode(mode, apct)
            lines.append(f"{i}. {price} — {mode_txt}")
    else:
        lines.append("Ценовых уведомлений нет")
    buttons: list[list[InlineKeyboardButton]] = []
    if enabled:
        buttons.append([InlineKeyboardButton(text="🔕 Отключить стоп/цель", callback_data=f"notif_disable_{tid}")])
    else:
        buttons.append([InlineKeyboardButton(text="🔔 Включить стоп/цель", callback_data=f"notif_enable_{tid}")])
    for aid, price, _, _ in alerts:
        buttons.append([
            InlineKeyboardButton(text=f"✏ {price}", callback_data=f"pa_edit_{aid}"),
            InlineKeyboardButton(text="🗑", callback_data=f"pa_del_{aid}"),
        ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить уведомление по цене", callback_data=f"pa_add_{tid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="opt_notify")])
    kb = with_back(InlineKeyboardMarkup(inline_keyboard=buttons))
    await message.answer("\n".join(lines), reply_markup=kb)


@dp.callback_query(lambda c: c.data.startswith("notif_cfg_"))
async def notif_cfg(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    tid = int(cb.data.split("_")[2])
    await send_notif_config(cb.message, uid, tid)


@dp.callback_query(lambda c: c.data == "notif_disable_all")
async def notif_disable_all(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE trades SET notifications_enabled=0, notify_type=NULL, notify_mode=NULL, notify_near_pct=NULL, notify_stop_sent=0, notify_target_sent=0, notify_stagnation_sent=0, notify_risk_sent=0 WHERE user_id=?",
            (uid,),
        )
        conn.commit()
    await cb.message.answer("🔕 Уведомления отключены для всех сделок.")
    await show_notifications_menu(uid, cb.message)


@dp.callback_query(lambda c: c.data.startswith("notif_disable_"))
async def notif_disable(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    tid = int(cb.data.split("_")[2])
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE trades SET notifications_enabled=0, notify_type=NULL, notify_mode=NULL, notify_near_pct=NULL, notify_stop_sent=0, notify_target_sent=0, notify_stagnation_sent=0, notify_risk_sent=0 WHERE id=? AND user_id=?",
            (tid, uid),
        )
        conn.commit()
    await cb.message.answer("🔕 Уведомления отключены.")
    await show_notifications_menu(uid, cb.message)


@dp.callback_query(lambda c: c.data.startswith("pa_add_"))
async def pa_add(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    tid = int(cb.data.split("_")[2])
    await state.update_data(pa_trade_id=tid, pa_edit_id=None, pa_symbol=None)
    await cb.message.answer("Введи цену для уведомления:")
    await state.set_state(PriceAlertState.waiting_price)


@dp.callback_query(F.data == "pa_manual_add")
async def pa_manual_add(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    await state.update_data(pa_trade_id=None, pa_edit_id=None, pa_symbol=None)
    await cb.message.answer("Введи тикер (например BTC):")
    await state.set_state(PriceAlertState.enter_symbol)


@dp.message(PriceAlertState.enter_symbol)
async def pa_manual_symbol(msg: types.Message, state: FSMContext):
    raw = (msg.text or "").strip().upper()
    base = _base_from_symbol(raw)
    if not base:
        await msg.answer("Введите тикер.")
        return
    price = await fetch_price(base)
    if price is None:
        await msg.answer(
            f"Монета {base} не найдена. Убедитесь, что она торгуется на Bybit и введите корректный тикер."
        )
        return
    await state.update_data(pa_symbol=base)
    await msg.answer("Введи цену для уведомления:")
    await state.set_state(PriceAlertState.waiting_price)


@dp.callback_query(lambda c: c.data.startswith("pa_edit_"))
async def pa_edit(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    aid = int(cb.data.split("_")[2])
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT trade_id, price, symbol FROM price_alerts WHERE id=?", (aid,)).fetchone()
    if not row:
        await cb.message.answer("Уведомление не найдено.")
        return
    tid, price, sym = row
    await state.update_data(pa_trade_id=tid, pa_edit_id=aid, pa_symbol=sym)
    await cb.message.answer(f"Текущая цена: {price}\nВведи новую цену:")
    await state.set_state(PriceAlertState.waiting_price)


@dp.callback_query(lambda c: c.data.startswith("pa_del_"))
async def pa_del(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    aid = int(cb.data.split("_")[2])
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT trade_id, user_id FROM price_alerts WHERE id=?", (aid,)).fetchone()
        if row:
            conn.execute("DELETE FROM price_alerts WHERE id=?", (aid,))
            conn.commit()
    if not row:
        await cb.message.answer("Уведомление не найдено.")
        return
    tid = row[0]
    await cb.message.answer("🗑 Уведомление удалено.")
    if tid:
        await send_notif_config(cb.message, cb.from_user.id, tid)
    else:
        await show_manual_alerts(cb.from_user.id, cb.message)


@dp.callback_query(F.data == "pa_manual_disable_all")
async def pa_manual_disable_all(cb: types.CallbackQuery):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    uid = cb.from_user.id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM price_alerts WHERE user_id=? AND manual=1", (uid,))
        conn.commit()
    await cb.message.answer("🔕 Вне-сделочные уведомления отключены.")
    await show_manual_alerts(uid, cb.message)


@dp.message(PriceAlertState.waiting_price)
async def pa_enter_price(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        await msg.answer("Введи число.")
        return
    price = float(msg.text.replace(",", "."))
    await state.update_data(pa_price=price)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="При точном достижении", callback_data="pa_mode_touch")],
            [InlineKeyboardButton(text="При приближении", callback_data="pa_mode_near")],
            [InlineKeyboardButton(text="При касании и приближении", callback_data="pa_mode_both")],
        ]
    )
    await msg.answer("Выбери режим:", reply_markup=with_back(kb))
    await state.set_state(PriceAlertState.choose_mode)


@dp.callback_query(PriceAlertState.choose_mode)
async def pa_mode_cb(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if cb.data.endswith("touch"):
        mode = "touch"
    elif cb.data.endswith("near"):
        mode = "near"
    else:
        mode = "both"
    await state.update_data(pa_mode=mode)
    if mode in ("near", "both"):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="±0.1%", callback_data="pa_near_0.1")],
                [InlineKeyboardButton(text="±0.3%", callback_data="pa_near_0.3")],
                [InlineKeyboardButton(text="±0.5%", callback_data="pa_near_0.5")],
                [InlineKeyboardButton(text="Ввести вручную", callback_data="pa_near_custom")],
            ]
        )
        await cb.message.answer("Насколько близко уведомлять?", reply_markup=with_back(kb))
        await state.set_state(PriceAlertState.choose_sensitivity)
    else:
        await save_price_alert(cb.message, state)


@dp.callback_query(PriceAlertState.choose_sensitivity)
async def pa_sens_cb(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if cb.data.endswith("custom"):
        await cb.message.answer("Введи процент (например 0.7):")
        await state.set_state(PriceAlertState.enter_custom)
    else:
        pct = float(cb.data.split("_")[2])
        await state.update_data(pa_near_pct=pct)
        await save_price_alert(cb.message, state)


@dp.message(PriceAlertState.enter_custom)
async def pa_custom(msg: types.Message, state: FSMContext):
    if not is_float(msg.text):
        await msg.answer("Введи число.")
        return
    pct = float(msg.text.replace(",", "."))
    await state.update_data(pa_near_pct=pct)
    await save_price_alert(msg, state)


@dp.callback_query(lambda c: c.data.startswith("notif_enable_"))
async def notif_enable(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    if not await require_basic(cb.message, cb.from_user.id):
        return
    tid = int(cb.data.split("_")[2])
    await state.update_data(notif_trade_id=tid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Цель", callback_data="notif_type_target")],
            [InlineKeyboardButton(text="🔴 Стоп", callback_data="notif_type_stop")],
            [InlineKeyboardButton(text="🟡 Цель и Стоп", callback_data="notif_type_both")],
        ]
    )
    await cb.message.answer("Что отслеживать?", reply_markup=with_back(kb))
    await state.set_state(NotifyState.choose_type)


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
            conn.execute("DELETE FROM auto_updates WHERE user_id=?", (uid,))
            conn.commit()
        await msg.answer("Все данные очищены.")
    else:
        await msg.answer("Очистка отменена.")
    await go_home(msg.from_user.id, state)


@dp.callback_query(F.data == "sub_manage")
async def sub_manage(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer()
        return
    await cb.answer()
    await cb.message.answer("Введи ID пользователя:")
    await state.set_state(SubscriptionState.waiting_user)


@dp.message(SubscriptionState.waiting_user)
async def sub_manage_user(msg: types.Message, state: FSMContext):
    try:
        target = int(msg.text.strip())
    except Exception:
        await msg.answer("Некорректный ID. Введи число.")
        return
    await state.update_data(target=target)
    sub = get_subscription(target)
    names = {
        "none": "Без подписки",
        "free": "Free",
        "basic": "Basic",
        "pro": "Pro",
    }
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Включить Basic-подписку", callback_data="subset_basic")],
            [InlineKeyboardButton(text="✅ Включить Pro-подписку", callback_data="subset_pro")],
            [InlineKeyboardButton(text="✅ Включить Free-подписку", callback_data="subset_free")],
            [InlineKeyboardButton(text="❌ Включить режим “Без подписки вообще”", callback_data="subset_none")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
        ]
    )
    await msg.answer(
        f"🔍 Текущая подписка: {names.get(sub, 'Без подписки')}\nВыберите нужный режим:",
        reply_markup=kb,
    )


@dp.callback_query(F.data.startswith("subset_"))
async def sub_manage_set(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer()
        return
    await cb.answer()
    data = await state.get_data()
    target = data.get("target")
    if not target:
        await cb.message.answer("ID пользователя не задан.")
        return
    sub = cb.data.split("_", 1)[1]
    set_subscription(target, sub)
    names = {
        "none": "Без подписки",
        "free": "Free",
        "basic": "Basic",
        "pro": "Pro",
    }
    await cb.message.answer(
        f"✅ Подписка изменена: теперь у пользователя {target} {names[sub]}"
    )
    try:
        await bot.send_message(target, f"✅ Подписка изменена: теперь у тебя {names[sub]}")
    except Exception:
        pass
    await state.clear()


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
            lines.extend(
                [
                    f"{i}. 📍 {name}",
                    f"   📊 {st['count']} | 📈 {st['avg']:+.1f}% | WR: {st['wr']:.0f}%",
                    "",
                ]
            )
    else:
        lines.append("Нет прибыльных связок.\n")

    worst = [item for item in combo_stats.items() if item[1]["avg"] < 0]
    worst = sorted(worst, key=lambda kv: kv[1]["avg"])[:5]
    if worst:
        lines.append("💀 Худшие связки:")
        for i, (name, st) in enumerate(worst, 1):
            lines.extend(
                [
                    f"{i}. 📍 {name}",
                    f"   📊 {st['count']} | 📉 {st['avg']:+.1f}% | WR: {st['wr']:.0f}%",
                    "",
                ]
            )
    else:
        lines.append("Нет убыточных связок.")
    return "\n".join(lines).rstrip()


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
            st = stats.setdefault(
                s,
                {"count": 0, "wins": 0, "losses": 0, "profit_sum": 0.0, "loss_sum": 0.0},
            )
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

    lines = ["📊 Аналитика по сетапам", ""]
    for name, st in sorted(stats.items(), key=lambda kv: kv[1]["count"], reverse=True):
        avg_profit = st["profit_sum"] / st["wins"] if st["wins"] else 0
        avg_loss = st["loss_sum"] / st["losses"] if st["losses"] else 0
        winrate = st["wins"] / st["count"] * 100 if st["count"] else 0
        lines.extend(
            [
                f"📍 {name}",
                f"   ⭐️ {st['count']} | 📈 {st['wins']} | 📉 {st['losses']} | WR: {winrate:.1f}%",
                f"   🎯 {avg_profit:+.1f}% | 📉 {avg_loss:+.1f}%",
                "",
            ]
        )

    top_wr = sorted(
        stats.items(),
        key=lambda kv: kv[1]["wins"] / kv[1]["count"] if kv[1]["count"] else 0,
        reverse=True,
    )[:5]
    if top_wr:
        lines.append("🏆 ТОП-5 по винрейту:")
        for i, (name, st) in enumerate(top_wr, 1):
            wr = st["wins"] / st["count"] * 100 if st["count"] else 0
            lines.append(f"{i}. 📍 {name} — WR: {wr:.1f}% ({st['count']})")
        lines.append("")

    top_profit = [item for item in stats.items() if item[1]["wins"]]
    top_profit = sorted(
        top_profit,
        key=lambda kv: kv[1]["profit_sum"] / kv[1]["wins"],
        reverse=True,
    )[:5]
    if top_profit:
        lines.append("📈 ТОП-5 по среднему профиту:")
        for i, (name, st) in enumerate(top_profit, 1):
            lines.append(f"{i}. 📍 {name} — {st['profit_sum'] / st['wins']:.1f}%")
        lines.append("")

    top_losses = sorted(stats.items(), key=lambda kv: kv[1]["losses"], reverse=True)[:5]
    if top_losses:
        lines.append("📉 ТОП-5 по частоте в убыточных:")
        for i, (name, st) in enumerate(top_losses, 1):
            lines.append(f"{i}. 📍 {name} — {st['losses']}")
        lines.append("")

    profit_pairs = sorted(pair_stats.items(), key=lambda kv: kv[1]["wins"], reverse=True)[:5]
    if profit_pairs:
        lines.append("🤝 Связки в профитных сделках:")
        for i, (pair, st) in enumerate(profit_pairs, 1):
            lines.append(f"{i}. 📍 {pair} — {st['wins']}")
        lines.append("")

    loss_pairs = sorted(pair_stats.items(), key=lambda kv: kv[1]["losses"], reverse=True)[:5]
    if loss_pairs:
        lines.append("💔 Связки в убыточных:")
        for i, (pair, st) in enumerate(loss_pairs, 1):
            lines.append(f"{i}. 📍 {pair} — {st['losses']}")

    return "\n".join(lines).rstrip()


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
                    sigs.append(f"{s} — {'⭐️'*SIGNAL_STARS.get(s, 0)}")
        sig_text = "; ".join(sigs) if sigs else "—"
        lines.append(
            f"{i}. 📍 {sym}\n   🎯 {pnl:+.2f}%\n   🧠 {sig_text}\n   🕓 {entry_date} — {exit_date}"
        )
    if best:
        lines.append(
            "\n⭐️ Самый высокозвёздочный сетап месяца:\n"
            f"📍 {best[0]} — {best[1]}⭐️"
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
async def charts(cb: types.CallbackQuery, state: FSMContext):
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
    fig1, ax1 = make_fig()
    colors = ["green" if v >= 0 else "red" for v in weekly.values]
    bars = ax1.bar(weekly.index.astype(str), weekly.values, color=colors)
    ax1.set_title("📊 PNL по неделям", fontweight="bold")
    ax1.set_xlabel("Неделя")
    ax1.set_ylabel("%")
    add_labels(ax1, fmt="{:+.1f}%")
    p1 = "pnl_week.png"
    fig1.savefig(p1, bbox_inches="tight", dpi=150, facecolor=fig1.get_facecolor())
    plt.close(fig1)

    # Stop freq
    df["is_loss"] = df["pnl"] < 0
    stop_freq = df["is_loss"].value_counts(sort=False)
    fig2, ax2 = make_fig()
    labels = ["Прибыль" if idx is False else "Убыток" for idx in stop_freq.index]
    colors = ["green" if idx is False else "red" for idx in stop_freq.index]
    ax2.bar(labels, stop_freq.values, color=colors)
    ax2.set_title("⚠️ Частота стопов", fontweight="bold")
    add_labels(ax2, fmt="{:.0f}")
    p2 = "stop_freq.png"
    fig2.savefig(p2, bbox_inches="tight", dpi=150, facecolor=fig2.get_facecolor())
    plt.close(fig2)

    # Winrate by type
    winrate = (df[df["pnl"] > 0].groupby("trade_type").size() / df.groupby("trade_type").size() * 100).fillna(0)
    fig3, ax3 = make_fig()
    labels = [lab.upper() for lab in winrate.index]
    colors = ["green" if lab.lower() == "long" else "red" for lab in winrate.index]
    ax3.bar(labels, winrate.values, color=colors)
    ax3.set_title("🏆 Винрейт по типу", fontweight="bold")
    ax3.set_ylabel("%")
    add_labels(ax3, fmt="{:.0f}%")
    p3 = "winrate.png"
    fig3.savefig(p3, bbox_inches="tight", dpi=150, facecolor=fig3.get_facecolor())
    plt.close(fig3)

    await bot.send_photo(uid, FSInputFile(p1), caption="📊 PNL по неделям")
    await bot.send_photo(uid, FSInputFile(p2), caption="⚠️ Частота стопов")
    await bot.send_photo(uid, FSInputFile(p3), caption="🏆 Винрейт по типу")
    await cb.message.answer("✅ Готово!")
    await go_home(uid, state)

# ---------- RUN ----------
async def main():
    asyncio.create_task(reminder_scheduler())
    asyncio.create_task(report_scheduler())
    asyncio.create_task(notification_scheduler())
    asyncio.create_task(auto_update_scheduler())
    asyncio.create_task(habit_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
