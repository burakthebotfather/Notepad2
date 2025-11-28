#!/usr/bin/env python3
"""
Delivery Profit Bot - full implementation (Variant A)
Storage: JSON
Usage:
  - Set environment variable BOT_TOKEN with your Telegram bot token.
  - Run: python bot.py
"""

import os
import re
import asyncio
import json
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
from collections import defaultdict

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

BOT_TOKEN = os.getenv("BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    raise SystemExit("Please set BOT_TOKEN environment variable")

# timezone (UTC+3)
TZ = ZoneInfo("Europe/Minsk")

# Unique admin/user ID to receive daily copies
ADMIN_USER_ID = int(os.getenv("UNIQUE_USER_ID", 542345855))

# Allowed chat_id:thread_id mapping
ALLOWED = {
    -1002079167705: 48,
    -1002936236597: 3,
    -1002423500927: 2,
    -1003117964688: 5,
    -1002864795738: 3,
    -1002535060344: 5,
    -1002477650634: 3,
    -1003204457764: 4,
    -1002660511483: 3,
    -1002360529455: 3,
    -1002538985387: 3,
}

CHAT_NAMES = {
    -1002079167705: "A. Mousse Art Bakery - Белинского, 23",
    -1002936236597: "B. Millionroz.by - Тимирязева, 67",
    -1002423500927: "E. Flovi.Studio - Тимирязева, 65Б",
    -1003117964688: "F. Flowers Titan - Мележа, 1",
    -1002864795738: "G. Цветы Мира - Академическая, 6",
    -1002535060344: "H. Kudesnica.by - Старовиленский тракт, 10",
    -1002477650634: "I. Cvetok.by - Восточная, 41",
    -1003204457764: "J. Jungle.by - Неманская, 2",
    -1002660511483: "K. Pastel Flowers - Сурганова, 31",
    -1002360529455: "333. ТЕСТ БОТОВ - 1-й Нагатинский пр-д",
    -1002538985387: "L. Lamour.by - Кропоткина, 84",
}

DATA_FILE = "data.json"

# price map per spec
PRICE_BASE = 10.00  # '+' in city
PRICE_MK = 5.00
PRICE_GAB_UNIT = 7.00  # n*габ base
COLORS = {
    "синяя": 8.00, "красная": 16.00, "оранжевая": 25.00, "салатовая": 33.00,
    "коричневая": 42.00, "светло-серая": 50.00, "розовая": 49.00, "темно-серая": 67.00,
    "голубая": 76.00
}

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"drivers": {}, "entries": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

DATA = load_data()

def next_entry_id():
    ids = [e["id"] for e in DATA.get("entries", [])] or [0]
    return max(ids) + 1

def now_iso():
    return datetime.now(TZ).isoformat()

def parse_cash_from_text(text: str) -> float:
    m = re.search(r"(\d+[.,]?\d*)(?=\s*(р|руб|руб\.|р\.)?)", text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except:
            return 0.0
    return 0.0

def compute_earn_from_text(text: str) -> (float, list):
    text_l = text.lower()
    if "+" not in text_l:
        return 0.0, []
    after = text_l.split("+", 1)[1]
    earn = 0.0
    triggers = []
    if "++" in text_l:
        earn += PRICE_BASE * 2
        triggers.append("++")
    else:
        earn += PRICE_BASE
        triggers.append("+")
    if re.search(r"\bмк\b", after):
        earn += PRICE_MK
        triggers.append("мк")
    for color in COLORS.keys():
        if color in after:
            earn += COLORS[color]
            triggers.append(color)
    for m in re.finditer(r"(\d+)\s*\*?\s*габ", after):
        n = int(m.group(1))
        earn += n * PRICE_GAB_UNIT
        triggers.append(f"{n}габ")
    if re.search(r"\bгаб\b", after) and not re.search(r"\d+\s*\*?\s*габ", after):
        earn += PRICE_GAB_UNIT
        triggers.append("габ")
    return round(earn,2), triggers

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT = Bot(token=BOT_TOKEN)
DP = Dispatcher()

SCHEDULED_TASKS = {}
AWAITING_CORRECTION = {}

# command handlers
@DP.message(Command(commands=["start"]))
async def cmd_start(msg: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="/open - открыть смену", callback_data="noop")],
        [InlineKeyboardButton(text="/close - закрыть смену", callback_data="noop")],
        [InlineKeyboardButton(text="/report - сформировать отчет", callback_data="noop")],
    ])
    await msg.answer(
        "Привет! Я фиксирую отметки по доставкам.\n\nДоступные команды:\n"
        "/open - открыть смену\n/close - закрыть смену\n/max - подробный режим (уведомления сразу)\n/min - итоговый режим (уведомления в конце)\n/report - сформировать отчет (после /close, через 5 мин)\n/off - выйти с линии (нужно перед /report)",
        reply_markup=kb
    )

