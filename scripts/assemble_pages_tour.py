#!/usr/bin/env python3
"""Emit the static Pages screenshot tour document on stdout.

The captures only ever show the synthetic lesson fixture under
``docs/screenshots``. Keep private media and real-lesson captures out of the
tour; the Pages demo build re-checks the artifact for private residue.
"""

from __future__ import annotations

import html
import sys


TOUR = (
    (
        "review-queue.png",
        "Review queue",
        "Machine suggestions wait here until a named person accepts, revises, "
        "or rejects them. The source span, generator, confidence, and rights "
        "record stay beside the decision.",
    ),
    (
        "review-decision.png",
        "Decision feedback",
        "Accepting appends a decision, advances the queue, and records the "
        "outcome in a browser-session log. Original machine output is never "
        "overwritten.",
    ),
    (
        "transcript.png",
        "Full transcript",
        "Speech, notes, pitch, music, and overlap sit on one chronological "
        "evidence record with an explicitly attributed actor.",
    ),
    (
        "lesson-notes.png",
        "Lesson notes",
        "An evidence-backed projection of the teaching sequence, practice "
        "tasks, and rights-gated exports.",
    ),
    (
        "dark-theme.png",
        "Dark theme",
        "The same source-first layout in a low-light palette.",
    ),
    (
        "mobile-review.png",
        "Narrow layout",
        "The review flow stays usable on a phone-sized viewport.",
    ),
)

STYLES = """
  :root { color-scheme: light; }
  body {
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font: 14px / 1.5 var(--font);
    -webkit-font-smoothing: antialiased;
  }
  .tour-page { max-width: 1120px; margin: 0 auto; padding: 32px 24px 64px; }
  .tour-header { display: flex; align-items: center; gap: 14px; }
  .tour-header img { width: 40px; height: 40px; }
  .tour-eyebrow {
    margin: 0;
    color: var(--mute);
    font-size: 11px;
    letter-spacing: .14em;
    text-transform: uppercase;
  }
  .tour-title { margin: 2px 0 0; font: 500 1.75rem / 1.15 var(--serif); letter-spacing: -.02em; }
  .tour-lede { max-width: 68ch; margin: 20px 0 8px; color: var(--ink-2); font-size: 15px; }
  .tour-demo-link { display: inline-block; margin: 4px 0 28px; color: var(--indigo); font-weight: 600; }
  .tour-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 28px;
    margin: 0;
  }
  .tour-shot { margin: 0; }
  .tour-shot img {
    display: block;
    width: 100%;
    height: auto;
    border: 1px solid var(--rule-2);
    border-radius: var(--r-md);
    background: var(--paper);
  }
  .tour-shot figcaption { margin-top: 10px; color: var(--ink-2); font-size: 13px; }
  .tour-shot figcaption strong { color: var(--ink); font-weight: 650; }
  .tour-foot {
    margin-top: 40px;
    padding-top: 16px;
    border-top: 1px solid var(--rule);
    color: var(--mute);
    font-size: 12px;
  }
  .tour-foot a { color: var(--indigo); }
"""


def main() -> int:
    figures = "\n".join(
        f"""      <figure class="tour-shot">
        <img src="./assets/screenshots/{name}" alt="{html.escape(title)}: {html.escape(caption)}">
        <figcaption><strong>{html.escape(title)}.</strong> {html.escape(caption)}</figcaption>
      </figure>"""
        for name, title, caption in TOUR
    )
    document = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <meta name="theme-color" content="#ffffff">
    <meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; img-src 'self' data:; media-src 'none'; style-src 'self' 'unsafe-inline'">
    <meta name="description" content="Screenshot tour of the NoteWitness Evidence Ledger rendered from a synthetic lesson.">
    <title>NoteWitness · screenshot tour</title>
    <link rel="icon" href="./assets/notewitness-mark.svg" type="image/svg+xml">
    <link rel="stylesheet" href="./assets/styles/tokens.css">
    <style>{STYLES}</style>
  </head>
  <body>
    <main class="tour-page">
      <header class="tour-header">
        <img src="./assets/notewitness-mark.svg" alt="">
        <div>
          <p class="tour-eyebrow">NoteWitness</p>
          <h1 class="tour-title">Screenshot tour</h1>
        </div>
      </header>
      <p class="tour-lede">
        Captures from the interactive mock-data demo, rendered by the production
        Evidence Ledger interface over a deterministic synthetic violin lesson.
        They show the review workflow only, and contain no private lesson data.
      </p>
      <a class="tour-demo-link" href="./index.html">Open the interactive demo →</a>
      <section class="tour-grid">
{figures}
      </section>
      <p class="tour-foot">
        Screenshots show the synthetic fixture that ships with the repository.
        They are not evidence of model quality, browser coverage, or readiness
        for private lesson data. NoteWitness is licensed AGPL-3.0-or-later.
      </p>
    </main>
  </body>
</html>"""
    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
