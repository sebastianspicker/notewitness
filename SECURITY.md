# Security policy

## Supported versions

No NoteWitness version has been published. The current alpha branch is not a
supported release or deployment target.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability, a private-data exposure,
a credential, or an exploit. Do not attach lesson media, participant
identifiers, API keys, model artifacts, project databases, or restricted scores.

Use GitHub private vulnerability reporting from the repository's Security tab
when it is available. Otherwise, set up a private contact channel with a
maintainer before sharing details. Use synthetic data and include only the
minimum reproduction material.

A useful report identifies:

- the affected version or commit;
- the exact entry point and the state needed to reach it;
- the privacy, authorization, rights, or provenance boundary at risk;
- the observed behavior and its impact; and
- a minimal synthetic reproduction.

## Current security boundary

The workbench binds to `127.0.0.1` and requires a per-process session header for
private API, job, and mutation routes. A single-use launch URL delivers the
secret into the tab's origin-and-port-scoped session storage; media playback uses
separate source-bound capabilities. Host, Origin, and CSRF checks remain separate
request controls, and unauthenticated connection concurrency and header-read time
are bounded.
This limits access by local processes that do not know the token, but it does not
protect against a malicious process already running with the same user and
filesystem authority. Project actor IDs are evidence attribution, not
authenticated user identities.

On macOS, NoteWitness refuses external local-tool execution when its network-deny
sandbox is unavailable. Approved tools run with network operations denied and
with bounded arguments, environment, time, output, resource use, and
process-group cleanup, and the runtime checks executable identity around
execution. Filesystem reads and writes are not sandbox-restricted. Unless you
supply a separate filesystem sandbox, approved executables and model loaders
need to be trusted with the invoking user's filesystem authority.

The optional OpenAI path is outside strict local mode. It requires project policy
`remote_explicit`, source and evidence rights, explicit confirmation for each
call, selected text, and fixed request bounds. It never uploads media
automatically. See [`docs/openai-endpoint.md`](docs/openai-endpoint.md) for the
complete boundary.
