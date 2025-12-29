import json
import aiohttp
import os
import re
import logging
from slack_sdk.errors import SlackApiError

logger = logging.getLogger("channelblam.utils")
logger.setLevel(logging.INFO)

_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{2,}$")


def _env(name: str) -> str:
    if not (value := os.getenv(name)):
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _cookie_header() -> str:
    xoxd = _env("SLACK_XOXD").replace("%2F", "/").replace("%3D", "=")
    cookie = f"d={xoxd};"
    if extra := os.getenv("SLACK_X_COOKIE"):  # testing material, not necessary
        cookie = f"{cookie} x={extra};"
    return cookie


XOXC_TOKEN = _env("SLACK_XOXC")
HEADERS = {
    "Cookie": _cookie_header(),
    "Origin": "https://app.slack.com",
    "Referer": "https://app.slack.com/client",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Accept": "*/*",
}
ADMIN_ID = _env("ADMIN_ID")


async def _is_channel_manager(channel_id: str, user_id: str | None, app) -> bool:
    return user_id in await _list_channel_managers(channel_id, app)


async def _list_channel_managers(channel_id: str, app) -> list[str]:
    managers = []
    cursor = None
    while True:
        try:
            result = await app.client.conversations_members(
                channel=channel_id,
                cursor=cursor,
                limit=1000,  # this limit is quite meaningless
            )
        except SlackApiError as exc:
            logger.warning("Failed to fetch channel managers", exc_info=exc)
            break

        members = result.get("members", []) or []
        managers.extend(members)

        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return managers


async def _channel_post_managers(channel_id: str, app) -> None:
    users: list[str] = await _list_channel_managers(channel_id, app=app)
    await _allow_channel_post(channel_id, users)


def _parse_mention(token: str) -> str | None:
    if not (token.startswith("<@") and token.endswith(">")):
        return None
    inner = token[2:-1]
    user_id = inner.split("|", 1)[0]
    if not _USER_ID_RE.match(user_id):
        return None
    return user_id


def _is_valid_userid(userid: str) -> bool:
    return bool(_USER_ID_RE.match(userid))


async def _allow_channel_post(channel_id: str, add_user_ids: list[str]) -> None:
    users: list[str] = []
    # separate fields (channel pings) need a separate request. let me know if it's actually necessary to preserve channel ping permissions for smoe reason
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = "https://hackclub.enterprise.slack.com/api/channels.prefs.get?slack_route=E09V59WQY1E%3AE09V59WQY1E"
        formdata = {
            "token": XOXC_TOKEN,
            "channel_id": channel_id,
            "pref_name": "who_can_post",
        }
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(
                    f"channels.prefs.get failed: {data.get('error', '??')}"
                )
            pref = data.get("pref_value", {})
            users = pref.get("user", [])  # it's user without an s for some reason

    users.extend(add_user_ids)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = "https://hackclub.enterprise.slack.com/api/channels.prefs.set?slack_route=E09V59WQY1E%3AE09V59WQY1E"
        prefs = {
            "who_can_post": "type:admin,user:" + ",user:".join(users),
            "can_thread": "type:admin,user:" + ",user:".join(users),
            # TODO: experiment with this ^^ this seems promising for future features
            "enable_at_here": "true",
            "enable_at_channel": "true",
        }
        formdata = {
            "token": XOXC_TOKEN,
            "channel_id": channel_id,
            "prefs": json.dumps(prefs),
        }
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(
                    f"channels.prefs.set failed: {data.get('error', '??')}"
                )


async def _prevent_channel_post(
    channel_id: str, remove_user_ids: list[str], app
) -> None:
    users: list[str] = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = "https://hackclub.enterprise.slack.com/api/channels.prefs.get?slack_route=E09V59WQY1E%3AE09V59WQY1E"
        formdata = {
            "token": XOXC_TOKEN,
            "channel_id": channel_id,
            "pref_name": "who_can_post",
        }
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(
                    f"channels.prefs.get failed: {data.get('error', '??')}"
                )
            pref = data.get("pref_value", {})
            users = pref.get("user", [])

    olen = len(users)
    users = [u for u in users if u not in remove_user_ids]
    if len(users) == olen and olen > 0:
        logger.warning(
            "User not in current posting perms, not updating",
            extra={"channel_id": channel_id, "remove_user_ids": remove_user_ids},
        )
        return

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = "https://hackclub.enterprise.slack.com/api/channels.prefs.set?slack_route=E09V59WQY1E%3AE09V59WQY1E"
        prefs = {
            "who_can_post": (
                "type:admin,user:" + ",user:".join(users) if users else "type:admin"
            ),
            "can_thread": (
                "type:admin,user:" + ",user:".join(users) if users else "type:admin"
            ),
            "enable_at_here": "true",
            "enable_at_channel": "true",
        }
        formdata = {
            "token": XOXC_TOKEN,
            "channel_id": channel_id,
            "prefs": json.dumps(prefs),
        }
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(
                    f"channels.prefs.get failed: {data.get('error', '??')}"
                )
            pref = data.get("pref_value", {})
            users = pref.get("user", [])


async def _initialize_channel_post(channel_id: str, app) -> None:
    users: list[str] = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = "https://hackclub.enterprise.slack.com/api/conversations.members?slack_route=E09V59WQY1E%3AE09V59WQY1E"
        formdata = {
            "token": XOXC_TOKEN,
            "channel": channel_id,
        }
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(
                    f"conversations.members failed: {data.get('error', '??')}"
                )
            users = data.get("members", [])
    await _allow_channel_post(channel_id, users)
