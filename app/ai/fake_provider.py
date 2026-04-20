from __future__ import annotations

from app.ai.base import AIProvider, DailySummaryResult, FileSummaryResult


class FakeAIProvider(AIProvider):
    def summarize_file(self, *, filename: str, content: bytes, context: dict) -> FileSummaryResult:
        preview = content[:500].decode("utf-8", errors="ignore") or "Arquivo lido com sucesso."
        student_name = context.get("student_name", "Aluno")
        return FileSummaryResult(
            extracted_text=preview,
            ai_summary=f"{student_name}: arquivo {filename} analisado. Há contexto útil para acompanhamento e próximos ajustes.",
            structured_data={
                "source_filename": filename,
                "highlights": [
                    "Documento processado em ambiente de desenvolvimento",
                    "Resumo fake pronto para substituir por provider real depois",
                ],
            },
        )

    def summarize_student_day(self, *, context: dict) -> DailySummaryResult:
        student_name = context["student_name"]
        signals = context.get("signals", [])
        interactions = context.get("interactions", [])
        last_signal = signals[0]["title"] if signals else "Sem novos sinais hoje"
        risk_level = "attention" if context.get("score", 70) < 60 else "normal"
        return DailySummaryResult(
            food_summary_text="Alimentação com sinais parciais no dia." if signals else "Sem dados alimentares suficientes.",
            activity_summary_text=f"{len(signals)} sinais e {len(interactions)} interações considerados.",
            overall_summary_text=f"{student_name} está em acompanhamento com leitura pragmática do dia.",
            ai_reading_text=f"Último ponto observado: {last_signal}.",
            suggested_adjustment_text="Priorizar contato humano leve e revisar aderência antes de aumentar demanda.",
            suggested_message_text=f"Oi {student_name.split()[0]}, passei aqui para entender como foi seu dia e ajustar o plano contigo se precisar.",
            risk_level=risk_level,
        )

    def suggest_message(self, *, context: dict) -> str:
        student_name = context["student_name"]
        reason = context.get("reason", "acompanhar sua semana")
        return f"Oi {student_name.split()[0]}! Passei para {reason}. Se fizer sentido, me responde aqui que eu ajusto seu plano com você."

    def summarize_student_progress(self, *, context: dict) -> str:
        student_name = context["student_name"]
        return f"Resumo de progresso de {student_name}: acompanhamento consistente com foco em aderência, sinais recentes e próximos passos."
