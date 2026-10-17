"""Vini: WhatsApp's web app window read "web.whatsapp.com" (its address,
Chrome's stand-in for a page without a title) while a tab of it read
"WhatsApp": the page's title is set again through DevTools' pipe, the web
app's name when it has none. python3 -m unittest tests.test_webapp_title"""
import json
import os
import types
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

    def attach(self):
        self.feed(info("A", URL, "WhatsApp", "Target.targetCreated"),
                  {"method": "Target.attachedToTarget",
                   "params": {"sessionId": "S1", "targetInfo": {"targetId": "A", "type": "page"}}})

    def test_watches_the_app_page(self):
        self.feed(info("A", URL, "WhatsApp", "Target.targetCreated"))
        self.assertEqual(self.msgs()[-1]["method"], "Target.attachToTarget")
        self.assertEqual(self.msgs()[-1]["params"], {"targetId": "A", "flatten": True})
        self.feed({"method": "Target.attachedToTarget",
                   "params": {"sessionId": "S1", "targetInfo": {"targetId": "A", "type": "page"}}})
        methods = [(m["method"], m.get("sessionId")) for m in self.msgs()[-2:]]
        self.assertEqual(methods, [("Page.enable", "S1"), ("Runtime.evaluate", "S1")])

    def test_title_set_again_after_a_move_within_the_page(self):
        """pushState left a history entry without a title: Chrome showed the address."""
        self.attach()
        n = len(self.sent)
        self.feed({"method": "Page.navigatedWithinDocument", "sessionId": "S1",
                   "params": {"frameId": "A", "url": URL + "chat"}})
        last = self.msgs()[-1]
        self.assertEqual(len(self.sent), n + 1)
        self.assertEqual((last["method"], last["sessionId"]), ("Runtime.evaluate", "S1"))
        self.assertIn("document.title = t", last["params"]["expression"])
        self.feed({"method": "Page.navigatedWithinDocument", "sessionId": "OTHER", "params": {}})
        self.assertEqual(len(self.sent), n + 1)                              # another page's: no

    def test_nothing_made_up(self):
        """Vini: no hard-coded name -- the page's own title is set again."""
        self.attach()
        expr = self.msgs()[-1]["params"]["expression"]
        self.assertNotIn("WhatsApp", expr)
        self.assertIn("if (t)", expr)

    def answer(self, value):
        ask = self.msgs()[-1]
        self.assertEqual(ask["method"], "Runtime.evaluate")
        self.assertTrue(ask["params"]["returnByValue"])
        n = len(self.sent)
        self.feed({"id": ask["id"], "sessionId": "S1", "result": {"result": {"type": "string", "value": value}}})
        return self.msgs()[n:]

    def test_page_without_a_title_gets_the_apps_name(self):
        """Vini: WhatsApp's window opened as "web.whatsapp.com" and stayed so
        (its log: the page never gave Chrome a title). The web app's own name
        stands in, only while the page has none."""
        logged = []
        self.g.log = logged.append
        self.attach()
        sent = self.answer("")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["params"]["expression"], 'if (!document.title) document.title = "WhatsApp"')
        self.assertEqual(sent[0]["sessionId"], "S1")
        self.assertIn("page's own title: ''", logged)

    def test_page_with_a_title_keeps_it(self):
        self.attach()
        self.assertEqual(self.answer("(3) WhatsApp"), [])                   # its own title stays

    def test_no_name_nothing_made_up(self):
        self.g.name = ""
        self.attach()
        self.assertEqual(self.answer(""), [])

    def test_address_title_tried_a_few_times_only(self):
        self.attach()
        for _ in range(10):
            self.feed(info("A", URL, "web.whatsapp.com"))
        evals = sum(m["method"] == "Runtime.evaluate" for m in self.msgs())
        self.assertLessEqual(evals, 1 + Guard.TITLE_TRIES)

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


