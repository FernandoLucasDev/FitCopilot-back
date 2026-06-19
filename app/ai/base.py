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


@dataclass
class MealAnalysisResult:
    estimated_calories: int | None
    protein_grams: int | None
    carbs_grams: int | None
    fats_grams: int | None
    summary_text: str
    guidance_text: str


@dataclass
class MediaSafetyResult:
    allowed: bool
    category: str
    severity: str
    user_message: str
    confidence: float | None = None


class AIProvider:
    def summarize_file(self, *, filename: str, content: bytes, context: dict) -> FileSummaryResult:
        raise NotImplementedError

    def summarize_student_day(self, *, context: dict) -> DailySummaryResult:
        raise NotImplementedError

    def suggest_message(self, *, context: dict) -> str:
        raise NotImplementedError

    def summarize_student_progress(self, *, context: dict) -> str:
        raise NotImplementedError

    def analyze_meal(self, *, context: dict) -> MealAnalysisResult:
        raise NotImplementedError

    def moderate_media(self, *, content: bytes, mime_type: str, context: dict) -> MediaSafetyResult:
        raise NotImplementedError

    def generate_workout_insight(self, *, context: dict) -> str:
        raise NotImplementedError
