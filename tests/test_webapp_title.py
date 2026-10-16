"""Vini: WhatsApp's web app window read "web.whatsapp.com" (its address,
Chrome's stand-in for a page without a title) while a tab of it read
"WhatsApp": the page's title is set again through DevTools' pipe, the web
app's name when it has none. python3 -m unittest tests.test_webapp_title"""
import json
import unittest

from sonata2.webapps.chromeguard import Guard, is_address

URL = "https://web.whatsapp.com/"


def info(tid, url, title, method="Target.targetInfoChanged"):
    return {"method": method, "params": {"targetInfo": {"targetId": tid, "type": "page", "url": url, "title": title}}}


class TitleTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.g = Guard(URL, self.sent.append, open_outside=lambda u: None, name="WhatsApp")

    def msgs(self):
        return [json.loads(m.rstrip(b"\0")) for m in self.sent]

    def feed(self, *ms):
        self.g.feed(b"".join(json.dumps(m).encode() + b"\0" for m in ms))

    def test_address_title_set_again(self):
        self.feed(info("A", URL, "web.whatsapp.com", "Target.targetCreated"))
        self.assertEqual(self.msgs()[-1]["method"], "Target.attachToTarget")
        self.assertEqual(self.msgs()[-1]["params"], {"targetId": "A", "flatten": True})
        self.feed({"method": "Target.attachedToTarget",
                   "params": {"sessionId": "S1", "targetInfo": {"targetId": "A", "type": "page"}}})
        last = self.msgs()[-1]
        self.assertEqual((last["method"], last["sessionId"]), ("Runtime.evaluate", "S1"))
        self.assertIn('"WhatsApp"', last["params"]["expression"])         # the name, when it has none
        n = len(self.sent)
        self.feed(info("A", URL, "web.whatsapp.com"))                       # still: tried again, attached
        self.assertEqual(self.msgs()[-1]["method"], "Runtime.evaluate")
        self.assertEqual(len(self.sent), n + 1)

    def test_a_real_title_left_alone(self):
        self.feed(info("A", URL, "WhatsApp", "Target.targetCreated"), info("A", URL, "(3) WhatsApp"))
        self.assertEqual(len(self.msgs()), 1)                              # (setDiscoverTargets only)

    def test_never_a_loop(self):
        self.feed(info("A", URL, "web.whatsapp.com", "Target.targetCreated"),
                  {"method": "Target.attachedToTarget",
                   "params": {"sessionId": "S1", "targetInfo": {"targetId": "A"}}})
        for _ in range(10):
            self.feed(info("A", URL, ""))
        self.assertLessEqual(sum(m["method"] == "Runtime.evaluate" for m in self.msgs()), Guard.TITLE_TRIES)

    def test_other_pages_untouched(self):
        self.feed(info("A", URL, "WhatsApp", "Target.targetCreated"),
                  {"method": "Target.attachedToTarget", "params": {"sessionId": "X", "targetInfo": {"targetId": "B"}}})
        self.assertIsNone(self.g.session)

    def test_is_address(self):
        for t in ("", "web.whatsapp.com", "web.whatsapp.com/", URL, "https://web.whatsapp.com"):
            self.assertTrue(is_address(t, URL), t)
        for t in ("WhatsApp", "(2) WhatsApp", "whatsapp"):
            self.assertFalse(is_address(t, URL), t)
        self.assertTrue(is_address("instagram.com", "https://www.instagram.com/"))


if __name__ == "__main__":
    unittest.main()
