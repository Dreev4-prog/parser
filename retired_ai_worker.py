from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-retired-ai")

async def main() -> None:
    log.warning("Legacy DT AI service is retired. DT Radar 3.0 owns all observed-demand analysis.")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
