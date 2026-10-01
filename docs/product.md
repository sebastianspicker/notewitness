# Product and interface guidance

This page describes who NoteWitness is for and the interface rules it follows.
It is not a capability list, and it is not evidence that the interface has been
evaluated. For current implementation status, see the root
[README.md](../README.md) and [capabilities.md](capabilities.md).

## Scope

NoteWitness is a local-first evidence workbench for music teaching and artistic
research. The current alpha supports private, single-user review of lesson and
interview evidence. It does not claim to be faster, more accurate, or more
effective than existing research tools.

## Who it is for

Music-education researchers, artistic researchers, instrumental and vocal
teachers, authorized students or participants, and research data stewards. They
share one task: review what was said, played, demonstrated, assigned, and
changed without losing the source span, provenance, rights state, or revision
history.

## What to evaluate

Judge the workbench on whether an authorized user can:

1. locate a suggestion's exact source span;
2. tell machine suggestions apart from accepted evidence;
3. correct evidence without overwriting earlier records;
4. resume interrupted local processing without repeating completed work;
5. export only the evidence and media derivatives the recorded rights allow.

The current alpha has not demonstrated those outcomes in a user study.

## Interface rules

- Put source verification before summaries or automation controls.
- Show suggestion, acceptance, revision, conflict, processing, and save state in
  text as well as color.
- Keep listening, reviewing, correcting, bookmarking, and exporting in the same
  source context.
- Let people attribute, accept, revise, reject, or leave each automatic
  suggestion uncertain.
- Show offline, remote, and rights state without implying a security guarantee
  the runtime does not provide.
- Avoid chat-style interfaces, unexplained scores, decorative data
  visualizations, and controls that present machine output as accepted fact.
- Do not assume a score, Western notation, teacher and student roles, or one
  pedagogical tradition is present in every project.

## Accessibility

The design target is WCAG 2.2 AA, but conformance has not been audited. The
repository checks cover selected keyboard, status, and workbench interaction
contracts. They do not verify screen-reader behavior, browser coverage, text
scaling, reduced motion, touch target size, or complete audio alternatives.
Manual review still needs to check focus, clipping, status accuracy, and
readable scaling.
