from __future__ import annotations

from dataclasses import dataclass

from flask import current_app


@dataclass
class AgentResult:
    status: str
    task_type: str
    model: str
    result: dict


class FitCopilotAgent:
    def _resolve_model(self, task_type: str) -> str:
        if current_app.config.get("AI_PROVIDER") == "gemini":
            if task_type in {"STUDENT_DAILY_READING", "PROGRESS_REPORT", "FILE_SUMMARY"}:
                return current_app.config.get("GEMINI_MODEL_SMART", "gemini-2.5-flash")
            return current_app.config.get("GEMINI_MODEL_FAST", "gemini-2.0-flash")
        if task_type in {"STUDENT_DAILY_READING", "PROGRESS_REPORT", "FILE_SUMMARY"}:
            return current_app.config.get("LOCAL_AI_MODEL_SMART", "fitcopilot-smart")
        return current_app.config.get("LOCAL_AI_MODEL_FAST", "fitcopilot-fast")

    def process_request(self, *, task_type: str, payload: dict) -> AgentResult:
        provider = current_app.extensions["ai_provider"]
        model = self._resolve_model(task_type)
        if task_type == "WORKSPACE_OVERVIEW":
            result = {
                "headline": "Priorize alunos com risco operacional hoje.",
                "action": "Abra a carteira e trate primeiro quem está sem sinal ou com queda de aderência.",
            }
        elif task_type == "STUDENT_DAILY_READING":
            summary = provider.summarize_student_day(context=payload)
            result = {
                "ai_reading_text": summary.ai_reading_text,
                "suggested_adjustment_text": summary.suggested_adjustment_text,
                "suggested_message_text": summary.suggested_message_text,
                "risk_level": summary.risk_level,
            }
        elif task_type == "MESSAGE_SUGGESTION":
            result = {"message_text": provider.suggest_message(context=payload)}
        elif task_type == "FILE_SUMMARY":
            raw_content = (payload.get("content") or "").encode("utf-8")
            summary = provider.summarize_file(
                filename=payload.get("filename", "arquivo.txt"),
                content=raw_content,
                context=payload,
            )
            result = {
                "extracted_text": summary.extracted_text,
                "ai_summary": summary.ai_summary,
                "structured_data": summary.structured_data,
            }
        else:
            result = {"summary_text": provider.summarize_student_progress(context=payload)}
        return AgentResult(status="SUCCESS", task_type=task_type, model=model, result=result)


fitcopilot_agent = FitCopilotAgent()
