import aiohttp
import asyncio
import logging
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


async def idv_notfound(userid: str, logger) -> bool:
    return await idvstatus(userid, logger) == "not_found"
