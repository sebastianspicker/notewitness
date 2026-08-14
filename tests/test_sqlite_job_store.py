from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from notewitness.domain.analysis import AnalysisStage, JobState
from notewitness.domain.jobs import AnalysisJobSpec
from notewitness.domain.timeline import MediaSpan
from notewitness.infrastructure.sqlite_job_store import (
    JobConflictError,
    JobStoreError,
    SQLiteJobStore,
    _secure_private_sidecar,
)


SHA = "a" * 64


def spec(**overrides: object) -> AnalysisJobSpec:
    values: dict[str, object] = {
        "job_id": "job:test",
        "source_id": "source:test",
        "source_sha256": SHA,
        "stages": (AnalysisStage.SPEECH_RECOGNITION,),
        "spans": (MediaSpan("source:test", "audio:0", 0, 10),),
        "adapter_fingerprint_sha256": "b" * 64,
        "runtime_fingerprint_sha256": "c" * 64,
        "settings_fingerprint_sha256": "d" * 64,
        "score_sha256": "e" * 64,
    }
    values.update(overrides)
    return AnalysisJobSpec(**values)  # type: ignore[arg-type]


class SQLiteJobStoreTests(unittest.TestCase):
    def test_two_stores_claim_atomically(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.sqlite"
            first, second = SQLiteJobStore(path), SQLiteJobStore(path)
            first.enqueue(spec())
            claimed = first.claim(
                "job:test", owner_id="worker:a", lease_seconds=30, source_sha256=SHA,
                adapter_fingerprint_sha256="b" * 64, runtime_fingerprint_sha256="c" * 64,
                settings_fingerprint_sha256="d" * 64, score_sha256="e" * 64,
            )
            other = second.claim(
                "job:test", owner_id="worker:b", lease_seconds=30, source_sha256=SHA,
                adapter_fingerprint_sha256="b" * 64, runtime_fingerprint_sha256="c" * 64,
                settings_fingerprint_sha256="d" * 64, score_sha256="e" * 64,
            )
            self.assertEqual("worker:a", claimed.owner_id if claimed else None)
            self.assertIsNone(other)

    def test_stale_lease_recovers_to_paused(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            store.claim(
                "job:test", owner_id="worker:a", lease_seconds=0.01, source_sha256=SHA,
                adapter_fingerprint_sha256="b" * 64, runtime_fingerprint_sha256="c" * 64,
                settings_fingerprint_sha256="d" * 64, score_sha256="e" * 64,
            )
            time.sleep(0.02)
            self.assertEqual(1, store.recover_stale_leases())
            self.assertEqual(
                JobState.PAUSED, store.get("job:test").state
            )  # type: ignore[union-attr]

    def test_cancellation_prevents_completion(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            store.claim(
                "job:test", owner_id="worker:a", lease_seconds=30, source_sha256=SHA,
                adapter_fingerprint_sha256="b" * 64, runtime_fingerprint_sha256="c" * 64,
                settings_fingerprint_sha256="d" * 64, score_sha256="e" * 64,
            )
            store.checkpoint(
                "job:test",
                owner_id="worker:a",
                stage=AnalysisStage.SPEECH_RECOGNITION,
                completed_span_count=0,
                continuation_token="resume:cancelled",
                last_artifact_id="artifact:checkpoint",
                pause=False,
            )
            store.request_cancellation("job:test")
            cancelled = store.complete("job:test", owner_id="worker:a")
            self.assertEqual(JobState.CANCELLED, cancelled.state)
            self.assertEqual("resume:cancelled", cancelled.continuation_token)
            self.assertEqual("artifact:checkpoint", cancelled.last_artifact_id)

    def test_claim_rejects_each_immutable_identity_mismatch(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            identities = {
                "source_sha256": SHA,
                "adapter_fingerprint_sha256": "b" * 64,
                "runtime_fingerprint_sha256": "c" * 64,
                "settings_fingerprint_sha256": "d" * 64,
                "score_sha256": "e" * 64,
            }
            for field in identities:
                with self.subTest(field=field), self.assertRaises(JobConflictError):
                    attempt = dict(identities)
                    attempt[field] = "f" * 64
                    store.claim(
                        "job:test", owner_id="worker:a", lease_seconds=30, **attempt
                    )
            with self.assertRaises(JobConflictError):
                store.enqueue(spec(runtime_fingerprint_sha256="e" * 64))

    def test_claim_next_requires_adapter_and_score_identity(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            for adapter, score in (("f" * 64, "e" * 64), ("b" * 64, "f" * 64)):
                with self.subTest(adapter=adapter, score=score):
                    self.assertIsNone(
                        store.claim_next(
                            owner_id="worker:a", lease_seconds=30, source_sha256=SHA,
                            adapter_fingerprint_sha256=adapter,
                            runtime_fingerprint_sha256="c" * 64,
                            settings_fingerprint_sha256="d" * 64, score_sha256=score,
                        )
                    )

    def test_checkpoint_reopen_and_resume(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.sqlite"
            store = SQLiteJobStore(path); store.enqueue(spec())
            store.claim(
                "job:test", owner_id="worker:a", lease_seconds=30, source_sha256=SHA,
                adapter_fingerprint_sha256="b" * 64, runtime_fingerprint_sha256="c" * 64,
                settings_fingerprint_sha256="d" * 64, score_sha256="e" * 64,
            )
            store.checkpoint(
                "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                completed_span_count=1, continuation_token="next", last_artifact_id="artifact:1",
            )
            reopened = SQLiteJobStore(path)
            paused = reopened.get("job:test")
            self.assertEqual(
                (JobState.PAUSED, "next", "artifact:1"),
                (paused.state, paused.continuation_token, paused.last_artifact_id),
            )  # type: ignore[union-attr]
            self.assertEqual(
                JobState.RUNNING,
                reopened.resume(
                    "job:test", owner_id="worker:b", lease_seconds=30, source_sha256=SHA,
                    adapter_fingerprint_sha256="b" * 64,
                    runtime_fingerprint_sha256="c" * 64,
                    settings_fingerprint_sha256="d" * 64, score_sha256="e" * 64,
                ).state,
            )  # type: ignore[union-attr]

    def test_checkpoint_rejects_invalid_values_without_mutating_running_job(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            store.enqueue(spec())
            store.claim(
                "job:test", owner_id="worker:a", lease_seconds=30, source_sha256=SHA,
                adapter_fingerprint_sha256="b" * 64, runtime_fingerprint_sha256="c" * 64,
                settings_fingerprint_sha256="d" * 64, score_sha256="e" * 64,
            )
            before = store.get("job:test")

            with self.assertRaisesRegex(ValueError, "^stage must be an AnalysisStage\\.$"):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage="invalid",  # type: ignore[arg-type]
                    completed_span_count=1, continuation_token="resume:next",
                    last_artifact_id="artifact:checkpoint",
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                JobConflictError, "^checkpoint is outside the immutable job specification$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.MEDIA_PROBE,
                    completed_span_count=1, continuation_token="resume:next",
                    last_artifact_id="artifact:checkpoint",
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                JobConflictError, "^checkpoint is outside the immutable job specification$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=2, continuation_token="resume:next",
                    last_artifact_id="artifact:checkpoint",
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                ValueError, "^completed_span_count must be non-negative\\.$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=True, continuation_token="resume:next",  # type: ignore[arg-type]
                    last_artifact_id="artifact:checkpoint",
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                ValueError, "^completed_span_count must be non-negative\\.$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=-1, continuation_token="resume:next",
                    last_artifact_id="artifact:checkpoint",
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                ValueError, "^continuation_token exceeds its bounded contract\\.$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=1, continuation_token="x" * 4_097,
                    last_artifact_id="artifact:checkpoint",
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                ValueError, "^continuation_token exceeds its bounded contract\\.$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=1, continuation_token=1,  # type: ignore[arg-type]
                    last_artifact_id="artifact:checkpoint",
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                ValueError, "^continuation_token exceeds its bounded contract\\.$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=1, continuation_token="",
                    last_artifact_id="artifact:checkpoint",
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                ValueError, "^last_artifact_id must be a bounded non-empty string\\.$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=1, continuation_token="resume:next",
                    last_artifact_id="x" * 513,
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                ValueError, "^last_artifact_id must be a bounded non-empty string\\.$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=1, continuation_token="resume:next",
                    last_artifact_id=1,  # type: ignore[arg-type]
                )
            self.assertEqual(before, store.get("job:test"))

            with self.assertRaisesRegex(
                ValueError, "^last_artifact_id must be a bounded non-empty string\\.$"
            ):
                store.checkpoint(
                    "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                    completed_span_count=1, continuation_token="resume:next",
                    last_artifact_id="",
                )
            self.assertEqual(before, store.get("job:test"))

            boundary_checkpoint = store.checkpoint(
                "job:test", owner_id="worker:a", stage=AnalysisStage.SPEECH_RECOGNITION,
                completed_span_count=1, continuation_token="x" * 4_096,
                last_artifact_id="x" * 512, pause=False,
            )
            self.assertEqual(
                (JobState.RUNNING, "worker:a", "x" * 4_096, "x" * 512),
                (
                    boundary_checkpoint.state,
                    boundary_checkpoint.owner_id,
                    boundary_checkpoint.continuation_token,
                    boundary_checkpoint.last_artifact_id,
                ),
            )
            self.assertEqual(boundary_checkpoint, store.get("job:test"))

    def test_permissions_and_symlink_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            unsafe = parent / "unsafe"; unsafe.mkdir(); unsafe.chmod(0o755)
            with self.assertRaises(JobStoreError): SQLiteJobStore(unsafe / "jobs.sqlite")
            private = parent / "private"; private.mkdir(mode=0o700)
            target = private / "target.sqlite"; target.touch(mode=0o600)
            link = private / "link.sqlite"; link.symlink_to(target)
            with self.assertRaises(JobStoreError): SQLiteJobStore(link)

    def test_sidecar_disappearance_is_ignored(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            vanished = Path(f"{store.path}-wal")
            real_open = os.open

            def disappear(path: object, flags: int, mode: int = 0o777) -> int:
                if os.fspath(path) == os.fspath(vanished):
                    raise FileNotFoundError()
                return real_open(path, flags, mode)

            with patch(
                "notewitness.infrastructure.sqlite_job_store.os.open",
                side_effect=disappear,
            ):
                store._private_sidecars()

    def test_sidecar_symlink_is_rejected_without_touching_target(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            target = Path(temporary) / "unrelated-private-file"
            target.write_bytes(b"must not be chmodded through the symlink")
            target.chmod(0o644)
            sidecar = Path(f"{store.path}-wal")
            sidecar.symlink_to(target)

            with self.assertRaisesRegex(
                JobStoreError,
                "^database path must be a regular non-symlink file$",
            ):
                store._private_sidecars()

            self.assertEqual(0o644, target.stat().st_mode & 0o777)

    def test_sidecar_os_error_has_stable_message(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SQLiteJobStore(Path(temporary) / "jobs.sqlite")
            failing = Path(f"{store.path}-wal")
            real_open = os.open

            def deny(path: object, flags: int, mode: int = 0o777) -> int:
                if os.fspath(path) == os.fspath(failing):
                    raise PermissionError("private details must not escape")
                return real_open(path, flags, mode)

            with patch(
                "notewitness.infrastructure.sqlite_job_store.os.open", side_effect=deny):
                with self.assertRaisesRegex(
                    JobStoreError,
                    "^database sidecar could not be secured$",
                ):
                    store._private_sidecars()

    def test_sidecar_chmod_uses_open_descriptor_after_pathname_swap(self) -> None:
        with TemporaryDirectory() as temporary:
            parent = Path(temporary)
            sidecar = parent / "jobs.sqlite-wal"
            secured = parent / "opened-sidecar"
            replacement = parent / "replacement"
            sidecar.write_bytes(b"opened first")
            replacement.write_bytes(b"swapped in later")
            sidecar.chmod(0o644)
            replacement.chmod(0o644)
            real_fchmod = os.fchmod

            def swap_then_chmod(descriptor: int, mode: int) -> None:
                sidecar.replace(secured)
                replacement.replace(sidecar)
                real_fchmod(descriptor, mode)

            with patch(
                "notewitness.infrastructure.sqlite_job_store.os.fchmod",
                side_effect=swap_then_chmod,
            ):
                _secure_private_sidecar(sidecar)

            self.assertEqual(0o600, secured.stat().st_mode & 0o777)
            self.assertEqual(0o644, sidecar.stat().st_mode & 0o777)

    def test_sidecar_close_failure_never_replaces_earlier_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "jobs.sqlite-wal"
            sidecar.write_bytes(b"sidecar")
            real_close = os.close

            def close_then_fail(descriptor: int) -> None:
                real_close(descriptor)
                raise OSError("close failure")

            with (
                patch(
                    "notewitness.infrastructure.sqlite_job_store.os.fchmod",
                    side_effect=OSError("chmod failure"),
                ),
                patch(
                    "notewitness.infrastructure.sqlite_job_store.os.close",
                    side_effect=close_then_fail,
                ),
                self.assertRaisesRegex(
                    JobStoreError,
                    "^database sidecar could not be secured$",
                ) as caught,
            ):
                _secure_private_sidecar(sidecar)

            self.assertEqual("chmod failure", str(caught.exception.__cause__))

    def test_sidecar_close_failure_replaces_success(self) -> None:
        with TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "jobs.sqlite-wal"
            sidecar.write_bytes(b"sidecar")
            real_close = os.close

            def close_then_fail(descriptor: int) -> None:
                real_close(descriptor)
                raise OSError("close failure")

            with (
                patch(
                    "notewitness.infrastructure.sqlite_job_store.os.close",
                    side_effect=close_then_fail,
                ),
                self.assertRaisesRegex(
                    JobStoreError,
                    "^database sidecar could not be secured$",
                ) as caught,
            ):
                _secure_private_sidecar(sidecar)

            self.assertEqual("close failure", str(caught.exception.__cause__))

    def test_sidecar_fstat_base_exception_closes_and_propagates_same_object(self) -> None:
        class SentinelFailure(BaseException):
            pass

        with TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "jobs.sqlite-wal"
            sidecar.write_bytes(b"sidecar")
            sentinel = SentinelFailure()
            descriptors: list[int] = []
            real_close = os.close

            def fail_fstat(descriptor: int) -> None:
                descriptors.append(descriptor)
                raise sentinel

            def close_then_fail(descriptor: int) -> None:
                real_close(descriptor)
                raise OSError("close failure")

            with (
                patch(
                    "notewitness.infrastructure.sqlite_job_store.os.fstat",
                    side_effect=fail_fstat,
                ),
                patch(
                    "notewitness.infrastructure.sqlite_job_store.os.close",
                    side_effect=close_then_fail,
                ) as close,
                self.assertRaises(SentinelFailure) as caught,
            ):
                _secure_private_sidecar(sidecar)

            self.assertIs(sentinel, caught.exception)
            close.assert_called_once_with(descriptors[0])
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])

    def test_sidecar_fchmod_base_exception_closes_and_propagates_same_object(self) -> None:
        class SentinelFailure(BaseException):
            pass

        with TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "jobs.sqlite-wal"
            sidecar.write_bytes(b"sidecar")
            sentinel = SentinelFailure()
            descriptors: list[int] = []
            real_close = os.close

            def fail_fchmod(descriptor: int, mode: int) -> None:
                descriptors.append(descriptor)
                raise sentinel

            def close_then_fail(descriptor: int) -> None:
                real_close(descriptor)
                raise OSError("close failure")

            with (
                patch(
                    "notewitness.infrastructure.sqlite_job_store.os.fchmod",
                    side_effect=fail_fchmod,
                ),
                patch(
                    "notewitness.infrastructure.sqlite_job_store.os.close",
                    side_effect=close_then_fail,
                ) as close,
                self.assertRaises(SentinelFailure) as caught,
            ):
                _secure_private_sidecar(sidecar)

            self.assertIs(sentinel, caught.exception)
            close.assert_called_once_with(descriptors[0])
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])