@DP.message(Command(commands=["open"]))
async def cmd_open(msg: types.Message):
    user = msg.from_user.id
    drivers = DATA.setdefault("drivers", {})
    state = drivers.get(str(user), {})
    state["open"] = True
    state["open_time"] = now_iso()
    state["mode"] = state.get("mode", "min")
    state["entries"] = state.get("entries", [])
    state["last_activity"] = now_iso()
    state["off"] = False
    drivers[str(user)] = state
    save_data(DATA)
    await msg.reply(f"Вы на линии с {datetime.now(TZ).strftime('%H:%M:%S')}! Режим: {state['mode']} (по умолчанию /min)")

@DP.message(Command(commands=["max"]))
async def cmd_max(msg: types.Message):
    user = msg.from_user.id
    drivers = DATA.setdefault("drivers", {})
    state = drivers.get(str(user), {})
    state["mode"] = "max"
    drivers[str(user)] = state
    save_data(DATA)
    await msg.reply("Режим оповещений: подробный (/max). После каждой отметки бот будет присылать начисления.")

@DP.message(Command(commands=["min"]))
async def cmd_min(msg: types.Message):
    user = msg.from_user.id
    drivers = DATA.setdefault("drivers", {})
    state = drivers.get(str(user), {})
    state["mode"] = "min"
    drivers[str(user)] = state
    save_data(DATA)
    await msg.reply("Режим оповещений: в конце дня (/min). Бот будет накапливать отметки.")

@DP.message(Command(commands=["off"]))
async def cmd_off(msg: types.Message):
    user = msg.from_user.id
    drivers = DATA.setdefault("drivers", {})
    state = drivers.get(str(user), {})
    state["off"] = True
    drivers[str(user)] = state
    save_data(DATA)
    await msg.reply("Вы ушли с линии. Теперь можно формировать отчет после /close.")

@DP.message(Command(commands=["close"]))
async def cmd_close(msg: types.Message):
    user = msg.from_user.id
    drivers = DATA.setdefault("drivers", {})
    state = drivers.get(str(user), {})
    if not state.get("open"):
        await msg.reply("Смена не была открыта.")
        return
    state["open"] = False
    state["close_time"] = now_iso()
    drivers[str(user)] = state
    save_data(DATA)
    asyncio.create_task(wait_and_create_report(user, delay=5*60))
    await msg.reply("Смена закрыта. Через 5 минут можно формировать /report.")

async def wait_and_create_report(user_id: int, delay: int = 300):
    await asyncio.sleep(delay)
    data = DATA
    drivers = data.setdefault("drivers", {})
    state = drivers.get(str(user_id), {})
    state["ready_for_report"] = True
    drivers[str(user_id)] = state
    save_data(data)

@DP.message(Command(commands=["report"]))
async def cmd_report(msg: types.Message):
    user = msg.from_user.id
    drivers = DATA.setdefault("drivers", {})
    state = drivers.get(str(user), {})
    if state.get("open"):
        await msg.reply("Сначала закрой смену командой /close.")
        return
    if not state.get("ready_for_report"):
        await msg.reply("Отчёт будет доступен через 5 минут после /close.")
        return
    if not state.get("off"):
        await msg.reply("Перед формированием отчёта войдите с линии командой /off.")
        return
    open_t = datetime.fromisoformat(state.get("open_time")) if state.get("open_time") else None
    close_t = datetime.fromisoformat(state.get("close_time")) if state.get("close_time") else None
    entries = []
    for e in DATA.get("entries", []):
        if e["driver_id"] == user and e["processed"]:
            et = datetime.fromisoformat(e["accepted_ts"])
            if open_t and close_t and open_t <= et <= close_t:
                entries.append(e)
    total_income = sum(e["earn"] for e in entries)
    total_cash = sum(e.get("cash",0.0) for e in entries)
    balance = round(total_cash - total_income,2)
    count = len(entries)
    text_lines = [
        f"{datetime.now(TZ).strftime('%d.%m.%Y')}",
        f"{msg.from_user.full_name} (id:{user})",
        "",
        f"Доход: {total_income:.2f} BYN",
        f"Наличные: {total_cash:.2f} BYN",
        f"Баланс: {balance:.2f} BYN",
        "",
        f"Количество: {count}",
        ""
    ]
    by_chat = {}
    for e in entries:
        by_chat.setdefault(e["chat_id"], []).append(e)
    for cid, lst in by_chat.items():
        name = CHAT_NAMES.get(int(cid), str(cid))
        text_lines.append(f"{name}:")
        for e in lst:
            text_lines.append(f" - {e['text']} ({e['earn']:.2f} BYN, cash {e.get('cash',0.0):.2f})")
        text_lines.append("")
    report_text = "\n".join(text_lines)
    await msg.reply(report_text)
    try:
        await BOT.send_message(ADMIN_USER_ID, f"[Отчёт водителя] {report_text}")
    except Exception:
        pass
    state["ready_for_report"] = False
    save_data(DATA)

