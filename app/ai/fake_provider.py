from __future__ import annotations

from app.ai.base import AIProvider, DailySummaryResult, FileSummaryResult, MealAnalysisResult, MediaSafetyResult


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
        intent = str(context.get("intent") or "acompanhar sua semana")
        if intent == "greeting":
            return f"Oi, {student_name.split()[0]}! 👋 Estou por aqui para te acompanhar hoje. Me manda como você está ou digita treino para ver sua ficha."
        if intent == "checkin":
            return f"Oi, {student_name.split()[0]}! Como você está se sentindo hoje? Se tiver treino ou refeição para registrar, pode me mandar por aqui. 💪"
        return f"Oi, {student_name.split()[0]}! Recebi sua mensagem e já deixei isso no acompanhamento ✅ Me manda qualquer detalhe que eu sigo te ajudando por aqui."

    def summarize_student_progress(self, *, context: dict) -> str:
        student_name = context["student_name"]
        return f"Resumo de progresso de {student_name}: acompanhamento consistente com foco em aderência, sinais recentes e próximos passos."

    def analyze_meal(self, *, context: dict) -> MealAnalysisResult:
        description = str(context.get("meal_description") or "refeição registrada")
        lower = description.lower()
        estimated_calories = 450
        protein = 28
        carbs = 42
        fats = 14
        if "2 ovos" in lower or "dois ovos" in lower:
            estimated_calories = 160
            protein = 13
            carbs = 1
            fats = 11
        elif "bolo" in lower or "chocolate" in lower:
            estimated_calories = 380
            protein = 5
            carbs = 52
            fats = 17
        elif "misto quente" in lower or ("presunto" in lower and "queijo" in lower):
            estimated_calories = 360
            protein = 20
            carbs = 32
            fats = 17
        elif "banana" in lower or "aveia" in lower:
            estimated_calories = 320
            protein = 12
            carbs = 48
            fats = 8
        elif "hamburg" in lower or "pizza" in lower or "lanche" in lower:
            estimated_calories = 780
            protein = 30
            carbs = 70
            fats = 38
        elif (
            ("arroz" in lower and "feij" in lower)
            and ("bife" in lower or "carne" in lower or "steak" in lower)
            and ("batata" in lower or "frita" in lower)
        ):
            estimated_calories = 820
            protein = 42
            carbs = 88
            fats = 34
        elif (
            ("arroz" in lower and "feij" in lower)
            and ("carne" in lower or "bolinha" in lower or "almond" in lower)
        ):
            estimated_calories = 825 if "2 prato" in lower or "dois prato" in lower else 620
            protein = 38
            carbs = 100 if estimated_calories > 700 else 72
            fats = 28
        return MealAnalysisResult(
            estimated_calories=estimated_calories,
            protein_grams=protein,
            carbs_grams=carbs,
            fats_grams=fats,
            summary_text=f"Registrei sua refeição com cerca de {estimated_calories} kcal.",
            guidance_text="Se conseguir, mantenha boa hidratação e siga a próxima refeição sem pular. 💧",
            items=[{"name": description.strip()[:60] or "Refeição", "quantity_estimate": "porção estimada", "calories": estimated_calories}],
            confidence=0.55,
        )

    def moderate_media(self, *, content: bytes, mime_type: str, context: dict) -> MediaSafetyResult:
        return MediaSafetyResult(
            allowed=False,
            category="unknown",
            severity="block",
            user_message="Não consegui validar essa imagem com segurança. Me descreve em texto que eu registro por aqui.",
            confidence=0.0,
        )

    def generate_workout_insight(self, *, context: dict) -> str:
        workout_label = context.get("workout_label", "Seu treino")
        daily_calories = context.get("daily_calories")
        if daily_calories:
            return (
                f"{workout_label}: hoje vale focar em boa execução e constância. "
                f"Até agora você tem cerca de {daily_calories} kcal no dia, então tenta manter energia e hidratação."
            )
        return (
            f"{workout_label}: hoje vale focar em boa execução, ritmo constante e sem pular exercícios principais. "
            "Quando concluir, me manda aqui como você se sentiu."
        )
