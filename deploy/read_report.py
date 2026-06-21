import asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

async def main():
    engine = create_async_engine("postgresql+asyncpg://aiops:aiops123@postgres:5432/aiops_k8s")
    async with AsyncSession(engine) as s:
        r = await s.execute(sa.text(
            "SELECT markdown, status FROM reports WHERE id=cast('2aa2de6f-7753-44ae-a0a0-21160e284b32' as uuid)"
        ))
        row = r.one()
        print("STATUS:", row.status)
        print("--- LAST 4000 CHARS ---")
        print((row.markdown or "empty")[-4000:])

asyncio.run(main())
