import asyncio
import logging
import os
import re

from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from db import (
    add_blam,
    ensure_schema,
    get_client,
    # get_user_token,
    list_blammed,
    remove_blam,
)

db_client = None  # main
ADMIN_ID = None


def _require_env(name: str) -> str:
    if not (value := os.getenv(name)):
        raise RuntimeError(f"Missing required env var: {name}")
    return value


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = AsyncApp(
    token=_require_env("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
)


def _db_client():
    if db_client is None:
        raise RuntimeError("Database client not initialized yet")
    return db_client


_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{2,}$")


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

    text = (command.get("text") or "").strip()
    if not channel_id:
        await respond("Cannot determine channel.")
        return
    tokens = text.split()
    if not tokens:
        await respond("Usage: /blam @user | /blam [add/remove] @user | /blam list")
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
        await add_blam(channel_id, target_user, blammed_by=actor_id, client=client)
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
            await AsyncWebClient(
                token=_require_env("SLACK_BOT_TOKEN")
            ).conversations_invite(channel=channel_id, users=str(ADMIN_ID))
        except SlackApiError as exc:
            logger.warning("Failed to invite admin to channel", exc_info=exc)

    if user_id == ADMIN_ID or not user_id in await list_blammed(
        channel_id, client=client
    ):
        return
    try:
        await _kick_if_possible(channel_id, user_id, logger)
        logger.info(f"Kicked blammed user {user_id} from channel {channel_id} on join")
    except Exception as exc:
        logger.error("Failed to kick blammed user", exc_info=exc)


async def _kick_if_possible(channel_id: str, user_id: str, logger) -> None:
    try:
        token = None
        # token = await get_user_token(client=_db_client())
        fallback_token = os.getenv("SLACK_PERSONAL_TOKEN") or _require_env(
            "SLACK_BOT_TOKEN"
        )
        client = AsyncWebClient(token=token or fallback_token)
        await client.conversations_kick(channel=channel_id, user=user_id)
    except SlackApiError as exc:
        logger.warning("Kick failed", exc_info=exc)


async def _start_socket_mode():
    handler = AsyncSocketModeHandler(app, _require_env("SLACK_APP_TOKEN"))
    await handler.start_async()


async def main() -> None:
    global db_client
    global ADMIN_ID
    db_client = get_client()
    ADMIN_ID = _require_env("ADMIN_ID")
    await ensure_schema(db_client)
    await asyncio.gather(_start_socket_mode())


if __name__ == "__main__":
    asyncio.run(main())
