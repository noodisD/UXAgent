"""Measured image and control counts count each page once.

They were summed over every accessibility snapshot, so a page revisited, or a
configurator stepped through, was counted each time: the 2026-09-24 hyundai
report said "145 z 1603 obrazów" for pages holding 424 images (41 unnamed).
"""
from simulated_web_agent.main.a11y_checks import measure_names, merge_pages
from simulated_web_agent.main.trace_store import TraceStore

HOME = '- img "Hyundai TUCSON"\n- img\n- button "Konfigurator"'
HOME_MORE = HOME + '\n- img "IONIQ 5"'


def _store(path, snaps_urls):
    s = TraceStore(path)
    obs = []
    for step, (snap, url) in enumerate(snaps_urls):
        s.write_a11y_snapshot(step, snap)
        obs.append({"tabs": [{"url": url, "active": True}]})
    for o in obs:
        s.append_observation(o)
    return s


def test_a_revisited_page_counts_once_at_its_fullest(tmp_path):
    s = _store(tmp_path, [(HOME, "https://x.pl/"), (HOME_MORE, "https://x.pl/"), (HOME, "https://x.pl/")])
    m = measure_names(s)
    assert m["images"] == {"total": 3, "unnamed": 1, "share_unnamed": 0.333}


def test_the_same_page_across_participants_counts_once(tmp_path):
    a = measure_names(_store(tmp_path / "a", [(HOME, "https://x.pl/")]))
    b = measure_names(_store(tmp_path / "b", [(HOME, "https://x.pl/"), (HOME, "https://x.pl/#/kolor")]))
    m = merge_pages([a, b])
    assert m["pages_counted"] == 2, "a hash route is its own view"
    assert m["images"]["total"] == 4 and m["controls"]["total"] == 2
