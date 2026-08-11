import argparse
import asyncio
from datetime import datetime

from sqlalchemy import select

from db import SessionLocal, init_db
from models import Listing, ViewSnapshot
from parser import KleinanzeigenParser


async def upsert_listing(category: str, item) -> tuple[Listing, bool]:
    async with SessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.external_id == item.external_id))
        listing = result.scalar_one_or_none()
        created = listing is None

        if listing is None:
            listing = Listing(
                external_id=item.external_id,
                category=category,
                title=item.title,
                price_text=item.price_text,
                price_eur=item.price_eur,
                url=item.url,
            )
            session.add(listing)
            await session.flush()
        else:
            listing.category = category
            listing.title = item.title
            listing.price_text = item.price_text
            listing.price_eur = item.price_eur
            listing.url = item.url
            listing.last_seen_at = datetime.utcnow()

        await session.commit()
        await session.refresh(listing)
        return listing, created


async def save_views(listing_id: int, views: int | None) -> None:
    async with SessionLocal() as session:
        session.add(ViewSnapshot(listing_id=listing_id, views=views))
        await session.commit()


async def run(category: str, url: str, max_items: int) -> None:
    await init_db()
    parser = KleinanzeigenParser()
    try:
        items = await parser.parse_category_page(url)
        print(f"Found {len(items)} listings on category page")
        print(f"Opening up to {min(len(items), max_items)} listings for view count...")

        with_views = 0
        for item in items[:max_items]:
            listing, created = await upsert_listing(category, item)
            view_result = await parser.parse_views(item.url)
            await save_views(listing.id, view_result.views)
            if view_result.views is not None:
                with_views += 1
            marker = "NEW" if created else "UPD"
            print(
                f"[{marker}] {item.title} | {item.price_text or '-'} | "
                f"views={view_result.views} | source={view_result.source} | {item.url}"
            )

        print(f"Done. View count found for {with_views}/{min(len(items), max_items)} opened listings.")
        if max_items and with_views == 0:
            print(
                "NOTE: Kleinanzeigen may not expose public view counts for these listings. "
                "v1.1 checks static HTML, embedded JSON, rendered JavaScript DOM and JSON network responses; "
                "it does not bypass login, CAPTCHA or access controls."
            )
    finally:
        await parser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Kleinanzeigen category parser v1.1")
    ap.add_argument("--category", required=True, help="Friendly category name")
    ap.add_argument("--url", required=True, help="Public Kleinanzeigen category/search URL")
    ap.add_argument("--max-items", type=int, default=20, help="Max listings to open for view count")
    args = ap.parse_args()
    asyncio.run(run(args.category, args.url, args.max_items))
