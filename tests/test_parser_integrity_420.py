import asyncio
import unittest
from datetime import datetime, timedelta

from categories import CATEGORIES
from parser import (
    _allowed_url,
    _parse_category_html_with_stats,
    category_page_info_from_html,
    page_url,
    posted_date_moscow,
    private_provider_url,
    profile_page_dates,
)
from traffic import AdaptiveTrafficManager


def card(slug="normal-ad", extra="", title=None, price="100 €", date="Heute, 10:00", external_id="3499999999"):
    title = title or slug.replace("-", " ")
    return f'''<ul><li class="ad-listitem"><article>{extra}
    <a href="/s-anzeige/{slug}/{external_id}-225-1234"><h2 class="ellipsis">{title}</h2></a>
    <p class="aditem-main--middle--price-shipping--price">{price}</p>
    <div class="aditem-main--top--right">{date}</div>
    </article></li></ul>'''


class PromotionParserTests(unittest.TestCase):
    def assert_card(self, html, *, parsed, promoted=0, reduced=0):
        items, stats = _parse_category_html_with_stats(html)
        self.assertEqual(len(items), parsed)
        self.assertEqual(stats["promoted_filtered"], promoted)
        self.assertEqual(stats["price_reduced_filtered"], reduced)

    def test_product_words_do_not_fake_hochschieben(self):
        for slug in ("push-up-board", "hochschiebe-regal", "boost-adapter"):
            with self.subTest(slug=slug):
                self.assert_card(card(slug), parsed=1)

    def test_title_top_zustand_is_not_paid_top(self):
        self.assert_card(card("fahrrad", title="TOP Zustand Fahrrad"), parsed=1)

    def test_real_bump_svg_is_filtered(self):
        self.assert_card(card(extra='<svg><use href="#icon-feature-bumpup"></use></svg>'), parsed=0, promoted=1)

    def test_real_bump_feature_class_is_filtered(self):
        self.assert_card(card(extra='<span class="featurelabel-bumpup"><svg><use href="#x"></use></svg></span>'), parsed=0, promoted=1)

    def test_generic_navigation_arrow_is_not_filtered(self):
        self.assert_card(card(extra='<svg><use href="#icon-arrow-up"></use></svg>'), parsed=1)

    def test_arrow_inside_explicit_promotion_context_is_filtered(self):
        self.assert_card(card(extra='<span class="promotion-feature"><svg><use href="#icon-arrow-up"></use></svg></span>'), parsed=0, promoted=1)

    def test_crossed_old_price_is_flagged_but_kept_for_price_history(self):
        items, stats = _parse_category_html_with_stats(card(extra='<del>199 €</del>', price="149 €"))
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].is_price_reduced)
        self.assertEqual(stats["price_reduced_filtered"], 1)

    def test_robot_product_word_does_not_trigger_challenge(self):
        html = card("maehroboter", title="Mähroboter Gardena")
        info = category_page_info_from_html(html, requested_page=1, final_url="https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225")
        self.assertFalse(info.suspicious)


class DateAndCategoryTests(unittest.TestCase):
    def test_berlin_late_today_rolls_into_next_moscow_day(self):
        now = datetime(2026, 8, 29, 12, 0)
        self.assertEqual(str(posted_date_moscow("Heute, 23:30", now)), "2026-08-30")
        self.assertEqual(str(posted_date_moscow("Gestern, 23:30", now)), "2026-08-29")

    def test_explicit_calendar_date_is_preserved(self):
        self.assertEqual(str(posted_date_moscow("29.08.2026")), "2026-08-29")

    def test_profile_page_dates_separates_target_and_older(self):
        items, _ = _parse_category_html_with_stats(
            card("a", date="29.08.2026", external_id="3499999991")
            + card("b", date="28.08.2026", external_id="3499999992")
        )
        from datetime import date
        prof = profile_page_dates(items, date(2026, 8, 29))
        self.assertEqual(prof.target_count, 1)
        self.assertEqual(prof.older_count, 1)

    def test_all_category_urls_and_page_variants_are_allowed(self):
        for key, category in CATEGORIES.items():
            url = getattr(category, "url", "")
            if not url:
                continue
            with self.subTest(category=key):
                self.assertTrue(_allowed_url(url))
                private = private_provider_url(url)
                self.assertTrue(_allowed_url(private))
                self.assertTrue(_allowed_url(page_url(private, 15)))


class TrafficPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_browser_obeys_autoscan_pause(self):
        manager = AdaptiveTrafficManager()
        manager.scan_min_interval = manager.view_min_interval = manager.browser_min_interval = 0.0
        await manager.background_pause_started()

        entered = asyncio.Event()

        async def background_browser():
            async with manager.lease("browser", "background"):
                entered.set()

        task = asyncio.create_task(background_browser())
        await asyncio.sleep(0.08)
        self.assertFalse(entered.is_set(), "background browser escaped foreground pause")
        await manager.background_pause_finished()
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        await task


