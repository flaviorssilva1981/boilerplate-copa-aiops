import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sm = async_sessionmaker(engine)
    async with sm() as s:
        result = await s.execute(text(
            "DELETE FROM reports WHERE status='INCOMPLETO' AND coalesce(markdown,'')='' RETURNING id"
        ))
        deleted = result.fetchall()
        await s.commit()
        print(f"Deleted {len(deleted)} empty INCOMPLETO placeholder reports")
        r = await s.execute(text("SELECT status, COUNT(*) FROM reports GROUP BY status ORDER BY status"))
        print("Remaining:")
        for row in r.fetchall():
            print(f"  {row[0]}: {row[1]}")
    await engine.dispose()

asyncio.run(main())
