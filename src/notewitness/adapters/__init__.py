"""Contracts for local analysis adapters."""

from notewitness.adapters.base import AnalysisAdapter, Hypothesis, SourceSpan
from notewitness.adapters.analysis_cli import (
    AnalysisCLIError,
    AnalysisCLIExecutionError,
    LocalAnalysisCLIAdapter,
    LocalAnalysisCLIExecution,
    LocalAnalysisCLISettings,
    LocalAnalysisSource,
)

__all__ = [
    "AnalysisAdapter",
    "AnalysisCLIError",
    "AnalysisCLIExecutionError",
    "Hypothesis",
    "LocalAnalysisCLIAdapter",
    "LocalAnalysisCLIExecution",
    "LocalAnalysisCLISettings",
    "LocalAnalysisSource",
    "SourceSpan",
]
