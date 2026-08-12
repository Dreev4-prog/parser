from parser import page_url, private_provider_url


def test_private_filter_plain_category():
    url = private_provider_url("https://www.kleinanzeigen.de/s-tablets-reader/c285")
    assert url == "https://www.kleinanzeigen.de/s-tablets-reader/anbieter%3Aprivat/c285"


def test_private_filter_regional_category():
    url = private_provider_url("https://www.kleinanzeigen.de/s-tablets-reader/berlin/c285l3331")
    assert url == "https://www.kleinanzeigen.de/s-tablets-reader/berlin/anbieter%3Aprivat/c285l3331"


def test_private_filter_is_idempotent():
    url = "https://www.kleinanzeigen.de/s-tablets-reader/anbieter%3Aprivat/c285"
    assert private_provider_url(url) == url


def test_private_filter_replaces_business():
    url = private_provider_url("https://www.kleinanzeigen.de/s-tablets-reader/anbieter%3Agewerblich/c285")
    assert url == "https://www.kleinanzeigen.de/s-tablets-reader/anbieter%3Aprivat/c285"


def test_page_url_preserves_private_filter():
    base = private_provider_url("https://www.kleinanzeigen.de/s-tablets-reader/c285")
    assert page_url(base, 2) == "https://www.kleinanzeigen.de/s-tablets-reader/anbieter%3Aprivat/seite%3A2/c285"
