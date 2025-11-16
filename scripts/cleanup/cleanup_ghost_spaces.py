#!/usr/bin/env python3
import asyncio
import aiohttp
import os

# Current active space ID
ACTIVE_SPACE_ID = "!OppJhCXBkzgFjVxHLX:matrix.oculair.ca"

# All ghost space IDs
GHOST_SPACE_IDS = [
    "!AoIjigHTTfPWTNKrOK:matrix.oculair.ca", "!arEWlYHbHZlmgUmwbR:matrix.oculair.ca",
    "!BFYEPFJdLgCDnwqEUK:matrix.oculair.ca", "!ddnMuqHTZpHHvUVFfS:matrix.oculair.ca",
    "!dXOVpxaThGAFbvIDyH:matrix.oculair.ca", "!EtpWwHrSBvZYcLLzTh:matrix.oculair.ca",
    "!FaacCDNFQQigubBWDm:matrix.oculair.ca", "!gCEosidiGeSdnEqazu:matrix.oculair.ca",
    "!gxkoVnbWuoHeLWzdnF:matrix.oculair.ca", "!gyfRNfXmQDfkvxvXJY:matrix.oculair.ca",
    "!hLSgncSAMgWUJFHUhC:matrix.oculair.ca", "!hOKckOCvCcYvsBqmJl:matrix.oculair.ca",
    "!hszSjxkMoSLujIXyAQ:matrix.oculair.ca", "!hUxOoNuryuuAPQRjoX:matrix.oculair.ca",
    "!hZzRaUsLMupBEVgzWK:matrix.oculair.ca", "!IACWyzigFWrKQkAJku:matrix.oculair.ca",
    "!iJAtwtXQDfWCTIyekO:matrix.oculair.ca", "!IOrjcPNXrgFMdIUbfH:matrix.oculair.ca",
    "!jCDQCqnXozRQihnbOD:matrix.oculair.ca", "!lgkBsdLMKPFCgZoVqD:matrix.oculair.ca",
    "!mmxJvXmOfGJQncqwed:matrix.oculair.ca", "!ndtWeLILjQNgEvRwiM:matrix.oculair.ca",
    "!pTcqFhlYlXPKwwBopy:matrix.oculair.ca", "!PufBlkHtNVOOPmwKCM:matrix.oculair.ca",
    "!qdKwMHNQACpssSwLKb:matrix.oculair.ca", "!QkKZEERXoSlDqYniFk:matrix.oculair.ca",
    "!qudnCuvIRhzbWoIoUT:matrix.oculair.ca", "!qVlFtqbcvCAAILbdPB:matrix.oculair.ca",
    "!rgmdPFnqOHeTMsacGi:matrix.oculair.ca", "!rKNbEjUtLhKDWasZru:matrix.oculair.ca",
    "!rMLRvYAKcXFFbuthtO:matrix.oculair.ca", "!rTpCAWJhioGoMgNtky:matrix.oculair.ca",
    "!SKKohBHlplDNUXlNhk:matrix.oculair.ca", "!tfDNdwpmVwSKTrJLnS:matrix.oculair.ca",
    "!tHdVFIXULpApiDYWWz:matrix.oculair.ca", "!tzpktdhLGwdkFCseDF:matrix.oculair.ca",
    "!uoseVapHsdvuRpEyxm:matrix.oculair.ca", "!UvIMnZvwMnbgmODZyZ:matrix.oculair.ca",
    "!vCxicLkqfxErZWOdBl:matrix.oculair.ca", "!VIclMDGRIOrqGTEBSj:matrix.oculair.ca",
    "!ViOoXWweoLHNMCkHom:matrix.oculair.ca", "!VtlrKDMEUPmjHvTdAZ:matrix.oculair.ca",
    "!vuUkimZNbQhOzNxQrf:matrix.oculair.ca", "!wFAXvcDppoAnBDfUam:matrix.oculair.ca",
    "!wIsKeZvMTbkGGiiygV:matrix.oculair.ca", "!wqUgedKSedWeOAALcQ:matrix.oculair.ca",
    "!wsIVmTDIwVwiLNJylU:matrix.oculair.ca", "!wURwuoAGjZBiZWDJcz:matrix.oculair.ca",
    "!wzNtKKizVsAecrRubb:matrix.oculair.ca", "!xqEocoSIfGsbUKhiJJ:matrix.oculair.ca",
    "!ZFcgAeVDYpohAhjFBK:matrix.oculair.ca", "!zYIwAIjDsaIdvwxCBQ:matrix.oculair.ca",
    "!ZZvweMMTgZMHirbiit:matrix.oculair.ca"
]

async def get_admin_token():
    """Get admin token"""
    login_url = "http://synapse:8008/_matrix/client/r0/login"
    login_data = {
        "type": "m.login.password",
        "user": "matrixadmin",
        "password": os.getenv("MATRIX_ADMIN_PASSWORD", "admin123")
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(login_url, json=login_data, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("access_token")
    return None

async def cleanup_ghost_spaces():
    """Delete all ghost spaces"""
    
    admin_token = await get_admin_token()
    if not admin_token:
        print("Failed to get admin token")
        return
    
    deleted_count = 0
    failed_count = 0
    
    print(f"Active space (will keep): {ACTIVE_SPACE_ID}")
    print(f"Cleaning up {len(GHOST_SPACE_IDS)} ghost spaces...\n")
    
    for i, space_id in enumerate(GHOST_SPACE_IDS, 1):
        try:
            url = f"http://synapse:8008/_synapse/admin/v1/rooms/{space_id}/delete"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            
            data = {"block": False, "purge": True}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status in [200, 404]:
                        status = "✓ Deleted" if response.status == 200 else "✓ Already gone"
                        print(f"[{i}/{len(GHOST_SPACE_IDS)}] {status}: {space_id}")
                        deleted_count += 1
                    else:
                        print(f"[{i}/{len(GHOST_SPACE_IDS)}] ✗ Failed ({response.status}): {space_id}")
                        failed_count += 1
        
        except Exception as e:
            print(f"[{i}/{len(GHOST_SPACE_IDS)}] ✗ Error: {space_id}")
            failed_count += 1
    
    print(f"\n{'='*50}")
    print(f"Summary: {deleted_count} cleaned, {failed_count} failed")
    print(f"{'='*50}")

if __name__ == "__main__":
    asyncio.run(cleanup_ghost_spaces())
