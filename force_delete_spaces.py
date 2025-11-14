#!/usr/bin/env python3
import asyncio
import aiohttp
import os

# Current active space ID - DO NOT DELETE
ACTIVE_SPACE_ID = "!OppJhCXBkzgFjVxHLX:matrix.oculair.ca"

# All ghost space IDs to force delete
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
    "!ZZvweMMTgZMHirbiit:matrix.oculair.ca", "!iykHuCAvYcEvpLFcae:matrix.oculair.ca"
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

async def force_delete_room(admin_token, room_id):
    """Force delete a room using Synapse admin API with maximum force"""
    try:
        # First method: Admin delete API with purge
        url = f"http://synapse:8008/_synapse/admin/v1/rooms/{room_id}"
        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json"
        }
        
        delete_body = {
            "block": True,
            "purge": True,
            "force_purge": True
        }
        
        async with aiohttp.ClientSession() as session:
            # DELETE method
            async with session.delete(url, headers=headers, json=delete_body, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status in [200, 404]:
                    return "deleted_v1"
                result_text = await response.text()
                
                # Try POST method
                delete_url = f"{url}/delete"
                async with session.post(delete_url, headers=headers, json=delete_body, timeout=aiohttp.ClientTimeout(total=30)) as response2:
                    if response2.status in [200, 404]:
                        return "deleted_v2"
                    
                    return f"failed_{response2.status}"
        
    except Exception as e:
        return f"error: {str(e)[:50]}"

async def cleanup_all_spaces():
    """Delete all ghost spaces with maximum force"""
    
    admin_token = await get_admin_token()
    if not admin_token:
        print("❌ Failed to get admin token")
        return
    
    print(f"🔒 Active space (will keep): {ACTIVE_SPACE_ID}")
    print(f"🗑️  Force deleting {len(GHOST_SPACE_IDS)} ghost spaces...\n")
    
    deleted_count = 0
    failed_count = 0
    
    for i, space_id in enumerate(GHOST_SPACE_IDS, 1):
        if space_id == ACTIVE_SPACE_ID:
            print(f"[{i}/{len(GHOST_SPACE_IDS)}] ⏭️  SKIPPED (active): {space_id}")
            continue
            
        result = await force_delete_room(admin_token, space_id)
        
        if "deleted" in result:
            print(f"[{i}/{len(GHOST_SPACE_IDS)}] ✓ Deleted: {space_id}")
            deleted_count += 1
        else:
            print(f"[{i}/{len(GHOST_SPACE_IDS)}] ✗ {result}: {space_id}")
            failed_count += 1
        
        # Small delay to avoid overwhelming the server
        await asyncio.sleep(0.1)
    
    print(f"\n{'='*60}")
    print(f"✅ Successfully deleted: {deleted_count}")
    print(f"❌ Failed: {failed_count}")
    print(f"🔒 Active space preserved: {ACTIVE_SPACE_ID}")
    print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(cleanup_all_spaces())
