from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileSummaryResult:
    extracted_text: str
    ai_summary: str
    structured_data: dict


@dataclass
class DailySummaryResult:
    food_summary_text: str
    activity_summary_text: str
    overall_summary_text: str
    ai_reading_text: str
    suggested_adjustment_text: str
    suggested_message_text: str
    risk_level: str


class AIProvider:
    def summarize_file(self, *, filename: str, content: bytes, context: dict) -> FileSummaryResult:
        raise NotImplementedError

    def summarize_student_day(self, *, context: dict) -> DailySummaryResult:
        raise NotImplementedError

    def suggest_message(self, *, context: dict) -> str:
        raise NotImplementedError

    def summarize_student_progress(self, *, context: dict) -> str:
        raise NotImplementedError
