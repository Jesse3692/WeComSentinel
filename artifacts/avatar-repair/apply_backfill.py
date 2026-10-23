import asyncio,json
from scripts.backfill_avatars import run
from app.config import Settings
print(json.dumps(asyncio.run(run(Settings(),True)),ensure_ascii=False))