if __name__ == "__main__":
    unittest.main()

class ExactViewIdentityTests(unittest.TestCase):
    def test_listing_identity_is_bound_to_expected_external_id(self):
        from parser import _listing_identity_matches
        good = "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234"
        wrong = "https://www.kleinanzeigen.de/s-anzeige/test/3488888888-225-1234"
        category = "https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225"
        self.assertTrue(_listing_identity_matches(good, "3499999999"))
        self.assertFalse(_listing_identity_matches(wrong, "3499999999"))
        self.assertFalse(_listing_identity_matches(category, "3499999999"))

    def test_passive_counter_endpoint_must_match_requested_ad_id(self):
        from parser import _view_endpoint_matches_ad_id
        good = "https://www.kleinanzeigen.de/s-vac-inc-get.json?adId=3499999999"
        wrong = "https://www.kleinanzeigen.de/s-vac-inc-get.json?adId=3488888888"
        duplicate = "https://www.kleinanzeigen.de/s-vac-inc-get.json?adId=3499999999&adId=3488888888"
        foreign = "https://example.com/s-vac-inc-get.json?adId=3499999999"
        self.assertTrue(_view_endpoint_matches_ad_id(good, "3499999999"))
        self.assertFalse(_view_endpoint_matches_ad_id(wrong, "3499999999"))
        self.assertFalse(_view_endpoint_matches_ad_id(duplicate, "3499999999"))
        self.assertFalse(_view_endpoint_matches_ad_id(foreign, "3499999999"))

class ExactViewPayloadTests(unittest.TestCase):
    def test_official_numvisits_payload_is_accepted(self):
        from parser import _extract_passive_view_payload
        value, shape = _extract_passive_view_payload('{"numVisits": 942}', ad_id="3499999999")
        self.assertEqual(value, 942)
        self.assertIn("official", shape or "")

    def test_generic_count_payload_is_rejected(self):
        from parser import _extract_passive_view_payload
        value, _shape = _extract_passive_view_payload('{"count": 942}', ad_id="3499999999")
        self.assertIsNone(value)

    def test_conflicting_official_values_are_rejected(self):
        from parser import _extract_passive_view_payload
        value, shape = _extract_passive_view_payload('{"numVisits": 942, "nested": {"numVisitsStr": "943"}}')
        self.assertIsNone(value)
        self.assertIn("conflict", shape or "")

class VerifiedExtraInfoTests(unittest.TestCase):
    def test_extra_info_accepts_one_counter_after_date_time_removed(self):
        from parser import KleinanzeigenParser
        value, raw = KleinanzeigenParser._view_value_from_extra_text("Heute, 10:00 · 942")
        self.assertEqual(value, 942)
        self.assertIsNotNone(raw)

    def test_extra_info_rejects_ambiguous_multiple_numbers(self):
        from parser import KleinanzeigenParser
        value, _raw = KleinanzeigenParser._view_value_from_extra_text("Heute, 10:00 · 942 · 12345")
        self.assertIsNone(value)


class DetailOrganicGateTests(unittest.TestCase):
    @staticmethod
    def _doc(body: str) -> str:
        return "<html><head><title>Ad</title></head><body><main id='viewad-main'>" + body + (" x" * 400) + "</main></body></html>"

    def test_detail_featurelabel_highlight_is_promoted(self):
        from parser import KleinanzeigenParser
        p = KleinanzeigenParser()
        result = p._evaluate_detail_integrity_document(
            self._doc("<span class='featurelabel-highlight'>Premium</span>"),
            "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234",
            expected="3499999999", status=200,
        )
        self.assertTrue(result.verified)
        self.assertTrue(result.is_promoted)

    def test_detail_featuretag_gallery_is_promoted(self):
        from parser import KleinanzeigenParser
        p = KleinanzeigenParser()
        result = p._evaluate_detail_integrity_document(
            self._doc("<span class='featuretag-gallery'>Premium</span>"),
            "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234",
            expected="3499999999", status=200,
        )
        self.assertTrue(result.verified)
        self.assertTrue(result.is_promoted)

    def test_detail_plain_highlight_word_is_not_paid_feature(self):
        from parser import KleinanzeigenParser
        p = KleinanzeigenParser()
        result = p._evaluate_detail_integrity_document(
            self._doc("<h1>Highlight Lampe</h1><p>Normale Anzeige</p>"),
            "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234",
            expected="3499999999", status=200,
        )
        self.assertTrue(result.verified)
        self.assertFalse(result.is_promoted)

