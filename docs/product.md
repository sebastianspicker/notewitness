# Product and interface guidance

This document records the intended users and interface constraints for the current workbench. It is
not a capability list or evidence that the interface has been evaluated. Current implementation
status belongs in the root [`README.md`](../README.md) and
[`capabilities.md`](capabilities.md).

## Scope

NoteWitness is a local-first evidence workbench for music teaching and artistic research. The
current alpha supports private, single-user review of lesson and interview evidence. It does not
claim that its workflow is faster, more accurate, or more effective than existing research tools.

## Intended users

The intended users are music-education researchers, artistic researchers, instrumental and vocal
teachers, authorized students or participants, and research data stewards. Their relevant task is to
review what was said, played, demonstrated, assigned, and changed without losing the source span,
provenance, rights state, or revision history.

## Evaluation questions

The workbench should be evaluated on whether authorized users can:

1. locate a suggestion's exact source span;
2. distinguish machine suggestions from accepted evidence;
3. correct evidence without overwriting prior records;
4. resume interrupted local processing without duplicating completed work; and
5. export only evidence and media derivatives permitted by the recorded rights state.

The current alpha has not demonstrated those outcomes in a user study.

## Interface constraints

- Put source verification before summaries or automation controls.
- Express suggestion, acceptance, revision, conflict, processing, and save state in text as well as
  color.
- Keep listening, reviewing, correcting, bookmarking, and exporting within the same source context.
- Let people attribute, accept, revise, reject, or leave uncertain each automatic suggestion.
- Show offline, remote, and rights state without implying a security guarantee that the runtime does
  not provide.
- Avoid chat-interface conventions, unexplained scores, decorative data visualizations, and controls
  that present machine output as accepted fact.
- Do not assume a score, Western notation, teacher/student roles, or one pedagogical tradition is
  present in every project.

## Accessibility target

The design target is WCAG 2.2 AA, but conformance has not been audited. The
repository checks selected keyboard, status, and workbench interaction
contracts. It does not verify screen-reader behavior, browser coverage, text
scaling, reduced motion, touch target size, or complete audio alternatives.
Screenshot review must check focus, clipping, status accuracy, and readable
scaling.
