# storage.py - хранилище состояний и отзывов в JSON
import os
import json
import threading
from datetime import datetime, timezone

# Пути относительно папки скрипта
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATES_FILE = os.path.join(BASE_DIR, "states.json")
FEEDBACKS_FILE = os.path.join(BASE_DIR, "feedbacks.json")

_lock = threading.RLock()


def _atomic_write(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _ensure_files_exist():
    if not os.path.exists(STATES_FILE):
        _atomic_write(STATES_FILE, {})
    if not os.path.exists(FEEDBACKS_FILE):
        _atomic_write(FEEDBACKS_FILE, [])


def load_states() -> dict:
    _ensure_files_exist()
    with _lock:
        with open(STATES_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except (json.JSONDecodeError, ValueError):
                return {}


def save_states(states: dict):
    with _lock:
        _atomic_write(STATES_FILE, states)


def get_user_state(user_id: int) -> dict:
    uid = str(user_id)
    states = load_states()
    if uid in states:
        return states[uid]
    state = {
        "user_id": user_id,
        "current_q": 0,
        "scores": {},
        "finished": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    states[uid] = state
    save_states(states)
    return state


def save_user_state(state: dict):
    if "user_id" not in state:
        raise ValueError("state must contain user_id")
    uid = str(state["user_id"])
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    states = load_states()
    states[uid] = state
    save_states(states)


def load_feedbacks() -> list:
    _ensure_files_exist()
    with _lock:
        with open(FEEDBACKS_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except (json.JSONDecodeError, ValueError):
                return []


def append_feedback(user_id: int, text: str) -> dict:
    with _lock:
        feedbacks = load_feedbacks()
        next_id = (max((fb.get("id", 0) for fb in feedbacks), default=0) + 1) if feedbacks else 1
        rec = {
            "id": next_id,
            "user_id": user_id,
            "text": text,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        feedbacks.append(rec)
        _atomic_write(FEEDBACKS_FILE, feedbacks)
    return rec