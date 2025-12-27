import aiohttp
import asyncio
import logging
import os
import cachetools
from cachetools.keys import hashkey


_idv_cache = cachetools.TTLCache(maxsize=1024, ttl=300)


# thank you random internet person
def async_cached(cache):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            key = hashkey(*args, **kwargs)
            try:
                return cache[key]
            except KeyError:
                result = await func(*args, **kwargs)
                cache[key] = result
                return result

        return wrapper

    return decorator


@async_cached(_idv_cache)
async def idvstatus(userid: str, logger) -> str:
    IDV_ENDPOINT = "https://identity.hackclub.com/api/external/check"
    async with aiohttp.ClientSession() as session:
        params = {"slack_id": userid}
        async with session.get(IDV_ENDPOINT, params=params) as response:
            if response.status != 200:
                logger.error("IDV request failed", response)
            id_data = await response.json()
            return id_data.get("result", None)


async def is_idved(userid: str, logger) -> bool:
    return await idvstatus(userid, logger) in [
        "verified_eligible",
        "verified_but_over_18",
    ]


async def is_idved_under18(userid: str, logger) -> bool:
    return await idvstatus(userid, logger) == "verified_eligible"


botcache = set()
usercache = set()


async def user_is_bot(userid: str, client, logger) -> bool:
    if userid in botcache:
        return True
    if userid in usercache:
        return False

    async def _user_is_bot_xoxc() -> bool | None:
        token_xoxc = os.getenv("SLACK_XOXC")
        xoxd_raw = os.getenv("SLACK_XOXD")
        if not token_xoxc or not xoxd_raw:
            return None
        cookie = f"d={xoxd_raw.replace('%2F', '/').replace('%3D', '=')};"
        url = "https://slack.com/api/users.info"
        async with aiohttp.ClientSession(headers={"Cookie": cookie}) as session:
            try:
                async with session.post(
                    url, data={"user": userid, "token": token_xoxc}
                ) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        logger.warning(
                            "users.info xoxc failed", extra={"error": data.get("error")}
                        )
                        return None
                    return bool(data.get("user", {}).get("is_bot", False))
            except Exception as exc:
                logger.warning("users.info xoxc exception", exc_info=exc)
                return None

    try:
        is_bot_xoxc = await _user_is_bot_xoxc()
        if is_bot_xoxc is None:
            userinfo = await client.users_info(user=userid)
            is_bot = userinfo.get("user", {}).get("is_bot", False)
        else:
            is_bot = is_bot_xoxc
        if is_bot:
            botcache.add(userid)
        else:
            usercache.add(userid)
        return is_bot
    except Exception as e:
        logger.error(f"Error fetching user info for {userid}: {e}")
        return False
