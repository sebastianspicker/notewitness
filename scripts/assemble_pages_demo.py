#!/usr/bin/env python3
"""Assemble the static Pages document from renderer output on stdin."""

from __future__ import annotations

import base64
import json
import sys


DEMO_STYLES = """
  .demo-session-note {
    display: grid;
    gap: 2px;
    margin: 12px 0 0;
    padding: 12px 0 0;
    border-top: 1px solid var(--rule);
    color: var(--ink-3);
    font-size: 11px;
    line-height: 1.4;
  }
  .demo-session-note strong { color: var(--ink); font-weight: 650; }
  .demo-tour-link { margin-top: 6px; color: var(--ink); font-size: 11px; font-weight: 600; }
  .demo-audit { margin-top: 16px; }
  .demo-audit ol { margin: 8px 0 0; padding-left: 0; list-style: none; }
  .demo-revision-dialog [data-demo-close-revision] {
    min-height: 32px;
    padding: 0 12px;
    border: 1px solid var(--edge);
    border-radius: var(--r-1);
    font-weight: 650;
  }
  .demo-audit li { margin: 4px 0; color: var(--ink-3); font-size: 12px; }
  .demo-revision-dialog { max-width: 520px; }
  .demo-revision-dialog textarea { width: 100%; min-height: 100px; }
  .demo-decision-summary { margin: 0 24px 24px; padding: 16px 0; border-top: 1px solid var(--rule); }
  .demo-decision-summary h3 { margin: 0 0 8px; font-size: 14px; }
  .demo-decision-summary ol { margin: 0; padding-left: 20px; }
  .demo-decision-summary li { margin: 5px 0; color: var(--ink-3); font-size: 12px; }
  body[data-demo-mode="mock"] .processing-section,
  body[data-demo-mode="mock"] .utilities-section,
  body[data-demo-mode="mock"] .source-section .file-button,
  body[data-demo-mode="mock"] [data-action="open-bookmark"] { display: none !important; }
  body[data-demo-mode="mock"] .record-group { visibility: hidden; }
  body[data-demo-mode="mock"] .full-select.is-hidden { display: none !important; }
  .demo-session-note-mobile { display: none; }
  .demo-hidden { display: none !important; }
  @media (max-width: 760px) {
    .app-header .demo-session-note-mobile {
      display: block;
      position: absolute;
      right: var(--gutter);
      bottom: 12px;
      margin: 0;
      padding: 0;
      border: 0;
      font-size: 11px;
      line-height: 1.25;
      text-align: right;
    }
    .app-header .demo-session-note-mobile strong { font-weight: 650; }
    .app-header .demo-session-note-mobile span::before { content: " · "; }
    .app-header .project-title { padding-right: 150px; }
  }
  @media (max-width: 1100px) {
    body[data-demo-mode="mock"] .rail-tools { grid-template-columns: minmax(0, 1fr); }
  }
"""


def main() -> int:
    encoded = sys.stdin.buffer.read()
    payload = json.loads(base64.b64decode(encoded))
    panels = "\n".join(
        (
            f'<template data-demo-panel="{panel["name"]}">'
            f'{panel["markup"]}</template>'
        )
        for panel in payload["panels"]
    )
    contexts = "\n".join(
        (
            f'<template data-demo-context="{context["name"]}">'
            f'{context["markup"]}</template>'
        )
        for context in payload["contexts"]
    )
    review_contexts = "\n".join(
        f'<template data-demo-review-context="{context["id"]}">{context["markup"]}</template>'
        for context in payload["reviewContexts"]
    )
    document = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <meta name="theme-color" content="#f5f2ea">
    <meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; img-src 'self' data:; media-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'">
    <meta name="description" content="Browser-only mock lesson data rendered with the NoteWitness workbench interface.">
    <title>NoteWitness · mock lesson</title>
    <link rel="icon" href="/assets/notewitness-mark.svg" type="image/svg+xml">
    <link rel="stylesheet" href="/assets/app.css">
    <style>{DEMO_STYLES}</style>
  </head>
  <body data-demo-mode="mock">
    <a class="skip-link" href="#workbench-main">Skip to workspace</a>
    <div id="app">{payload["workbench"]}</div>
    <p class="demo-session-note" role="status"><strong>Mock lesson · browser-only</strong><span>Changes reset on reload</span></p>
    {panels}
    {contexts}
    {review_contexts}
    <template data-demo-review-context="empty">{payload["emptyReviewContext"]}</template>
    <noscript>
      This mock lesson interface needs JavaScript for browser-session interactions.
    </noscript>
    <script type="module" src="/assets/pages-demo.js"></script>
  </body>
</html>"""
    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
