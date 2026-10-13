import os
import json
import time
import logging
import asyncio
import requests
from dotenv import load_dotenv
from utils import vk_send_message, vk_upload_photo_and_get_attachment, make_keyboard
import storage

# Базовая директория проекта (чтобы пути работали из любой cwd)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID") or 0)
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)
CARE_PROGRAM_URL = (os.getenv("CARE_PROGRAM_URL") or "").strip()

if not VK_GROUP_TOKEN:
    logger.error("VK_GROUP_TOKEN не задан в .env")
    raise SystemExit(1)
if not VK_GROUP_ID:
    logger.error("VK_GROUP_ID не задан или равен 0")
    raise SystemExit(1)

logger.info(f"Конфигурация: GROUP_ID={VK_GROUP_ID}, ADMIN_ID={ADMIN_ID}")

# Загрузка викторины
QUIZ_PATH = os.path.join(BASE_DIR, "quiz.json")
try:
    with open(QUIZ_PATH, "r", encoding="utf-8") as f:
        QUIZ = json.load(f)
    logger.info(
        f"Викторина загружена: {len(QUIZ['questions'])} вопросов, "
        f"{len(QUIZ['animals'])} животных"
    )
except Exception as e:
    logger.error(f"Ошибка при загрузке quiz.json: {e}")
    raise SystemExit(1)


