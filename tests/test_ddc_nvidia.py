"""Vini: an external monitor with DDC/CI on, and no brightness slider that
worked -- NVIDIA's driver links no I2C bus to the DRM connector, which is
where Sonata looked. ddcutil's own detection is used then: by the connector
it names (ddcutil 2), else by the monitor's identity in its EDID.
python3 -m unittest tests.test_ddc_nvidia"""
import os
import tempfile
import unittest
from unittest import mock

from sonata2.backend import system as S

V2 = """Display 1
   I2C bus:  /dev/i2c-7
   DRM connector:           card1-HDMI-A-1
   Monitor:                 AOC:Q27G2G4:1A2B3C

Invalid display
   I2C bus:  /dev/i2c-3
   DRM connector:           card2-eDP-1
"""
V1 = """Display 1
   I2C bus:  /dev/i2c-5
   Monitor:             AOC:Q27G2G4:1A2B3C
"""


def edid(mfg="AOC", name="Q27G2G4", serial="1A2B3C") -> bytes:
    e = bytearray(128)
    e[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    v = sum((ord(c) - 64) << s for c, s in zip(mfg, (10, 5, 0)))
    e[8], e[9] = v >> 8, v & 0xFF
    for off, tag, text in ((72, 0xFC, name), (90, 0xFF, serial)):
        e[off:off + 5] = bytes([0, 0, 0, tag, 0])
        e[off + 5:off + 18] = (text.encode() + b"\n").ljust(13, b" ")
    return bytes(e)


class DdcTest(unittest.TestCase):
    def setUp(self):
        S._detected.update(at=0.0, displays=[])
        self.drm = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.drm, "card1-HDMI-A-1"))          # no "ddc" link (NVIDIA)
        with open(os.path.join(self.drm, "card1-HDMI-A-1", "edid"), "wb") as f:
            f.write(edid())

    def test_parse_both_versions(self):
        self.assertEqual(S.parse_ddc_detect(V2), [{"bus": 7, "connector": "card1-HDMI-A-1", "mfg": "AOC",
                                                   "model": "Q27G2G4", "serial": "1A2B3C"}])
        self.assertEqual(S.parse_ddc_detect(V1)[0]["bus"], 5)
        self.assertEqual(S.parse_ddc_detect(V1)[0]["connector"], "")

    def test_edid_identity(self):
        with mock.patch.object(S, "DRM", self.drm):
            self.assertEqual(S._edid_id("HDMI-A-1"), ("AOC", "Q27G2G4", "1A2B3C"))
            self.assertIsNone(S._edid_id("DP-9"))

    def bus(self, detect_out):
        with mock.patch.object(S, "DRM", self.drm), mock.patch.object(S.shutil, "which", return_value="/usr/bin/ddcutil"), \
                mock.patch.object(S, "_run", return_value=(0, detect_out)) as run:
            got = S.ddc_bus("HDMI-A-1")
            S.ddc_bus("HDMI-A-1")                                        # again: from what was found
        return got, run.call_count

    def test_found_by_connector(self):
        self.assertEqual(self.bus(V2), (7, 1))                           # one probe, kept

    def test_found_by_edid_without_connector(self):
        self.assertEqual(self.bus(V1)[0], 5)

    def test_other_monitor_not_taken(self):
        self.assertIsNone(self.bus(V1.replace("Q27G2G4", "U2415"))[0])

    def test_sysfs_link_still_first(self):
        os.makedirs(os.path.join(self.drm, "card0-DP-1", "i2c-4"))
        with mock.patch.object(S, "DRM", self.drm), mock.patch.object(S, "_run") as run:
            self.assertEqual(S.ddc_bus("DP-1"), 4)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
