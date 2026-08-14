"""Lease renewal isolated from resumable analysis coordination."""

from __future__ import annotations

import threading

from notewitness.application._resumable_analysis_artifacts import ResumableAnalysisError
from notewitness.infrastructure.sqlite_job_store import SQLiteJobStore


_MAX_LEASE_RENEWAL_SECONDS = 30.0


class LeaseRenewer:
    """Renew a stage lease without giving the adapter store ownership."""

    def __init__(
        self,
        store: SQLiteJobStore,
        job_id: str,
        owner_id: str,
        lease_seconds: float,
    ) -> None:
        self._store = store
        self._job_id = job_id
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._interval_seconds = min(lease_seconds / 3, _MAX_LEASE_RENEWAL_SECONDS)
        self._stopped = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._renew, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join()

    def raise_if_lost(self) -> None:
        if self._failure is not None:
            raise self._failure

    def _renew(self) -> None:
        while not self._stopped.wait(self._interval_seconds):
            try:
                self._store.heartbeat(
                    self._job_id,
                    owner_id=self._owner_id,
                    lease_seconds=self._lease_seconds,
                )
            except BaseException as exc:
                self._failure = exc
                self._stopped.set()
                return
