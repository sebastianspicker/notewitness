# Security policy

## Supported versions

No NoteWitness version has been published. The `0.1.0a0` working tree receives security fixes during
local review, but it is not a supported release or deployment target.

## Reporting a vulnerability

Do not open a public issue for a vulnerability, private-data exposure, credential, or exploit. Do
not attach lesson media, participant identifiers, API keys, model artifacts, project databases, or
restricted scores.

Before a public alpha is published, the maintainer must enable and verify GitHub private
vulnerability reporting. If that service is unavailable, the maintainer may establish and document
another specific private channel. Until one of those channels is verified, the release remains
blocked as recorded in the repository release status.

If you are reviewing this local candidate, share security details only through a private channel
that you have independently established with the maintainer. Use synthetic data and include only the
minimum reproduction material.

A useful report identifies:

- the affected version or commit;
- the exact entry point and prerequisite state;
- the expected privacy, authorization, rights, or provenance boundary;
- the observed behavior and impact; and
- a minimal synthetic reproduction.

## Current security boundary

The workbench binds to `127.0.0.1` and requires a per-process session cookie for private API, job,
media, and mutation routes. A single-use launch URL establishes the session. Host, Origin, and CSRF
checks remain separate request controls. This design limits access by local processes that do not
know the token, but it does not protect against a malicious process already running with the same
user and filesystem authority. Project actor IDs are evidence attribution, not authenticated user
identities.

On macOS, NoteWitness refuses external local-tool execution when its network-deny sandbox is
unavailable. Approved tools run with network operations denied, bounded arguments, environment,
time, output, resource use, and process-group cleanup, with executable identity checked around
execution. Filesystem reads and writes are not sandbox-restricted. Unless an operator supplies a
separate filesystem sandbox, approved executables and model loaders therefore need to be trusted
with the invoking user's filesystem authority.

The optional OpenAI path is outside strict local mode. It requires project policy
`remote_explicit`, source and evidence rights, explicit confirmation for each call, selected text,
and fixed request bounds. It does not upload media automatically. See
[`docs/openai-endpoint.md`](docs/openai-endpoint.md) for the complete boundary.