class ListingAvailabilityIdentityTests(unittest.IsolatedAsyncioTestCase):
    class _Response:
        def __init__(self, status_code, url, text):
            self.status_code = status_code
            self.url = url
            self.text = text

    class _Client:
        def __init__(self, response):
            self.response = response
        async def get(self, *args, **kwargs):
            return self.response

    async def test_wrong_redirect_with_unavailable_copy_is_unknown_not_fast_sold(self):
        from parser import KleinanzeigenParser
        requested = "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234"
        p = KleinanzeigenParser()
        original = p.client
        p.client = self._Client(self._Response(200, "https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225", "Anzeige nicht mehr verfügbar"))
        try:
            self.assertIsNone(await p.check_listing_active(requested))
        finally:
            p.client = original
            await original.aclose()

    async def test_exact_listing_unavailable_copy_is_disappeared(self):
        from parser import KleinanzeigenParser
        requested = "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234"
        p = KleinanzeigenParser()
        original = p.client
        p.client = self._Client(self._Response(200, requested, "Anzeige nicht mehr verfügbar"))
        try:
            self.assertFalse(await p.check_listing_active(requested))
        finally:
            p.client = original
            await original.aclose()

class SearchCardAttributeSafetyTests(unittest.TestCase):
    def test_product_highlight_in_title_attribute_is_not_promotion(self):
        html = card(extra='<span title="Highlight Lampe">Info</span>')
        items, stats = _parse_category_html_with_stats(html)
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["promoted_filtered"], 0)

    def test_product_gallery_in_aria_label_is_not_promotion(self):
        html = card(extra='<span aria-label="Gallery Bilderrahmen">Info</span>')
        items, stats = _parse_category_html_with_stats(html)
        self.assertEqual(len(items), 1)
        self.assertEqual(stats["promoted_filtered"], 0)

    def test_explicit_paid_highlight_attribute_is_promotion(self):
        html = card(extra='<span data-testid="feature-highlight">Premium</span>')
        items, stats = _parse_category_html_with_stats(html)
        self.assertEqual(len(items), 0)
        self.assertEqual(stats["promoted_filtered"], 1)

    def test_malformed_listing_url_cannot_invent_external_id_from_slug(self):
        html = '''<ul><li class="ad-listitem"><article>
        <a href="/s-anzeige/model-3499999999-without-real-id"><h2>Test</h2></a>
        <p class="aditem-main--middle--price-shipping--price">100 €</p>
        <div class="aditem-main--top--right">Heute, 10:00</div>
        </article></li></ul>'''
        items, _stats = _parse_category_html_with_stats(html)
        self.assertEqual(items, [])

class DetailGermanGalerieSafetyTests(unittest.TestCase):
    @staticmethod
    def _doc(body: str) -> str:
        return "<html><head><title>Ad</title></head><body><main id='viewad-main'>" + body + (" x" * 400) + "</main></body></html>"

    def test_detail_feature_galerie_attribute_is_promoted(self):
        from parser import KleinanzeigenParser
        p = KleinanzeigenParser()
        result = p._evaluate_detail_integrity_document(
            self._doc('<span data-testid="feature-galerie">Premium</span>'),
            "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234",
            expected="3499999999", status=200,
        )
        self.assertTrue(result.verified)
        self.assertTrue(result.is_promoted)

    def test_detail_galerie_metadata_is_promoted(self):
        from parser import KleinanzeigenParser
        p = KleinanzeigenParser()
        result = p._evaluate_detail_integrity_document(
            self._doc('<script type="application/json">{"isGalerieAd":true}</script>'),
            "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234",
            expected="3499999999", status=200,
        )
        self.assertTrue(result.verified)
        self.assertTrue(result.is_promoted)

class CategoryRedirectIdentityTests(unittest.TestCase):
    def test_same_category_canonical_redirect_is_allowed(self):
        from parser import _category_feed_identity_matches
        requested = "https://www.kleinanzeigen.de/s-pc-zubehoer-software/anbieter:privat/c225"
        final = "https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225"
        self.assertTrue(_category_feed_identity_matches(requested, final))

    def test_different_category_or_location_redirect_is_rejected(self):
        from parser import _category_feed_identity_matches
        self.assertFalse(_category_feed_identity_matches(
            "https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225",
            "https://www.kleinanzeigen.de/s-multimedia-elektronik/c161",
        ))
        self.assertFalse(_category_feed_identity_matches(
            "https://www.kleinanzeigen.de/s-x/c225l1234",
            "https://www.kleinanzeigen.de/s-x/c225l5678",
        ))

    def test_page_one_wrong_feed_is_not_verified(self):
        from parser import category_page_info_from_html
        info = category_page_info_from_html(
            "<html><body><main>Keine Anzeigen</main></body></html>",
            requested_page=1,
            requested_url="https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225",
            final_url="https://www.kleinanzeigen.de/s-multimedia-elektronik/c161",
        )
        self.assertFalse(info.page_verified)
        self.assertFalse(info.request_matches_page)