class LongPollServer:
    def __init__(self, token: str, api_version: str, group_id: int):
        self.token = token
        self.api_version = api_version
        self.group_id = group_id
        self.server = None
        self.key = None
        self.ts = None

    def get_server(self):
        params = {
            "group_id": self.group_id,
            "access_token": self.token,
            "v": self.api_version,
        }
        r = requests.get(
            "https://api.vk.com/method/groups.getLongPollServer",
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            err = data["error"]
            logger.error(f"VK API error: {err}")
            raise Exception(err.get("error_msg", str(err)))
        resp = data.get("response", {})
        self.server = resp.get("server")
        self.key = resp.get("key")
        self.ts = resp.get("ts")
        if not all([self.server, self.key, self.ts]):
            raise Exception("Incomplete LongPoll server response")
        logger.info(f"LongPoll server OK, ts={self.ts}")

    def poll(self):
        if not all([self.server, self.key, self.ts]):
            return []
        params = {
            "act": "a_check",
            "key": self.key,
            "ts": self.ts,
            "wait": 25,
        }
        try:
            r = requests.get(self.server, params=params, timeout=35)
            r.raise_for_status()
            data = r.json()
            failed = data.get("failed")
            if failed:
                logger.warning(f"LongPoll failed: {failed}")
                # 1 — история устарела, 2/3 — ключ/сервер устарели
                self.get_server()
                return []
            self.ts = data.get("ts", self.ts)
            return data.get("updates", [])
        except requests.exceptions.Timeout:
            return []
        except Exception as e:
            logger.error(f"Poll error: {e}")
            time.sleep(2)
            try:
                self.get_server()
            except Exception:
                pass
            return []


async def send_question(peer_id: int, q_index: int):
    if q_index >= len(QUIZ["questions"]):
        return
    q = QUIZ["questions"][q_index]
    text = (
        f"❓ Вопрос {q_index + 1}/{len(QUIZ['questions'])}\n\n"
        f"{q['text']}\n\nВыберите вариант:"
    )
    options = [opt["text"] for opt in q["options"]]
    keyboard = make_keyboard(options, one_time=True)
    vk_send_message(peer_id, text, keyboard=keyboard)
    await asyncio.sleep(0.05)


async def send_result(peer_id: int, scores: dict):
    if not scores:
        vk_send_message(peer_id, "Ошибка подсчёта. Введите /restart.")
        return

    animal = max(scores.items(), key=lambda x: x[1])[0]
    info = QUIZ["animals"].get(animal, {})
    desc = info.get("description", "Спасибо за участие!")
    photo_rel = info.get("photo")
    attachment = None

    if photo_rel:
        photo_path = os.path.join(BASE_DIR, photo_rel)
        if os.path.isfile(photo_path):
            try:
                attachment = vk_upload_photo_and_get_attachment(photo_path, peer_id)
                logger.info(f"Photo uploaded: {attachment}")
            except Exception as e:
                logger.warning(f"Photo upload failed: {e}")
        else:
            logger.warning(f"Photo not found: {photo_path}")

    text = (
        f"🎉 Твоё тотемное животное — {animal.upper()}!\n\n"
        f"{desc}\n\n"
        f"Хотите узнать о программе опеки?"
    )
    keyboard = {
        "one_time": False,
        "buttons": [
            [
                {
                    "action": {"type": "text", "label": "Узнать больше"},
                    "color": "primary",
                },
                {
                    "action": {"type": "text", "label": "Стать опекуном"},
                    "color": "positive",
                },
            ],
            [
                {
                    "action": {"type": "text", "label": "Пройти ещё раз"},
                    "color": "negative",
                },
            ],
        ],
    }
    vk_send_message(peer_id, text, attachment=attachment, keyboard=keyboard)

    if ADMIN_ID:
        try:
            vk_send_message(
                ADMIN_ID,
                f"Результат викторины\nID: {peer_id}\nЖивотное: {animal}\nScores: {scores}",
            )
        except Exception:
            logger.warning("Failed to notify admin")


async def handle_message(user_id: int, text: str, payload: str = None):
    state = storage.get_user_state(user_id)
    t = (text or "").strip()
    if not t and not payload:
        return

    low = t.lower()

    # Команды
    if low in ("/start", "начать", "старт", "привет"):
        state["current_q"] = 0
        state["scores"] = {}
        state["finished"] = False
        storage.save_user_state(state)
        vk_send_message(
            user_id,
            "Привет! 🦁 Это бот-викторина Московского зоопарка.\n"
            "Ответь на несколько вопросов и узнай своё тотемное животное!\n\n"
            "Команды:\n/start — начать\n/restart — заново\n/feedback текст — отзыв",
        )
        await asyncio.sleep(0.15)
        await send_question(user_id, 0)
        return

    if low in ("/restart", "ещё раз", "пройти ещё раз", "заново"):
        state["current_q"] = 0
        state["scores"] = {}
        state["finished"] = False
        storage.save_user_state(state)
        vk_send_message(user_id, "🔄 Начинаем заново!")
        await asyncio.sleep(0.15)
        await send_question(user_id, 0)
        return

    if low.startswith("/feedback"):
        fb_text = t[len("/feedback"):].strip() or "Спасибо"
        storage.append_feedback(user_id, fb_text)
        vk_send_message(user_id, "Спасибо за отзыв! 🙏")
        if ADMIN_ID:
            try:
                vk_send_message(ADMIN_ID, f"Отзыв от {user_id}:\n{fb_text}")
            except Exception:
                pass
        return

    if low in ("/help", "помощь"):
        vk_send_message(
            user_id,
            "Команды бота:\n"
            "/start — начать викторину\n"
            "/restart — пройти ещё раз\n"
            "/feedback ваш текст — оставить отзыв\n"
            "/help — эта справка",
        )
        return

    if low == "узнать больше":
        message = "Программа опеки помогает Московскому зоопарку заботиться о животных."
        if CARE_PROGRAM_URL:
            message += f"\n\nПодробнее: {CARE_PROGRAM_URL}"
        vk_send_message(user_id, message)
        return

    if low == "стать опекуном":
        message = "Спасибо за интерес к программе опеки! 💚"
        if CARE_PROGRAM_URL:
            message += f"\n\nОформить опеку: {CARE_PROGRAM_URL}"
        else:
            message += "\n(Администратор ещё не указал ссылку CARE_PROGRAM_URL)"
        vk_send_message(user_id, message)
        return

    # Уже закончил викторину
    if state.get("finished"):
        vk_send_message(user_id, "Викторина уже пройдена. Введите /restart, чтобы начать заново.")
        return

    q_idx = state.get("current_q", 0)
    if q_idx >= len(QUIZ["questions"]):
        vk_send_message(user_id, "Введите /restart.")
        return

    q = QUIZ["questions"][q_idx]
    selected = None

    # Сначала пробуем payload (если кнопка), потом текст
    button_text = None
    if payload:
        try:
            pl = json.loads(payload)
            button_text = pl.get("button")
        except Exception:
            pass

    search = (button_text or t).strip().lower()
    for opt in q["options"]:
        if opt["text"].strip().lower() == search:
            selected = opt
            break

    if not selected:
        opts = "\n".join(f"• {o['text']}" for o in q["options"])
        vk_send_message(user_id, f"Пожалуйста, выберите один из вариантов:\n\n{opts}")
        return

    # Начисляем баллы
    scores = state.get("scores") or {}
    for animal, pts in selected.get("scores", {}).items():
        scores[animal] = scores.get(animal, 0) + int(pts)
    state["scores"] = scores
    state["current_q"] = q_idx + 1

    if state["current_q"] >= len(QUIZ["questions"]):
        state["finished"] = True
    storage.save_user_state(state)

    if state["finished"]:
        await send_result(user_id, state["scores"])
    else:
        await send_question(user_id, state["current_q"])


async def main():
    lp = LongPollServer(VK_GROUP_TOKEN, VK_API_VERSION, VK_GROUP_ID)
    try:
        lp.get_server()
    except Exception as e:
        logger.error(f"Не удалось инициализировать LongPoll: {e}")
        return

    logger.info("Бот запущен и слушает сообщения...")
    while True:
        updates = lp.poll()
        for upd in updates:
            try:
                if not isinstance(upd, dict):
                    continue
                if upd.get("type") != "message_new":
                    continue

                obj = upd.get("object", {})
                message = obj.get("message") or obj  # совместимость
                peer_id = message.get("peer_id")
                from_id = message.get("from_id")
                text = message.get("text") or ""
                payload = message.get("payload")

                # Игнорируем сообщения от самого сообщества
                if from_id and from_id < 0:
                    continue
                if not peer_id:
                    continue

                await handle_message(peer_id, text, payload)
            except Exception:
                logger.exception("Ошибка при обработке обновления")
            await asyncio.sleep(0.05)


if __name__ == "__main__":
    try:
        storage._ensure_files_exist()
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")