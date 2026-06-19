from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from google import genai
from google.genai import types as genai_types
from pypdf import PdfReader

from app.ai.base import AIProvider, DailySummaryResult, FileSummaryResult, MealAnalysisResult, MediaSafetyResult
from app.ai.fake_provider import FakeAIProvider


def _safe_json_loads(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        return fallback


class GeminiAIProvider(AIProvider):
    def __init__(self, *, api_key: str | None, fast_model: str, smart_model: str):
        self.api_key = api_key
        self.fast_model = fast_model
        self.smart_model = smart_model
        self._client = genai.Client(api_key=api_key) if api_key else None
        self._fallback = FakeAIProvider()

    def _extract_text(self, filename: str, content: bytes) -> str:
        if filename.lower().endswith(".pdf"):
            try:
                reader = PdfReader(BytesIO(content))
                pages = [(page.extract_text() or "").strip() for page in reader.pages]
                text = "\n\n".join(page for page in pages if page)
                if text.strip():
                    return text
            except Exception:
                pass
        return content.decode("utf-8", errors="ignore")

    def _generate_json(self, *, prompt: str, model: str) -> dict[str, Any] | None:
        if not self._client:
            return None
        candidates = [candidate for candidate in [model, self.fast_model] if candidate]
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                response = self._client.models.generate_content(
                    model=candidate,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        temperature=0.2,
                        response_mime_type="application/json",
                    ),
                )
                return _safe_json_loads(response.text or "{}", {})
            except Exception:
                continue
        return None

    def summarize_file(self, *, filename: str, content: bytes, context: dict) -> FileSummaryResult:
        extracted_text = self._extract_text(filename, content).strip()
        if not extracted_text:
            return self._fallback.summarize_file(filename=filename, content=content, context=context)

        payload: dict[str, Any] | None = None
        for window_size in (2000, 1000, 500):
            summary_window = extracted_text[:window_size]
            prompt = f"""
Você é o agente do FitCopilot, um SaaS AI-first para personal trainers e nutricionistas.
Leia um documento do aluno e devolva JSON válido com:
- ai_summary: resumo executivo em português, útil para o profissional agir
- highlights: lista de pontos-chave
- structured_data: objeto com campos úteis quando existirem:
  goal, calories, macros, meals, restrictions, allergies, medications, injuries, hydration, sleep, recommendations

Contexto:
- aluno: {context.get("student_name", "Aluno")}
- arquivo: {filename}

Documento:
{summary_window}
"""
            payload = self._generate_json(prompt=prompt, model=self.smart_model)
            if payload:
                break
        if not payload:
            return self._fallback.summarize_file(filename=filename, content=content, context=context)

        structured_data = payload.get("structured_data") or {}
        highlights = payload.get("highlights") or []
        if highlights and "highlights" not in structured_data:
            structured_data["highlights"] = highlights

        fallback = self._fallback.summarize_file(filename=filename, content=content, context=context)
        return FileSummaryResult(
            extracted_text=extracted_text[:25000],
            ai_summary=payload.get("ai_summary") or fallback.ai_summary,
            structured_data=structured_data,
        )

    def summarize_student_day(self, *, context: dict) -> DailySummaryResult:
        payload = self._generate_json(
            prompt=f"""
Você é o FitCopilot. Analise o dia do aluno para o profissional agir rápido.
Retorne JSON com:
food_summary_text, activity_summary_text, overall_summary_text, ai_reading_text,
suggested_adjustment_text, suggested_message_text, risk_level.

Contexto JSON:
{json.dumps(context, ensure_ascii=False, default=str)}
""",
            model=self.smart_model,
        )
        if not payload:
            return self._fallback.summarize_student_day(context=context)
        return DailySummaryResult(
            food_summary_text=payload.get("food_summary_text") or "Sem leitura alimentar estruturada.",
            activity_summary_text=payload.get("activity_summary_text") or "Sem leitura de atividade estruturada.",
            overall_summary_text=payload.get("overall_summary_text") or "Sem resumo consolidado.",
            ai_reading_text=payload.get("ai_reading_text") or "Sem leitura disponível.",
            suggested_adjustment_text=payload.get("suggested_adjustment_text") or "Manter acompanhamento próximo.",
            suggested_message_text=payload.get("suggested_message_text") or "Como você está se sentindo hoje para ajustarmos seu plano?",
            risk_level=payload.get("risk_level") or "normal",
        )

    def suggest_message(self, *, context: dict) -> str:
        payload = self._generate_json(
            prompt=f"""
Você é o FitCopilot. Gere uma mensagem curta, profissional e calorosa para WhatsApp.
Retorne JSON com a chave message_text.

Contexto JSON:
{json.dumps(context, ensure_ascii=False, default=str)}
""",
            model=self.fast_model,
        )
        if not payload:
            return self._fallback.suggest_message(context=context)
        return payload.get("message_text") or self._fallback.suggest_message(context=context)

    def summarize_student_progress(self, *, context: dict) -> str:
        payload = self._generate_json(
            prompt=f"""
Você é o FitCopilot. Gere um resumo de progresso do aluno focado em aderência,
execução de treinos, contexto nutricional e próxima ação do profissional.
Retorne JSON com a chave summary_text.

Contexto JSON:
{json.dumps(context, ensure_ascii=False, default=str)}
""",
            model=self.smart_model,
        )
        if not payload:
            return self._fallback.summarize_student_progress(context=context)
        return payload.get("summary_text") or self._fallback.summarize_student_progress(context=context)

    def analyze_meal(self, *, context: dict) -> MealAnalysisResult:
        payload = self._generate_json(
            prompt=f"""
Voce e o FitCopilot. Analise uma refeicao descrita no WhatsApp e devolva JSON com:
estimated_calories, protein_grams, carbs_grams, fats_grams, summary_text, guidance_text.

Se faltar contexto ou a porcao for incerta, estime de forma prudente e evite subestimar pratos densos.
Arroz + feijao + carne/bife + batata frita normalmente fica mais perto de 700-950 kcal do que de 450 kcal.
Seja curto e util.

Contexto JSON:
{json.dumps(context, ensure_ascii=False, default=str)}
""",
            model=self.fast_model,
        )
        if not payload:
            return self._fallback.analyze_meal(context=context)
        fallback = self._fallback.analyze_meal(context=context)
        return MealAnalysisResult(
            estimated_calories=payload.get("estimated_calories") if payload.get("estimated_calories") is not None else fallback.estimated_calories,
            protein_grams=payload.get("protein_grams") if payload.get("protein_grams") is not None else fallback.protein_grams,
            carbs_grams=payload.get("carbs_grams") if payload.get("carbs_grams") is not None else fallback.carbs_grams,
            fats_grams=payload.get("fats_grams") if payload.get("fats_grams") is not None else fallback.fats_grams,
            summary_text=payload.get("summary_text") or fallback.summary_text,
            guidance_text=payload.get("guidance_text") or fallback.guidance_text,
        )

    def moderate_media(self, *, content: bytes, mime_type: str, context: dict) -> MediaSafetyResult:
        if not self._client:
            return self._fallback.moderate_media(content=content, mime_type=mime_type, context=context)

        prompt = f"""
Voce e uma camada de seguranca do FitCopilot antes da IA principal.
Classifique a imagem recebida no WhatsApp sem descrever detalhes explicitos.

Retorne JSON valido com:
- category: uma de safe_food, safe_body_progress, non_relevant, adult_nudity, sexual_content, violence, suspected_minor, unknown
- severity: allow, warn, block, critical
- allowed: boolean
- confidence: numero entre 0 e 1
- user_message: mensagem curta em portugues para o aluno quando allowed=false

Regras:
- Nudez, genitalia, conteudo sexual ou imagem intima: allowed=false, category adult_nudity ou sexual_content.
- Qualquer suspeita de menor em contexto sexual/intimo: allowed=false, severity=critical, category suspected_minor.
- Violencia/gore: allowed=false.
- Foto de refeicao: allowed=true, category safe_food.
- Foto de evolucao corporal sem nudez e sem sexualizacao: allowed=true, category safe_body_progress.
- Imagem segura mas fora de refeicao/treino/evolucao: allowed=false, category non_relevant.
- Se houver duvida, bloqueie.
- Nao use linguagem moralista. Nao descreva o conteudo explicito.

Contexto JSON:
{json.dumps(context, ensure_ascii=False, default=str)}
"""
        try:
            response = self._client.models.generate_content(
                model=self.fast_model,
                contents=[
                    genai_types.Part.from_bytes(data=content, mime_type=mime_type),
                    prompt,
                ],
                config=genai_types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
            payload = _safe_json_loads(response.text or "{}", {})
        except Exception:
            return self._fallback.moderate_media(content=content, mime_type=mime_type, context=context)

        category = str(payload.get("category") or "unknown")
        severity = str(payload.get("severity") or "block")
        allowed = bool(payload.get("allowed") is True and severity in {"allow", "warn"} and category in {"safe_food", "safe_body_progress"})
        if not allowed and category in {"safe_food", "safe_body_progress"}:
            category = "unknown"
            severity = "block"
        user_message = str(
            payload.get("user_message")
            or "Não consigo analisar esse tipo de imagem por aqui. Pode me mandar uma foto de refeição, treino, evolução física ou uma descrição em texto."
        )
        confidence = payload.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except Exception:
            confidence = None
        return MediaSafetyResult(
            allowed=allowed,
            category=category,
            severity=severity,
            user_message=user_message,
            confidence=confidence,
        )

    def generate_workout_insight(self, *, context: dict) -> str:
        payload = self._generate_json(
            prompt=f"""
Voce e o FitCopilot. Gere um insight curto de WhatsApp para o treino que o aluno escolheu hoje.
Considere objetivo, alimentacao do dia, aderencia recente e tom humano.
Retorne JSON com a chave insight_text. No maximo 3 frases curtas.

Contexto JSON:
{json.dumps(context, ensure_ascii=False, default=str)}
""",
            model=self.fast_model,
        )
        if not payload:
            return self._fallback.generate_workout_insight(context=context)
        return payload.get("insight_text") or self._fallback.generate_workout_insight(context=context)
