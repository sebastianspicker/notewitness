from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PagesDemoContractTests(unittest.TestCase):
    def test_artifact_is_an_interactive_browser_only_mock(self) -> None:
        with TemporaryDirectory() as temporary:
            site = Path(temporary) / "site"
            subprocess.run(
                ["bash", "scripts/build_pages_demo.sh", str(site)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            index = site.joinpath("index.html").read_text(encoding="utf-8")
            client = site.joinpath("assets/pages-demo.js").read_text(encoding="utf-8")
            release = json.loads(site.joinpath("release.json").read_text(encoding="utf-8"))

            self.assertIn('data-demo-mode="mock"', index)
            self.assertIn("Mock lesson · browser-only", index)
            self.assertIn("Changes reset on reload", index)
            self.assertIn("connect-src 'none'", index)
            self.assertGreaterEqual(index.count("data-review-card="), 4)
            for hook in (
                "applyMockDecision",
                "openRevision",
                "selectReview",
                "togglePlayback",
            ):
                self.assertIn(hook, client)

            retired = re.compile(
                r"simulated|walkthrough|data-demo-command",
                re.IGNORECASE,
            )
            self.assertIsNone(retired.search(index))
            self.assertIsNone(retired.search(client))
            self.assertIn("tour.html", client)

            tour = site.joinpath("tour.html").read_text(encoding="utf-8")
            self.assertIn("Screenshot tour", tour)
            self.assertIn('href="./index.html"', tour)
            self.assertIn('src="./assets/screenshots/', tour)
            screenshots = sorted(site.joinpath("assets/screenshots").glob("*.png"))
            self.assertGreaterEqual(len(screenshots), 4)
            for forbidden_api in (
                "fetch",
                "XMLHttpRequest",
                "WebSocket",
                "EventSource",
                "sendBeacon",
                "mediaDevices",
                "localStorage",
                "sessionStorage",
                "indexedDB",
                "document.cookie",
            ):
                self.assertNotIn(forbidden_api, client)

            self.assertEqual("1", release["schema_version"])
            self.assertRegex(release["source_revision"], r"\A[0-9a-fA-F]{40}\Z")
            self.assertIsInstance(release["dirty"], bool)
            fixture = ROOT / "examples/synthetic-lesson/project.json"
            self.assertEqual(
                hashlib.sha256(fixture.read_bytes()).hexdigest(),
                release["synthetic_fixture_sha256"],
            )
            for relative, expected in release["artifact_sha256"].items():
                self.assertEqual(
                    expected,
                    hashlib.sha256(site.joinpath(relative).read_bytes()).hexdigest(),
                    relative,
                )

    def test_pages_deployment_runs_the_complete_gate(self) -> None:
        workflow = ROOT.joinpath(".github/workflows/pages.yml").read_text(
            encoding="utf-8"
        )
        verify = workflow.index("run: bash scripts/verify.sh")
        build = workflow.index("run: bash scripts/build_pages_demo.sh")
        upload = workflow.index("actions/upload-pages-artifact")
        self.assertLess(verify, build)
        self.assertLess(build, upload)
        self.assertIn("needs: build", workflow)
        self.assertIn("pages: write", workflow)
        self.assertIn("id-token: write", workflow)


if __name__ == "__main__":
    unittest.main()
