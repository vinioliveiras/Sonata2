"""Vini: (1) a web app's sound showed as Chrome's in the mixer: each web
app is its own entry; (2) a link in a Chromium web app opened a full,
unthemed browser window: new tabs and windows go to the default browser,
login popups stay."""
import json
import os
import tempfile
import unittest
from unittest import mock

from sonata2.backend import mixer
from sonata2.webapps.chromeguard import Guard


def target(tid, url, opener=False, kind="page"):
    return {"method": "Target.targetCreated",
            "params": {"targetInfo": {"targetId": tid, "type": kind, "url": url, "canAccessOpener": opener}}}


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.sent, self.opened = [], []
        self.g = Guard("https://web.whatsapp.com/", self.sent.append, open_outside=self.opened.append)

    def msgs(self):
        return [json.loads(m.rstrip(b"\0")) for m in self.sent]

    def feed(self, *ms):
        self.g.feed(b"".join(json.dumps(m).encode() + b"\0" for m in ms))

    def test_new_tabs_go_to_the_default_browser(self):
        self.assertEqual(self.msgs()[0]["method"], "Target.setDiscoverTargets")
        self.feed(target("A", "https://web.whatsapp.com/"))                 # the app's own page
        self.feed(target("B", "about:blank"))                               # a new tab, no address yet
        self.assertEqual(self.opened, [])
        self.feed({"method": "Target.targetInfoChanged",
                   "params": {"targetInfo": {"targetId": "B", "type": "page", "url": "https://example.org/a"}}})
        self.assertEqual(self.opened, ["https://example.org/a"])
        self.assertEqual(self.msgs()[-1], {"id": 2, "method": "Target.closeTarget", "params": {"targetId": "B"}})
        self.feed(target("C", "chrome://newtab/"))                          # Ctrl+T: just closed
        self.assertEqual(self.msgs()[-1]["params"], {"targetId": "C"})
        self.assertEqual(len(self.opened), 1)

    def test_login_popups_and_workers_stay(self):
        self.feed(target("A", "https://web.whatsapp.com/"),
                  target("P", "https://accounts.google.com/o/oauth2", opener=True),
                  target("W", "https://web.whatsapp.com/sw.js", kind="service_worker"))
        self.assertEqual(self.opened, [])
        self.assertEqual(len(self.msgs()), 1)                              # nothing closed

    def test_split_messages(self):
        data = json.dumps(target("A", "https://web.whatsapp.com/")).encode() + b"\0" + \
            json.dumps(target("B", "https://x.org/")).encode() + b"\0"
        self.g.feed(data[:17])
        self.g.feed(data[17:])
        self.assertEqual(self.opened, ["https://x.org/"])


class MixerWebAppTest(unittest.TestCase):
    def fake_proc(self, tree):
        """tree: {pid: (ppid, cmdline)}"""
        d = tempfile.mkdtemp()
        for pid, (ppid, cmd) in tree.items():
            os.makedirs(f"{d}/{pid}")
            with open(f"{d}/{pid}/cmdline", "wb") as f:
                f.write(cmd.replace(" ", "\0").encode())
            with open(f"{d}/{pid}/stat", "w") as f:
                f.write(f"{pid} (x) S {ppid} 0 0")
        return d

    def test_webapp_of(self):
        proc = self.fake_proc({
            10: (1, "/opt/google/chrome/chrome --user-data-dir=/home/v/.local/share/sonata2-data/webapps/wa1/chromium"
                    " --app=https://web.whatsapp.com/"),
            11: (10, "/opt/google/chrome/chrome --type=utility --utility-sub-type=audio.mojom.AudioService"),
            20: (1, "/usr/bin/python3 -m sonata2 webapp yt2"),
            21: (20, "/usr/lib/webkitgtk-6.0/WebKitWebProcess 7 12"),
            30: (1, "/opt/google/chrome/chrome"),
            31: (30, "/opt/google/chrome/chrome --type=utility"),
        })
        self.assertEqual(mixer.webapp_of(11, proc), "wa1")
        self.assertEqual(mixer.webapp_of(21, proc), "yt2")
        self.assertIsNone(mixer.webapp_of(31, proc))                        # the real Chrome

    def test_stream_is_the_webapps_own(self):
        text = json.dumps([{"index": 5, "volume": {}, "mute": False, "properties": {
            "application.name": "Google Chrome", "application.process.binary": "chrome",
            "application.process.id": "11"}}])
        with mock.patch("sonata2.webapps.get", lambda wid: {"name": "WhatsApp"} if wid == "wa1" else None):
            s = mixer.parse(text, webapp=lambda pid: "wa1" if pid == 11 else None)[0]
        self.assertEqual((s.key, s.name), ("sonata2-webapp-wa1", "WhatsApp"))
        s = mixer.parse(text, webapp=lambda pid: None)[0]
        self.assertEqual(s.key, "chrome")


if __name__ == "__main__":
    unittest.main()
