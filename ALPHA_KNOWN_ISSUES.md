# NoteWitness alpha: known issues

Thank you for testing NoteWitness. This alpha is meant for macOS with Python 3.11
or newer. This page lists what does not work yet, how to work around it, and
what to include when you report a problem.

## Before you start

- **Use Python 3.11 or newer.** The `python3` that ships with macOS is older and
  fails with an `ImportError` about `StrEnum` instead of a clear message.
  Install a current Python (for example with Homebrew) and check with
  `python3 --version`.
- **macOS only for now.** Linux can open the workbench but cannot run local
  transcription or analysis tools. Windows cannot run the workbench at all.
- **Treat alpha projects as disposable.** The project file format may change
  before a stable release, and there is no upgrade path yet. Keep your original
  recordings separately and don't rely on an alpha project long term.

## Opening the workbench

- **Each launch link works once, in one browser tab.** If you open the
  workbench address in a second tab, a private window, or after the first tab
  was closed, the page says there is no workbench session. To get back in,
  switch to the original tab, or press Enter in the terminal where the workbench
  is running to print a new launch link. If that doesn't work (for example when
  the workbench runs in the background), stop it with Ctrl+C and start it again.
  Your project is saved and not affected.
- **Pressing Enter replaces the previous link.** If you press Enter before the
  browser has finished opening the first link, that first link stops working
  and the browser shows a short error. Use the newest link printed in the
  terminal.
- **Only one workbench per project.** Starting a second workbench on the same
  project stops with `workbench_processing_service_already_active`. Close the
  other one first.
- **Some messages are short technical codes**, such as
  `selected_local_runtime_not_ready` or `job_store_failed`. Please include the
  exact code when you report a problem.

## Projects and commands

- **Some commands want the project folder, others want `project.json`.**
  `validate`, `inspect` and `lesson-notes` need the path to `project.json`.
  `workbench`, `ingest-media` and `analyze-local` take the project folder.
  Passing a folder to `validate` prints a confusing "Is a directory" error.
- **A folder without a project in it** reports "project document is not a safe
  regular file" rather than "no project here". Create one with
  `notewitness init` first.

## Trying the review queue

- The shipped example has no audio and nothing to review. To try reviewing with
  audio playback, generate a private sample project as described under
  "Try the review queue with generated audio" in the README. Its suggestions
  are placeholders and don't describe the generated tones.
- **Imported audio may show a length of 0:00** until playback starts, if no
  ffprobe was configured. The length appears once the browser has loaded the
  audio.

## Local transcription and analysis

- Local processing needs tools you install yourself (ffprobe, ffmpeg, Whisper
  and others) plus their licence details. Homebrew installs are supported.
- **Tools may be refused for safety reasons.** A tool must be owned by you or
  the system, and must not be writable by other users. When a tool is refused,
  the error names the folder that failed the check, and
  `notewitness runtime-doctor` lists the reason under `tool_discovery_issues`.
- **Keep the runtime configuration file in a private folder.** For example, put
  it in a folder inside your home directory, with `chmod 600` on the file. Files
  in shared places such as `/tmp` are refused with
  `runtime_config_directory_untrusted`.
- **Running a real transcription job on a tester's Mac has not been confirmed
  yet for this alpha.** Python-based tools may need settings that the
  restricted tool environment does not pass on, and resuming a job after a
  macOS update may be refused. If a local job fails, please report it with the
  details below.
- If you install NoteWitness directly into Homebrew's own Python rather than a
  virtual environment, local tools may refuse to start. Use a virtual
  environment as shown in the README.

## Reporting a problem

Report ordinary bugs as issues on the project's GitHub repository. Include:

1. The version: `notewitness --version` (or
   `PYTHONPATH=src python3 -m notewitness --version` from a checkout).
2. Your macOS version, `python3 --version`, and your browser and its version.
3. The exact command you ran, and everything it printed in the terminal. The
   workbench writes one line per request to the terminal, starting with
   `notewitness-workbench:`. Copy the lines around the problem.
4. For local processing problems, the full output of `notewitness
   runtime-doctor` with the same tool paths you used. Shorten the paths if they
   reveal personal folder names.
5. What you expected, what happened instead, and whether it happens again after
   restarting the workbench.

**Do not attach real recordings, project folders, exports or screenshots of
real lessons.** They can contain personal data about students and teachers. If
a problem only happens with a specific recording, describe it (length, format,
how it was recorded) instead.

**Report security problems privately, not in a public issue.** Follow
`SECURITY.md`.
