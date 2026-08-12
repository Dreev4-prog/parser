import unittest
from datetime import date

from parser import (
    ParsedListing,
    category_page_info_from_html,
    parse_category_html,
    profile_page_dates,
)


def listing(idx: int, posted: str | None) -> ParsedListing:
    return ParsedListing(
        external_id=str(100000000 + idx),
        title=f"Item {idx}",
        price_text="10 €",
        price_eur=10,
        url=f"https://www.kleinanzeigen.de/s-anzeige/item/{100000000 + idx}-1-1",
        posted_text=posted,
    )


class ParserQualityTests(unittest.TestCase):
    def test_title_date_does_not_override_metadata_date(self):
        html = '''
        <html><body>
          <li class="ad-listitem">
            <a href="/s-anzeige/film-vom-10-08-2026/123456789-79-1"><h2>Film vom 10.08.2026</h2></a>
            <div class="aditem-main--top--right">Gestern, 14:30</div>
            <div class="aditem-main--middle--price-shipping--price">12 €</div>
          </li>
        </body></html>
        '''
        rows = parse_category_html(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].posted_text, "Gestern, 14:30")

    def test_promoted_card_is_removed_but_organic_kept(self):
        html = '''
        <html><body>
          <li class="ad-listitem is-topad">
            <a href="/s-anzeige/top/123456789-79-1"><h2>Top</h2></a>
            <div class="aditem-main--top--right">Heute, 10:00</div>
          </li>
          <li class="ad-listitem">
            <a href="/s-anzeige/normal/223456789-79-1"><h2>Normal</h2></a>
            <div class="aditem-main--top--right">Heute, 10:01</div>
            <div class="aditem-main--middle--price-shipping--price">15 €</div>
          </li>
        </body></html>
        '''
        info = category_page_info_from_html(html, requested_page=1, final_url="https://www.kleinanzeigen.de/s-film-dvd/c79")
        self.assertEqual(len(info.items), 1)
        self.assertEqual(info.promoted_filtered, 1)
        self.assertEqual(info.items[0].title, "Normal")

    def test_normalized_page_is_not_trusted(self):
        html = '''
        <html><body>
          <div>1.376 - 1.400 von 469.976</div>
          <li class="ad-listitem">
            <a href="/s-anzeige/item/323456789-79-1"><h2>Item</h2></a>
            <div class="aditem-main--top--right">11.08.2026</div>
          </li>
        </body></html>
        '''
        info = category_page_info_from_html(
            html,
            requested_page=64,
            final_url="https://www.kleinanzeigen.de/s-film-dvd/seite:56/c79",
        )
        self.assertEqual(info.actual_page, 56)
        self.assertFalse(info.request_matches_page)
        self.assertFalse(info.page_verified)

    def test_low_date_coverage_cannot_prove_target(self):
        rows = [listing(1, "10.08.2026")] + [listing(i, None) for i in range(2, 6)]
        profile = profile_page_dates(rows, date(2026, 8, 10))
        self.assertEqual(profile.relation, "unknown")
        self.assertLess(profile.coverage, 0.55)

    def test_boundary_page_with_one_target_is_accepted_when_dates_are_good(self):
        rows = [listing(i, "11.08.2026") for i in range(1, 5)] + [listing(5, "10.08.2026")]
        profile = profile_page_dates(rows, date(2026, 8, 10))
        self.assertEqual(profile.relation, "target")
        self.assertEqual(profile.target_count, 1)
        self.assertEqual(profile.parsed_count, 5)

    def test_date_direction_requires_reliable_dates(self):
        rows = [listing(i, "11.08.2026") for i in range(1, 6)]
        profile = profile_page_dates(rows, date(2026, 8, 10))
        self.assertEqual(profile.relation, "newer")
        self.assertGreaterEqual(profile.confidence, 0.9)


if __name__ == "__main__":
    unittest.main()

class LocationShardTests(unittest.TestCase):
    def test_counted_location_links_are_extracted_for_large_category_fallback(self):
        html = '''
        <html><body>
          <div>1 - 25 von 18.489</div>
          <section>
            <h3>Ort</h3>
            <ul>
              <li><a href="/s-foto/mitte/c245l3518">Mitte</a> (2.441)</li>
              <li><a href="/s-foto/pankow/c245l3431">Pankow</a> (2.069)</li>
            </ul>
          </section>
          <li class="ad-listitem">
            <a href="/s-anzeige/item/423456789-245-1"><h2>Camera</h2></a>
            <div class="aditem-main--top--right">Gestern, 20:00</div>
            <div class="aditem-main--middle--price-shipping--price">100 €</div>
          </li>
        </body></html>
        '''
        info = category_page_info_from_html(
            html,
            requested_page=1,
            final_url="https://www.kleinanzeigen.de/s-foto/berlin/c245l3331",
        )
        self.assertTrue(info.location_shards)
        urls = [u for u, _ in info.location_shards]
        self.assertIn("https://www.kleinanzeigen.de/s-foto/mitte/c245l3518", urls)
        self.assertIn("https://www.kleinanzeigen.de/s-foto/pankow/c245l3431", urls)
        counts = dict(info.location_shards)
        self.assertEqual(counts["https://www.kleinanzeigen.de/s-foto/mitte/c245l3518"], 2441)