class PageTitleCountTest(unittest.TestCase):
    """Vini: WhatsApp's count never showed -- Chrome titles its window
    "web.whatsapp.com"; the page's own title ("(3) WhatsApp") comes over
    DevTools' pipe, is published, and the Dock counts it."""

    def setUp(self):
        import tempfile
        self.path = os.path.join(tempfile.mkdtemp(), "titles.json")

    def test_published_and_counted(self):
        from sonata2 import badges
        wm = "chrome-web.whatsapp.com__-Default"
        badges.publish_page_title(wm, "(3) WhatsApp", self.path)
        badges.publish_page_title("chrome-other__-Default", "Other", self.path)
        pages = badges.page_titles(self.path)
        win = types.SimpleNamespace(app_id=wm, title="web.whatsapp.com")
        self.assertEqual(badges.window_titles([win], pages), ["(3) WhatsApp", "web.whatsapp.com"])
        self.assertEqual(badges.title_count(badges.window_titles([win], pages)), 3)
        plain = types.SimpleNamespace(app_id="org.gnome.App", title="(1) draft")
        self.assertEqual(badges.window_titles([plain], pages), ["(1) draft"])      # its own title
        badges.publish_page_title(wm, None, self.path)                         # its window gone
        self.assertNotIn(wm, badges.page_titles(self.path))
        self.assertIn("chrome-other__-Default", badges.page_titles(self.path))

    def test_guard_reports_the_page_title(self):
        got = []
        g = Guard(URL, lambda _b: None, open_outside=lambda u: None, on_title=got.append)
        g.feed(json.dumps(info("A", URL, "WhatsApp", "Target.targetCreated")).encode() + b"\0")
        g.feed(json.dumps(info("A", URL, "(2) WhatsApp")).encode() + b"\0")
        g.feed(json.dumps(info("B", "https://x.org/", "(9) X")).encode() + b"\0")    # another page: no
        self.assertEqual(got, ["WhatsApp", "(2) WhatsApp"])

    def test_dock_follows_the_file(self):
        import inspect
        from sonata2.shell import dock
        src = inspect.getsource(dock.Dock)
        self.assertIn("badges.watch(lambda: self._sync_titles(self.windows), badges.PAGE_TITLES)", src)
        self.assertIn("badges.window_titles(wins, pages)", src)


class WhatsAppLoadsTest(unittest.TestCase):
    """Vini: WhatsApp Web stayed on its loading screen for ever, after Sonata
    began running a watcher of its own in the page (Runtime.enable, a
    script on every load, a <title> of ours). The web app's page gets
    nothing of the sort: Sonata only reads it -- "we're putting too much
    where it doesn't belong"."""

    def test_nothing_run_in_the_page_on_every_load(self):
        sent = []
        g = Guard(URL, sent.append, open_outside=lambda u: None, name="WhatsApp")
        g.feed(b"".join(json.dumps(m).encode() + b"\0" for m in (
            info("A", URL, "", "Target.targetCreated"),
            {"method": "Target.attachedToTarget",
             "params": {"sessionId": "S1", "targetInfo": {"targetId": "A", "type": "page"}}},
            {"method": "Page.loadEventFired", "params": {}, "sessionId": "S1"})))
        methods = {json.loads(m.rstrip(b"\0"))["method"] for m in sent}
        for m in ("Runtime.enable", "Runtime.addBinding", "Page.addScriptToEvaluateOnNewDocument"):
            self.assertNotIn(m, methods)

    def test_title_count_not_hidden_by_a_stale_page_title(self):
        """The Dock: the page's title published at the last move ("Instagram",
        or the address) hid the window's own "(3) Instagram"."""
        from sonata2 import badges
        wm = "chrome-www.instagram.com__-Default"
        win = types.SimpleNamespace(app_id=wm, title="(3) Instagram")
        for stale in ("Instagram", "www.instagram.com"):
            self.assertEqual(badges.title_count(badges.window_titles([win], {wm: stale})), 3, stale)
        whatsapp = types.SimpleNamespace(app_id=wm, title="web.whatsapp.com")
        self.assertEqual(badges.title_count(badges.window_titles([whatsapp], {wm: "(4) WhatsApp"})), 4)


class ChromeSenderTest(unittest.TestCase):
    def test_chrome_rewritten_command_line(self):
        """/proc/<pid>/cmdline of Chrome is one string, spaces between -- the
        web app that sent a notification was never found by its profile (it
        went to "Google Chrome")."""
        from sonata2 import badges
        root = "/home/v/.local/share/sonata2-data/webapps"
        one = (f"/opt/google/chrome/chrome --user-data-dir={root}/w84b1822077/chromium --no-first-run"
               " --app=https://www.instagram.com/ --remote-debugging-pipe")
        self.assertEqual(badges.webapp_of([one]), "w84b1822077")
        self.assertEqual(badges.webapp_of(["chrome --user-data-dir=/home/v/My Data/webapps/wab/chromium"]), "wab")
        self.assertEqual(badges.webapp_of(["/opt/google/chrome/chrome --user-data-dir=/home/v/.config/chrome"]), "")
        self.assertEqual(badges.webapp_of(["chrome --app=x --not-user-data-dir=/w/webapps/a/chromium"]), "")


if __name__ == "__main__":
    unittest.main()
