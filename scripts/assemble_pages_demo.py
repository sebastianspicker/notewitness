#!/usr/bin/env python3
"""Assemble the static Pages document from renderer output on stdin."""

from __future__ import annotations

import base64
import json
import sys


DEMO_STYLES = """
  .demo-bar {
    min-height: 28px;
    padding: 6px 16px;
    border-bottom: 1px solid var(--rule);
    background: var(--indigo-soft);
    color: var(--indigo-deep);
    font-size: 11px;
    letter-spacing: .02em;
    text-align: center;
  }
  .demo-bar strong { font-weight: 700; }
  .app-shell { height: calc(100vh - 28px); max-height: calc(100vh - 28px); }
  [data-demo-command="simulated"]::after, .demo-action-label {
    display: inline-block;
    margin-left: 6px;
    color: currentColor;
    font-size: 8px;
    font-weight: 700;
    letter-spacing: .08em;
    line-height: 1;
    text-transform: uppercase;
    opacity: .72;
  }
  [data-demo-command="simulated"]::after { content: "Simulated"; }
  .file-button .demo-action-label { margin-left: 4px; }
  .record-group .demo-action-label,
  .practice-list .demo-action-label,
  .quick-plan .demo-action-label { margin: 3px 0 0; }
  .demo-command-help {
    margin: 8px 0 0;
    color: var(--mute);
    font-size: 11px;
    line-height: 1.45;
  }
  .demo-command-help strong { color: var(--indigo-deep); font-weight: 650; }
  .demo-hidden { display: none !important; }
  .full-select.is-hidden { width: 1px; min-height: 0; }
  @media (max-width: 780px) {
    .demo-bar { text-align: left; }
    .app-shell { height: auto; max-height: none; }
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
    document = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <meta name="theme-color" content="#ffffff">
    <meta name="description"
      content="Static, synthetic walkthrough of the NoteWitness local evidence workbench.">
    <title>NoteWitness · static interface demo</title>
    <link rel="icon" href="/assets/notewitness-mark.svg" type="image/svg+xml">
    <link rel="stylesheet" href="/assets/app.css">
    <style>{DEMO_STYLES}</style>
  </head>
  <body>
    <a class="skip-link" href="#workbench-main">Skip to workspace</a>
    <div class="demo-bar" role="note">
      <strong>Static demo · synthetic fixture.</strong>
      Navigation changes this page only; marked actions are simulated and never run commands.
    </div>
    <div id="app">{payload["workbench"]}</div>
    {panels}
    <noscript>
      This static walkthrough requires JavaScript for tabs and simulated controls.
    </noscript>
    <script type="module" src="/assets/pages-demo.js"></script>
  </body>
</html>"""
    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
