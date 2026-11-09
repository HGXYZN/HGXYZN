import asyncio
import os
from pathlib import Path
from office import Station

# Imposta la cartella degli asset grafici
os.environ['OFFICE_ASSETS'] = str(Path('./assets').resolve())

async def main():
    station = Station()
    await station.start()
    print(f"Server avviato su: {station.url}")
    try:
        await asyncio.Future()
    finally:
        await station.close()

if __name__ == '__main__':
    asyncio.run(main())