"""Which links a WebKit web app keeps in its own window (the same site) and
which go to the default browser. Too strict and a site's own login or
media pages open in the browser; too loose and any page -- or a file: or
javascript: link -- opens inside the app with its cookies.
Run: python3 -m unittest tests.test_webapp_links"""
import unittest
from unittest import mock

from sonata2.webapps import links as L


class SiteTest(unittest.TestCase):
    def test_subdomains_are_the_same_site(self):
        self.assertEqual(L.site("https://web.whatsapp.com/x"), "whatsapp.com")
        self.assertEqual(L.site("https://WWW.Example.COM/"), "example.com")      # hosts ignore case
        self.assertEqual(L.site("https://www.bbc.co.uk/news"), "bbc.co.uk")

    def test_hosted_pages_are_sites_of_their_own(self):
        """a.github.io and b.github.io belong to different people (the public
        suffix list WebKit uses itself)."""
        if L._base_domain("a.github.io") is None:
            self.skipTest("libsoup 3 not available")
        self.assertNotEqual(L.site("https://a.github.io"), L.site("https://b.github.io"))

    def test_without_libsoup(self):
        """No public suffix list: the last two labels, three for a two-letter
        country domain under a short one (example.co.uk)."""
        with mock.patch.object(L, "_base_domain", return_value=None):
            self.assertEqual(L.site("https://web.whatsapp.com"), "whatsapp.com")
            self.assertEqual(L.site("https://news.bbc.co.uk"), "bbc.co.uk")
            self.assertEqual(L.site("https://www.globo.com.br"), "globo.com.br")
            self.assertEqual(L.site("http://localhost:8080"), "localhost")

    def test_addresses_and_nothing(self):
        """An IP address is a site of its own (no labels to share)."""
        self.assertEqual(L.site("http://192.168.0.1:8080/"), "192.168.0.1")
        self.assertEqual(L.site("http://[::1]/"), "::1")
        self.assertEqual(L.site(""), "")
        self.assertEqual(L.site(None), "")


class StaysInsideTest(unittest.TestCase):
    APP = "https://web.whatsapp.com/"

    def test_same_site_stays(self):
        self.assertTrue(L.stays_inside(self.APP, "https://media.whatsapp.com/v/1"))
        self.assertTrue(L.stays_inside(self.APP, "http://whatsapp.com/"))
        self.assertTrue(L.stays_inside("http://localhost:3000", "http://localhost:4000/"))

    def test_other_sites_leave(self):
        """A host that only ends with the same letters is another site."""
        self.assertFalse(L.stays_inside(self.APP, "https://evilwhatsapp.com/"))
        self.assertFalse(L.stays_inside(self.APP, "https://whatsapp.com.evil.net/"))
        self.assertFalse(L.stays_inside(self.APP, "https://www.google.com/"))
        self.assertFalse(L.stays_inside("http://192.168.0.1", "http://192.168.0.2/"))

    def test_page_internals_stay(self):
        """about:blank, blob: and data: are the page's own (downloads,
        previews, popups it fills itself)."""
        for url in ("", "about:blank", "blob:https://web.whatsapp.com/1234", "data:text/plain,hi"):
            self.assertTrue(L.stays_inside(self.APP, url), url)

    def test_other_schemes_never_open_inside(self):
        """file:, javascript:, mailto: and the like never load in the app."""
        for url in ("file:///etc/passwd", "javascript:alert(1)", "mailto:a@b.c", "tel:+5511999999999",
                    "ftp://whatsapp.com/x"):
            self.assertFalse(L.stays_inside(self.APP, url), url)


if __name__ == "__main__":
    unittest.main()
