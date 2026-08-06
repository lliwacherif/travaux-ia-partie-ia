import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from app.core.config import settings

async def main():
    try:
        engine = create_async_engine(str(settings.DATABASE_URL))
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        
        async with async_session() as session:
            # Check packs
            result = await session.execute(text("SELECT pack_json FROM packs_travaux LIMIT 1"))
            row = result.fetchone()
            if row:
                import json
                print("=== PACK JSON SAMPLE ===")
                print(json.dumps(row[0], indent=2))
            else:
                print("No packs found in DB.")
                
            # Search for metal / wood
            print("\n=== SEARCHING FOR METAL / WOOD ===")
            res_metal = await session.execute(text("SELECT code_pack, nom_pack FROM packs_travaux WHERE nom_pack ILIKE '%métallique%' OR nom_pack ILIKE '%acier%' OR nom_pack ILIKE '%metal%'"))
            print("Metal:", res_metal.fetchall())
            
            res_wood = await session.execute(text("SELECT code_pack, nom_pack FROM packs_travaux WHERE nom_pack ILIKE '%bois%' OR nom_pack ILIKE '%ossature%'"))
            print("Wood:", res_wood.fetchall())
    except Exception as e:
        print("Error:", e)

asyncio.run(main())