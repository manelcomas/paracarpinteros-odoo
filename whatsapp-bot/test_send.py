import asyncio, sys, json
sys.path.insert(0, "/app")
from main import send_wa_message

async def main():
    result = await send_wa_message("50686069717", "Test desde diagnostic — ignora")
    print(json.dumps(result, indent=2, ensure_ascii=False))

asyncio.run(main())
