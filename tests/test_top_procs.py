"""Vini: the CPU and GPU modules list the five busiest programs, small, at
the graph's left -- read only while such a module is open."""
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from sonata2.backend import stats as S  # noqa: E402
from sonata2.shell import statsui  # noqa: E402

Adw.init()


class TopListTest(unittest.TestCase):
    def test_summed_per_program_busiest_first(self):
        rows = [("chrome", 10), ("chrome", 15), ("steam", 30), ("idle", 0), ("a", 3), ("b", 2), ("c", 1), ("d", 0.2)]
        self.assertEqual(S.top_list(rows), [("steam", 30), ("chrome", 25), ("a", 3), ("b", 2), ("c", 1)])

    def test_cpu_from_ticks_and_gpu_per_card(self):
        t = [0.0]
        procs = {}

        def processes(want=()):
            return {k: NS(**vars(v)) for k, v in procs.items()}
        sampler = mock.Mock(processes=processes)

        def gpu_usage(ps, dt, now):
            for p in ps.values():
                p.gpu, p.gpu_on = {1: (40.0, "NVIDIA"), 2: (5.0, "AMD")}.get(p.pid, (0.0, ""))
        sampler.gpu_usage = gpu_usage
        top = S.TopProcs(sampler, clock=lambda: t[0])
        procs[1] = NS(pid=1, start_ticks=1, ticks=0, name="game", cpu=0.0, gpu=0.0, gpu_on="")
        procs[2] = NS(pid=2, start_ticks=2, ticks=0, name="chrome", cpu=0.0, gpu=0.0, gpu_on="")
        top.read()
        t[0] = 1.0
        procs[1].ticks = int(0.5 * S.procfs.CLK_TCK)                  # half a core in that second
        with mock.patch.object(S, "CARDS", [("amd", "AMD", "card0"), ("nvidia", "NVIDIA", "card1")]):
            out = top.read()
        self.assertEqual(out["cpu"], [("game", 50.0)])
        self.assertEqual(out["gpu"], [("game", 40.0), ("chrome", 5.0)])
        self.assertEqual(out["gpu_nvidia"], [("game", 40.0)])
        self.assertEqual(out["gpu_amd"], [("chrome", 5.0)])


class ModuleTest(unittest.TestCase):
    def test_rows_and_reading_only_while_shown(self):
        m = statsui.module("cpu")
        self.assertEqual(len(m.top_rows), S.TOP_N)
        r = S.Reading(top={"cpu": [("steam", 31.4), ("chrome", 12.0)]})
        r.history = {}
        m._live.update(r)
        self.assertEqual([lbl.get_label() for lbl in m.top_rows if lbl.get_visible()], ["31%  steam", "12%  chrome"])
        st = S.Stats.shared()
        before = st.top_watchers
        with mock.patch.object(st, "subscribe"), mock.patch.object(st, "unsubscribe"):
            m._live._on(True)
            self.assertEqual(st.top_watchers, before + 1)
            m._live._on(False)
        self.assertEqual(st.top_watchers, before)
        self.assertEqual(statsui.module("ram").top_rows, [])                 # memory: no list


if __name__ == "__main__":
    unittest.main()
