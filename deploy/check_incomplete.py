import asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

async def main():
    engine = create_async_engine("postgresql+asyncpg://aiops:aiops123@postgres:5432/aiops_k8s")
    async with AsyncSession(engine) as s:
        r = await s.execute(sa.text(
            "SELECT id, created_at, array_length(event_uids,1) as nevents, left(markdown,500) "
            "FROM reports WHERE status='INCOMPLETO' ORDER BY created_at DESC"
        ))
        rows = r.all()
        print(f"Total INCOMPLETO: {len(rows)}")
        for row in rows:
            print("---")
            print(f"ID: {row[0]}  |  created: {row[1]}  |  events: {row[2]}")
            print(f"markdown: {repr(row[3])}")

asyncio.run(main())
