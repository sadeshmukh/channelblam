import asyncio
import logging
import os
import re

import aiohttp
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from db import (
    add_blam,
    add_whitelist,
    ensure_schema,
    get_client,
    list_blammed,
    list_whitelisted,
    remove_blam,
    get_idv_required_level,
    remove_whitelist,
    set_idv_required_level,
)

from idv import is_idved, is_idved_under18, user_is_bot

db_client = None  # main
ADMIN_ID = None
BOT_USER_ID = None


def _env(name: str) -> str:
    if not (value := os.getenv(name)):
        raise RuntimeError(f"Missing required env var: {name}")
    return value


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = AsyncApp(
    token=_env("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
)


def _db_client():
    if db_client is None:
        raise RuntimeError("Database client not initialized yet")
    return db_client


async def _is_channel_manager(channel_id: str, user_id: str | None, logger) -> bool:
    if not user_id:
        return False
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
        if user_id in members:
            return True

        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return False


_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{2,}$")


HELP_TEXT = (
    "CHANNELBLAM commands:\n"
    "- /blam @user — blam and kick a user.\n"
    "- /blam add @user — same as above.\n"
    "- /blam remove @user — unblam a user.\n"
    "- /blam list — list blammed users in this channel.\n"
    "- /blam idv [required|under18|off] — set IDV requirement.\n"
    "- /blam idv test [required|under18|off] — show how many would be kicked for that setting.\n"
    "- /blam whitelist @user — whitelist a user (exempt from blam/IDV).\n"
    "- /blam whitelist remove @user — remove a user from whitelist.\n"
    "- /blam whitelist channel — whitelist everyone currently in the channel.\n"
)


def _parse_mention(token: str) -> str | None:
    if not (token.startswith("<@") and token.endswith(">")):
        return None
    inner = token[2:-1]
    user_id = inner.split("|", 1)[0]
    if not _USER_ID_RE.match(user_id):
        return None
    return user_id


@app.command("/blam")
async def handle_blam(ack, respond, command, logger):
    await ack()
    channel_id = command.get("channel_id")

    # region Authorization check
    actor_id = command.get("user_id")
    cursor = None
    found = False

    while True:
        try:
            result = await app.client.conversations_members(
                channel=channel_id, cursor=cursor, limit=1000
            )
        except SlackApiError as exc:
            logger.error("Failed to fetch channel members", exc_info=exc)
            break

        if actor_id in result.get("members", []):
            found = True
            break
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    if actor_id != ADMIN_ID and not found:
        await respond("You are not authorized to use this command.")
        return

    # endregion

    text = (command.get("text") or "").strip()
    if not channel_id:
        await respond("Cannot determine channel.")
        return
    tokens = text.split()
    if not tokens:
        await respond(HELP_TEXT)
        return

    if channel_id.startswith("C"):
        try:
            await app.client.conversations_join(channel=channel_id)
        except SlackApiError as exc:
            logger.info(exc)
            if exc.response["error"] != "method_not_supported_for_channel_type":
                logger.warning("Failed to join channel", exc_info=exc)
                respond("Error joining channel.")
                return

    first = tokens[0].lower()

    if first in {"help", "usage"}:
        await respond(HELP_TEXT)
        return

    if first == "list":
        try:
            client = _db_client()
            blammed = await list_blammed(channel_id, client=client)
            if not blammed:
                await respond("No one is blammed in this channel.")
                return
            mentions = ", ".join(f"<@{user_id}>" for user_id in blammed)
            await respond(f"Blammed users: {mentions}")
        except Exception as exc:
            logger.error("Failed to list blammed", exc_info=exc)
            await respond("Error listing blammed users.")
        return
    if first == "idv":
        subcmd = tokens[1].lower() if len(tokens) > 1 else "required"

        if subcmd == "test":
            level = tokens[2].lower() if len(tokens) > 2 else "required"

            if level not in {"required", "under18", "off"}:
                await respond(
                    "Usage: /blam idv test [required/under18/off], defaults to required"
                )
                return

            match level:
                case "off":
                    levelnum = 0
                case "required":
                    levelnum = 1
                case "under18":
                    levelnum = 2
                case default:
                    levelnum = 1

            try:
                client = _db_client()
                users = []
                cursor = None
                while True:
                    result = await app.client.conversations_members(
                        channel=channel_id, cursor=cursor, limit=1000
                    )
                    members = result.get("members", []) or []
                    users.extend(members)
                    cursor = result.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break

                whitelisted = set(await list_whitelisted(channel_id, client=client))
                semaphore = asyncio.Semaphore(20)

                async def needs_kick(user_id: str) -> int:
                    if user_id == ADMIN_ID or user_id in whitelisted:
                        return 0
                    async with semaphore:
                        is_bot = await user_is_bot(user_id, app.client, logger)
                    if is_bot or levelnum == 0:
                        return 0
                    if levelnum == 1:
                        async with semaphore:
                            return 1 if not await is_idved(user_id, logger) else 0
                    async with semaphore:
                        return 1 if not await is_idved_under18(user_id, logger) else 0

                kick_flags = await asyncio.gather(
                    *(needs_kick(user_id) for user_id in users)
                )
                to_kick = sum(kick_flags)

                await respond(
                    f"{to_kick} users would be kicked if IDV requirement were set to {level}."
                )
            except Exception as exc:
                logger.error("Failed to test IDV requirement", exc_info=exc)
                await respond("Error testing IDV requirement.")

            return

        level = subcmd

        if level not in {"required", "under18", "off"}:
            await respond(
                "Usage: /blam idv [required/under18/off], defaults to required"
            )
            return

        match level:
            case "off":
                levelnum = 0
            case "required":
                levelnum = 1
            case "under18":
                levelnum = 2
            case default:
                levelnum = 1

        old_level = await get_idv_required_level(channel_id, client=_db_client()) or 0
        if old_level == levelnum:
            await respond(
                f"IDV requirement is already set to {level} for this channel."
            )
            return

        try:
            await set_idv_required_level(channel_id, levelnum, client=_db_client())
            await respond(f"Set IDV requirement to {level} for this channel.")
        except Exception as exc:
            logger.error("Failed to set IDV requirement", exc_info=exc)
            await respond("Error setting IDV requirement.")

        if old_level == 0 and levelnum > 0:
            # newly enabled, kick blammed users
            try:
                client = _db_client()
                toblam = []
                # list channel
                users = []
                cursor = None
                while True:
                    result = await app.client.conversations_members(
                        channel=channel_id, cursor=cursor, limit=1000
                    )
                    members = result.get("members", []) or []
                    users.extend(members)
                    cursor = result.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break
                for user_id in users:
                    is_bot = await user_is_bot(user_id, app.client, logger)
                    if is_bot:
                        continue
                    is_whitelisted = await list_whitelisted(channel_id, client=client)
                    if user_id in is_whitelisted:
                        continue
                    if user_id == ADMIN_ID:
                        continue
                    if levelnum == 1 and not await is_idved(user_id, logger):
                        toblam.append(user_id)
                        continue
                    if levelnum == 2 and not await is_idved_under18(user_id, logger):
                        toblam.append(user_id)
                        continue
                # for user_id in toblam:
                #     await _kick_if_possible(channel_id, user_id, logger)
                await asyncio.gather(
                    *(
                        _kick_if_possible(channel_id, user_id, logger)
                        for user_id in toblam
                    )
                )
                logger.info(
                    f"Kicked {len(toblam)} blammed users from channel {channel_id} due to IDV requirement change"
                )
            except Exception as exc:
                logger.error("Failed to kick blammed users on IDV change", exc_info=exc)

        return
    if first == "whitelist":
        if len(tokens) < 2:
            await respond(
                "Usage: /blam whitelist @user | /blam whitelist remove @user | /blam whitelist channel"
            )
            return
        second = tokens[1].lower() if len(tokens) > 1 else ""
        if second == "channel":
            # mark all users in channel as whitelisted
            try:
                client = _db_client()
                users = []
                cursor = None
                while True:
                    result = await app.client.conversations_members(
                        channel=channel_id, cursor=cursor, limit=1000
                    )
                    members = result.get("members", []) or []
                    users.extend(members)
                    cursor = result.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break
                for user_id in users:
                    await remove_blam(channel_id, user_id, client=client)
                    await add_whitelist(channel_id, user_id, client=client)
                await respond(f"Whitelisted all users currently in the channel.")
            except Exception as exc:
                logger.error("Failed to whitelist channel", exc_info=exc)
                await respond("Error whitelisting channel.")
            return

        if second == "remove":
            # remove whitelist for given user
            if len(tokens) < 3:
                await respond(
                    "Please mention a user, e.g., /blam whitelist remove @user"
                )
                return
            user_id = tokens[2].split("|", 1)[0] if len(tokens) > 2 else ""

            if not _USER_ID_RE.match(user_id):
                await respond(
                    "Please mention a user, e.g., /blam whitelist remove @user"
                )
                return
            try:
                client = _db_client()
                await remove_whitelist(channel_id, user_id, client=client)
                await respond(f"Removed whitelist for <@{user_id}> in this channel.")
            except Exception as exc:
                logger.error("Failed to remove whitelist", exc_info=exc)
                await respond("Error removing whitelist.")
        user_id = second.split("|", 1)[0] if len(tokens) > 1 else ""
        if not _USER_ID_RE.match(user_id):
            await respond("Please mention a user, e.g., /blam whitelist @user")
            return
        try:
            client = _db_client()
            await remove_blam(channel_id, user_id, client=client)
            await add_whitelist(channel_id, user_id, client=client)
            await respond(f"Whitelisted <@{user_id}> in this channel.")
        except Exception as exc:
            logger.error("Failed to whitelist user", exc_info=exc)
            await respond("Error whitelisting user.")
        return

    action = "add"
    mention_token_idx = 0

    if first in {"add", "remove"}:  # /blam [add/remove] ...
        action = first
        mention_token_idx = 1
    if len(tokens) <= mention_token_idx:  # /blam [add/remove]
        await respond("Please mention a user, e.g., /blam @user")
        return
    target_user = _parse_mention(tokens[mention_token_idx])
    if not target_user:
        await respond("Please mention a user, e.g., /blam @user")
        return

    if action == "remove":
        try:
            client = _db_client()
            await remove_blam(channel_id, target_user, client=client)
            await respond(f"Unblammed <@{target_user}> in this channel.")
        except Exception as exc:
            logger.error("Failed to remove blam", exc_info=exc)
            await respond("Error removing blam.")
        return

    try:
        client = _db_client()
        await add_blam(channel_id, target_user, client=client)
        await _kick_if_possible(channel_id, target_user, logger)
        await respond(f"Blammed <@{target_user}> in this channel.")
    except Exception as exc:
        logger.error("Failed to blam", exc_info=exc)
        await respond("Error blamming.")


@app.event("member_joined_channel")
async def handle_member_joined_channel(body, say, logger):
    event = body.get("event", {})
    user_id = event.get("user")
    channel_id = event.get("channel")
    client = _db_client()
    # remove perms only on the authed user, so we've got to do the whole invite shenanigans
    if user_id == body.get("authorizations", [{}])[0].get("user_id"):  # self check
        try:
            await AsyncWebClient(token=_env("SLACK_BOT_TOKEN")).conversations_invite(
                channel=channel_id, users=str(ADMIN_ID)
            )
        except SlackApiError as exc:
            logger.warning("Failed to invite admin to channel", exc_info=exc)

    # TODO: send message to channel if channel is invitelocked

    whitelisted = await list_whitelisted(channel_id, client=client)
    if user_id in whitelisted:
        return

    blam_ok = user_id == ADMIN_ID or not user_id in await list_blammed(
        channel_id, client=client
    )
    # idv
    idv_ok = True
    if (
        blam_ok
        and (level := await get_idv_required_level(channel_id, client=client))
        and level > 0
    ):
        logger.info("hi")
        is_bot = await user_is_bot(user_id, app.client, logger)
        if is_bot:
            logger.info("skipping kick for bot")
            return
        if level == 1:
            idv_ok = await is_idved(user_id, logger)
        elif level == 2:
            idv_ok = await is_idved_under18(user_id, logger)

    if blam_ok and idv_ok:
        return
    try:
        await _kick_if_possible(channel_id, user_id, logger)
        logger.info(f"Kicked blammed user {user_id} from channel {channel_id} on join")
    except Exception as exc:
        logger.error("Failed to kick blammed user", exc_info=exc)


async def _kick_if_possible(channel_id: str, user_id: str, logger) -> None:
    try:
        await _kick_xoxc(channel_id, user_id, logger)
        return
    except Exception as exc:
        logger.warning("Kick xoxc failed", exc_info=exc)

    try:
        await AsyncWebClient(
            token=os.getenv("SLACK_PERSONAL_TOKEN")
        ).conversations_kick(channel=channel_id, user=user_id)
    except SlackApiError as exc:
        if exc.response.get("error") == "not_in_channel":
            return
        logger.warning("Kick failed", exc_info=exc)


async def _kick_xoxc(channel_id: str, user_id: str, logger) -> None:
    token_xoxc = _env("SLACK_XOXC")
    url = "https://hackclub.enterprise.slack.com/api/conversations.kick?slack_route=E09V59WQY1E%3AE09V59WQY1E"
    async with aiohttp.ClientSession() as session:
        formdata = {"channel": channel_id, "user": user_id, "token": token_xoxc}
        cookie = f"d={_env('SLACK_XOXD').replace('%2F', '/').replace('%3D', '=')};"
        headers = {"Cookie": cookie}
        session.headers.update(headers)
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                logger.warning(f"Kick xoxc failed: {data}")


async def _invite_user(
    channel_id: str, user_id: str, logger, *, token: str | None = None
):
    try:
        token_to_use = token or _env(
            "SLACK_BOT_TOKEN"
        )  # only for invites! when kicking, it has to use personal token
        client = AsyncWebClient(token=token_to_use)
        await client.conversations_invite(channel=channel_id, users=str(user_id))
    except SlackApiError as exc:
        if exc.response.get("error") == "already_in_channel":
            return
        logger.warning("Invite failed", exc_info=exc)


async def _invite_bot(channel_id: str, logger) -> None:
    admin_token = _env("SLACK_PERSONAL_TOKEN")
    await _invite_user(channel_id, str(BOT_USER_ID), logger, token=admin_token)


async def _resolve_bot_user_id(logger) -> str:
    try:
        auth_info = await app.client.auth_test()
        user_id = auth_info.get("user_id")
        if not user_id:  # otherwise linter complains
            raise Exception("Unable to resolve bot user id")
        logger.info(f"Bot user id resolved as {user_id}")
        return user_id
    except SlackApiError as exc:
        logger.error("auth_test failed", exc_info=exc)
        raise


async def _start_socket_mode():
    handler = AsyncSocketModeHandler(app, _env("SLACK_APP_TOKEN"))
    await handler.start_async()


@app.event("member_left_channel")
async def handle_member_left_channel(body, logger):
    event = body.get("event", {})
    channel_id = event.get("channel")
    user_id = event.get("user")
    actor_id = event.get("actor_id")
    if not channel_id or not user_id:
        return

    if await _is_channel_manager(channel_id, actor_id, logger):
        return

    if BOT_USER_ID and user_id == BOT_USER_ID:
        await _invite_bot(channel_id, logger)
        return

    if user_id == ADMIN_ID:
        await _invite_user(channel_id, str(ADMIN_ID), logger)


async def main() -> None:
    global db_client
    global ADMIN_ID
    global BOT_USER_ID
    db_client = get_client()
    ADMIN_ID = str(_env("ADMIN_ID"))
    BOT_USER_ID = str(await _resolve_bot_user_id(logging.getLogger(__name__)))
    await ensure_schema(db_client)
    await asyncio.gather(_start_socket_mode())


if __name__ == "__main__":
    asyncio.run(main())