@DP.message()
async def handle_any_message(msg: types.Message):
    if msg.chat.id not in ALLOWED:
        return
    expected_thread = ALLOWED[msg.chat.id]
    if msg.message_thread_id != expected_thread:
        return
    user = msg.from_user.id
    drivers = DATA.setdefault("drivers", {})
    state = drivers.get(str(user), {})
    if not state.get("open"):
        return
    text = msg.text or ""
    if "+" not in text:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Повторная запись отметки", callback_data=f"repeat|{msg.chat.id}|{msg.message_id}")]
        ])
        sent = await msg.reply("Ошибка. Отсутствует основной триггер. Пожалуйста, нажмите кнопку «повторная запись отметки» и введите данные корректно", reply_markup=kb)
        AWAITING_CORRECTION[user] = {"orig_chat": msg.chat.id, "orig_msg": msg.message_id, "bot_msg": sent.message_id}
        return
    entry_id = next_entry_id()
    entry = {
        "id": entry_id,
        "driver_id": user,
        "chat_id": msg.chat.id,
        "thread_id": msg.message_thread_id,
        "text": text,
        "ts": msg.date.astimezone(TZ).isoformat(),
        "processed": False,
        "accepted_ts": None,
        "earn": 0.0,
        "cash": 0.0
    }
    DATA.setdefault("entries", []).append(entry)
    save_data(DATA)
    async def delayed_process(entry_local, user_local, chat_local):
        await asyncio.sleep(5*60)
        e = next((x for x in DATA.get("entries", []) if x["id"]==entry_local["id"]), None)
        if not e:
            return
        earn, triggers = compute_earn_from_text(e["text"])
        cash = parse_cash_from_text(e["text"])
        e["earn"] = earn
        e["cash"] = cash
        e["processed"] = True
        e["accepted_ts"] = now_iso()
        save_data(DATA)
        state_l = DATA.setdefault("drivers", {}).get(str(user_local), {})
        state_l["last_activity"] = now_iso()
        DATA["drivers"][str(user_local)] = state_l
        save_data(DATA)
        if state_l.get("mode","min") == "max":
            summary = (f"+{earn:.2f} BYN • {cash:.2f} BYN\n"
                       f"{CHAT_NAMES.get(chat_local, str(chat_local))}\n"
                       f"{e['ts']}\n\nАдрес: {e['text'].split('+')[0].strip()}\nТриггеры: {', '.join(triggers)}\n\n"
                       f"Доход за смену: {calc_driver_total(user_local):.2f} BYN\nБаланс за смену: {calc_driver_balance(user_local):.2f} BYN")
            try:
                await BOT.send_message(user_local, summary)
            except Exception:
                pass
    task = asyncio.create_task(delayed_process(entry, user, msg.chat.id))
    SCHEDULED_TASKS[entry_id] = task
    await msg.reply("Отметка принята. Данные будут зафиксированы через 5 минут (можно исправить).")

@DP.callback_query(lambda c: c.data and c.data.startswith("repeat|"))
async def cb_repeat(call: types.CallbackQuery):
    parts = call.data.split("|")
    user = call.from_user.id
    if AWAITING_CORRECTION.get(user) is None:
        await call.answer("Нет ожидающих исправлений.", show_alert=False)
        return
    await call.message.answer("Правильная отметка:")
    AWAITING_CORRECTION[user]["expecting_private"] = True
    await call.answer()

