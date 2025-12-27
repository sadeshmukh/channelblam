import aiohttp
import asyncio
import logging


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
