# utils.py - VK API утилиты
import os
import json
import random
import requests
from dotenv import load_dotenv

load_dotenv()

VK_TOKEN = os.getenv("VK_GROUP_TOKEN")
API_VERSION = os.getenv("VK_API_VERSION", "5.199")
REQUEST_TIMEOUT = 15


def _vk_call(method: str, params: dict = None):
    url = f"https://api.vk.com/method/{method}"
    data = {
        "access_token": VK_TOKEN,
        "v": API_VERSION,
    }
    if params:
        data.update(params)
    r = requests.post(url, data=data, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    res = r.json()
    if "error" in res:
        raise Exception(res["error"])
    return res.get("response")


def vk_send_message(peer_id: int, text: str, attachment: str = None, keyboard: dict = None):
    params = {
        "peer_id": peer_id,
        "message": text,
        "random_id": random.randint(1, 2**31 - 1),
    }
    if attachment:
        params["attachment"] = attachment
    if keyboard:
        params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
    return _vk_call("messages.send", params)


def vk_get_messages_upload_server(peer_id: int):
    return _vk_call("photos.getMessagesUploadServer", {"peer_id": peer_id})


def vk_save_messages_photo(photo, server, hash_):
    return _vk_call("photos.saveMessagesPhoto", {
        "photo": photo,
        "server": server,
        "hash": hash_,
    })


def vk_upload_photo_and_get_attachment(photo_path: str, peer_id: int) -> str:
    upload_info = vk_get_messages_upload_server(peer_id)
    upload_url = upload_info["upload_url"]
    with open(photo_path, "rb") as f:
        r = requests.post(upload_url, files={"photo": f}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    saved = vk_save_messages_photo(data["photo"], data["server"], data["hash"])
    item = saved[0]
    att = f"photo{item['owner_id']}_{item['id']}"
    if item.get("access_key"):
        att = f"{att}_{item['access_key']}"
    return att


def make_keyboard(options: list, one_time: bool = True) -> dict:
    """Вертикальная клавиатура из текстовых кнопок."""
    buttons = []
    for opt in options:
        buttons.append([{
            "action": {
                "type": "text",
                "label": opt[:40],  # VK limit
                "payload": json.dumps({"button": opt}, ensure_ascii=False),
            },
            "color": "secondary",
        }])
    return {
        "one_time": one_time,
        "buttons": buttons,
    }