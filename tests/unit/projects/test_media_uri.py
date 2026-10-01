from __future__ import annotations

import unittest

from notewitness.projects.media import is_project_media_uri


class ProjectMediaUriTests(unittest.TestCase):
    def test_uri_rule_in_both_modes(self) -> None:
        # (uri, accepted when nested=False, accepted when nested=True)
        cases = (
            ("media/a", True, True),
            ("media/a/b", False, True),
            ("/media/a", False, False),
            ("media\\a", False, False),
            ("media/../a", False, False),
            ("media/a/../b", False, False),
            # PurePosixPath normalises "." segments and trailing slashes away.
            ("media/./a", True, True),
            ("media/", False, True),
            ("media", False, True),
            ("other/a", False, False),
            ("", False, False),
            (None, False, False),
            (7, False, False),
            (b"media/a", False, False),
        )
        for uri, flat, nested in cases:
            with self.subTest(uri=uri):
                self.assertIs(flat, is_project_media_uri(uri, nested=False))
                self.assertIs(nested, is_project_media_uri(uri, nested=True))


if __name__ == "__main__":
    unittest.main()
