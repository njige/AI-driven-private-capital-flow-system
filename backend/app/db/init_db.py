import asyncio
from app.db.database import engine, Base
from app.db.models import FilingRecord, OpenLineageAuditLog  # loads models into metadata

async def init_tables():
    async with engine.begin() as conn:
        print("Creating Layer 4 database tables in PostgreSQL...")
        await conn.run_sync(Base.metadata.create_all)
        print("Database tables created successfully!")

if __name__ == "__main__":
    asyncio.run(init_tables())