@DP.message(lambda m: m.chat.type == "private")
async def private_message(msg: types.Message):
    user = msg.from_user.id
    if user in AWAITING_CORRECTION and AWAITING_CORRECTION[user].get("expecting_private"):
        text = msg.text or ""
        if "+" not in text:
            await msg.reply("Ошибка. В корректной отметке отсутствует '+'. Попробуйте ещё раз.")
            return
        info = AWAITING_CORRECTION[user]
        orig_chat = info["orig_chat"]
        orig_msg = info["orig_msg"]
        bot_msg = info["bot_msg"]
        entry_id = next_entry_id()
        entry = {
            "id": entry_id,
            "driver_id": user,
            "chat_id": orig_chat,
            "thread_id": ALLOWED.get(orig_chat),
            "text": text,
            "ts": now_iso(),
            "processed": True,
            "accepted_ts": now_iso(),
            "earn": 0.0,
            "cash": 0.0
        }
        earn, triggers = compute_earn_from_text(text)
        cash = parse_cash_from_text(text)
        entry["earn"] = earn
        entry["cash"] = cash
        DATA.setdefault("entries", []).append(entry)
        save_data(DATA)
        await msg.reply("Отметка принята! Старые пометки записи будут удалены в автоматическом режиме через некоторое время")
        async def delayed_delete():
            await asyncio.sleep(3*60)
            try:
                await BOT.delete_message(chat_id=orig_chat, message_id=orig_msg)
            except Exception:
                pass
            try:
                await BOT.delete_message(chat_id=orig_chat, message_id=bot_msg)
            except Exception:
                pass
            try:
                await BOT.delete_message(chat_id=user, message_id=msg.message_id)
            except Exception:
                pass
        asyncio.create_task(delayed_delete())
        drivers = DATA.setdefault("drivers", {})
        state = drivers.get(str(user), {})
        if state.get("mode") == "max":
            try:
                await BOT.send_message(user, f"+{earn:.2f} BYN • {cash:.2f} BYN\n{CHAT_NAMES.get(orig_chat)}\n{entry['ts']}\nАдрес: {text.split('+')[0].strip()}\nТриггеры: {', '.join(triggers)}\n\nДоход за смену: {calc_driver_total(user):.2f} BYN\nБаланс за смену: {calc_driver_balance(user):.2f} BYN")
            except Exception:
                pass
        AWAITING_CORRECTION.pop(user, None)

def calc_driver_total(user_id: int) -> float:
    total = 0.0
    for e in DATA.get("entries", []):
        if e["driver_id"] == user_id and e.get("processed"):
            total += float(e.get("earn",0.0))
    return round(total,2)

def calc_driver_cash(user_id: int) -> float:
    total = 0.0
    for e in DATA.get("entries", []):
        if e["driver_id"] == user_id and e.get("processed"):
            total += float(e.get("cash",0.0))
    return round(total,2)

def calc_driver_balance(user_id: int) -> float:
    return round(calc_driver_cash(user_id) - calc_driver_total(user_id),2)

async def background_tasks():
    while True:
        now = datetime.now(TZ)
        drivers = DATA.setdefault("drivers", {})
        for uid, st in list(drivers.items()):
            if not st.get("open"):
                continue
            last = datetime.fromisoformat(st.get("last_activity")) if st.get("last_activity") else None
            if last:
                if (now - last) > timedelta(hours=3) and not st.get("reminder_sent"):
                    try:
                        await BOT.send_message(int(uid), "Активности нет длительное время. Пожалуйста, закройте смену и сформируйте отчет!")
                        st["reminder_sent"] = True
                        save_data(DATA)
                    except Exception:
                        pass
                if st.get("reminder_sent") and (now - last) > timedelta(hours=4):
                    try:
                        await BOT.send_message(int(uid), "Активности нет длительное время. Пожалуйста, закройте смену и сформируйте отчет!")
                    except Exception:
                        pass
        if now.time().hour == 23 and now.time().minute == 0:
            for uid, st in list(drivers.items()):
                if st.get("open"):
                    st["open"] = False
                    st["close_time"] = now.isoformat()
                    st["ready_for_report"] = True
                    try:
                        await BOT.send_message(ADMIN_USER_ID, f"Водитель {uid} не закрыл смену — бот закрыл автоматически и сформировал отчёт.")
                    except Exception:
                        pass
            save_data(DATA)
        await asyncio.sleep(60)

async def on_startup():
    asyncio.create_task(background_tasks())

if __name__ == "__main__":
    print("Starting bot...")
    asyncio.get_event_loop().create_task(on_startup())
    from aiogram import executor
    executor.start_polling(DP, BOT, skip_updates=True)
