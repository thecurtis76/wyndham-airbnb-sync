import datetime as dt
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from feeds import Block, FeedError, Listing, blocked_nights, build_all, to_ranges  # noqa: E402

D = dt.date
FIXTURE = pathlib.Path(__file__).parent / "fixture_2026-11-12.json"


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.l = Listing(slug="x", resort="r", labels=["2 Bedroom Presidential",
                                                       "2 Bedroom Presidential (Presidential Reserve)"])
        self.avail = {
            "2027-01-01": {"2 Bedroom Presidential": 1},
            "2027-01-02": {"2 Bedroom Presidential (Presidential Reserve)": 2},
            "2027-01-03": {"Studio": 5},
            "2027-01-04": {},
        }

    def test_either_label_opens_night(self):
        b = blocked_nights(self.l, self.avail, D(2027, 1, 1), 5, [])
        self.assertEqual(b, [D(2027, 1, 3), D(2027, 1, 4), D(2027, 1, 5)])  # 5th: no data -> blocked

    def test_min_available(self):
        self.l.min_available = 2
        b = blocked_nights(self.l, self.avail, D(2027, 1, 1), 2, [])
        self.assertEqual(b, [D(2027, 1, 1)])

    def test_manual_block_by_resort(self):
        blk = [Block(listing="r", start=D(2027, 1, 2), end=D(2027, 1, 2))]
        b = blocked_nights(self.l, self.avail, D(2027, 1, 1), 2, blk)
        self.assertEqual(b, [D(2027, 1, 2)])

    def test_ranges_use_exclusive_checkout(self):
        r = to_ranges([D(2027, 1, 3), D(2027, 1, 4), D(2027, 1, 7)])
        self.assertEqual(r, [(D(2027, 1, 3), D(2027, 1, 5)), (D(2027, 1, 7), D(2027, 1, 8))])

    def test_refuses_when_labels_vanish(self):
        today = D(2027, 1, 1)
        data = {"r": {(today + dt.timedelta(days=i)).isoformat(): {"Studio": 3} for i in range(90)}}
        with self.assertRaises(FeedError):
            build_all([self.l], data, [], today, 30, dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc))

    def test_refuses_on_empty_resort(self):
        with self.assertRaises(FeedError):
            build_all([self.l], {"r": {}}, [], D(2027, 1, 1), 30,
                      dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc))


class FixtureTests(unittest.TestCase):
    """Real data pulled from the owner site on 2026-11-12."""

    def test_real_data_builds(self):
        import yaml
        cfg = yaml.safe_load((FIXTURE.parent.parent / "config.yaml").read_text())
        data = json.loads(FIXTURE.read_text())
        listings = [Listing(slug=l["slug"], resort=l["resort"], labels=l.get("labels") or [],
                            allow_no_inventory=l.get("allow_no_inventory", False))
                    for l in cfg["listings"]]
        today = D(2026, 11, 13)
        feeds = build_all(listings, data, [], today, 730,
                          dt.datetime(2026, 11, 13, tzinfo=dt.timezone.utc))
        self.assertEqual(feeds["avon-studio-presidential"][1], 0)
        for slug, (_, open_n) in feeds.items():
            if slug != "avon-studio-presidential":
                self.assertGreater(open_n, 30, slug)
        # Spot check: Bonnet Creek 1BR Deluxe had units every night 2027-03-07..20.
        bc = next(l for l in listings if l.slug == "bonnet-creek-1br-deluxe")
        blocked = set(blocked_nights(bc, data["bonnet-creek"], today, 730, []))
        for i in range(14):
            self.assertNotIn(D(2027, 3, 7) + dt.timedelta(days=i), blocked)
        # Nothing past Wyndham's window is open.
        self.assertTrue(all(D(2028, 2, 12) + dt.timedelta(days=i) in blocked for i in range(100)))


if __name__ == "__main__":
    unittest.main()
