"""Conservative parsing of DZMM group invitation links and RPC responses."""
import re
from urllib.parse import urlsplit


INVITE_PATH = re.compile(r"^/invite/([A-Za-z0-9]+)(?:/)?$")
INVITE_URL = re.compile(r"https://www\.dzmm\.io/invite/[A-Za-z0-9]+/?(?=$|[\s<>)\]\}\"'])")


def invite_code(text):
    if not isinstance(text, str):
        return None
    for match in INVITE_URL.finditer(text):
        url = urlsplit(match.group(0))
        if url.scheme == "https" and url.netloc == "www.dzmm.io":
            path = INVITE_PATH.fullmatch(url.path)
            if path:
                return path.group(1)
    return None


def invite_card_content(content):
    """Return the confirmed DZMM share-card content shape only."""
    if not isinstance(content, dict) or content.get("type") != "share" or content.get("shareType") != "group_invite":
        return None
    code = content.get("resourceId")
    if not isinstance(code, str) or not 0 < len(code) <= 100 or not code.isascii() or not code.isalnum():
        return None
    return {"type": "share", "shareType": "group_invite", "resourceId": code}


def rpc_json(body):
    """Unwrap the observed tRPC result.data.json envelope; fail closed."""
    if not isinstance(body, dict):
        return None
    result = body.get("result")
    data = result.get("data") if isinstance(result, dict) else None
    return data.get("json") if isinstance(data, dict) else None


def private_room_ids(body):
    """Accept only chat.listAll user/one_on_one entries, never group rooms."""
    value = rpc_json(body)
    if isinstance(value, dict):
        value = value.get("chats")
    if not isinstance(value, list):
        return set()
    rooms = set()
    for entry in value:
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        data = entry.get("data")
        if isinstance(data, dict) and data.get("chatType") == "one_on_one":
            room = data.get("chatroomId")
            if isinstance(room, str) and 0 < len(room) <= 200:
                rooms.add(room)
    return rooms
