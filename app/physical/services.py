from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from io import BytesIO
import re
import unicodedata
import zipfile
from xml.etree import ElementTree

from flask import current_app
from pypdf import PdfReader

from app.common.api import ApiError
from app.extensions import db
from app.files.models import StudentFile
from app.files.services import create_student_file
from app.physical.models import (
    PhysicalAssessment,
    PhysicalAssessmentAIRun,
    PhysicalAssessmentComparison,
    PhysicalAssessmentPhoto,
)
from app.students.models import StudentDailySignal, StudentInteraction
from app.students.services import require_student
from app.whatsapp.services import send_manual_whatsapp_message


PHOTO_TYPES = {"front", "back", "left_side", "right_side"}
NUMERIC_FIELDS = {
    "weight_kg",
    "height_cm",
    "bmi",
    "body_fat_percentage",
    "lean_mass_kg",
    "fat_mass_kg",
    "basal_metabolic_rate",
    "visceral_fat_level",
    "body_age",
    "hydration_percentage",
    "chest_cm",
    "waist_cm",
    "abdomen_cm",
    "hip_cm",
    "left_arm_relaxed_cm",
    "right_arm_relaxed_cm",
    "left_arm_contracted_cm",
    "right_arm_contracted_cm",
    "left_forearm_cm",
    "right_forearm_cm",
    "left_thigh_cm",
    "right_thigh_cm",
    "left_calf_cm",
    "right_calf_cm",
    "neck_cm",
    "shoulders_cm",
    "resting_heart_rate",
}
TEXT_FIELDS = {
    "title",
    "notes",
    "blood_pressure",
    "posture_notes",
    "mobility_notes",
    "injury_notes",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_physical_assessment(*, account_id, student_id, actor_user_id, data: dict, files: dict | None = None) -> PhysicalAssessment:
    student = require_student(account_id, student_id)
    assessment_date = _parse_date(data.get("assessment_date")) or date.today()
    values = {field: _decimal_or_none(data.get(field)) for field in NUMERIC_FIELDS if data.get(field) not in (None, "")}
    values.update({field: str(data[field]).strip() for field in TEXT_FIELDS if data.get(field)})
    if not values.get("bmi"):
        values["bmi"] = _calculate_bmi(values.get("weight_kg"), values.get("height_cm") or student.height_cm)
    if not values.get("height_cm") and student.height_cm:
        values["height_cm"] = student.height_cm

    assessment = PhysicalAssessment(
        account_id=account_id,
        student_id=student.id,
        created_by_user_id=actor_user_id,
        assessment_date=assessment_date,
        **values,
    )
    db.session.add(assessment)
    db.session.flush()

    _attach_photos(assessment=assessment, account_id=account_id, student_id=student.id, files=files or {})
    generate_physical_assessment_insights(assessment)

    student.current_weight_kg = assessment.weight_kg or student.current_weight_kg
    student.height_cm = assessment.height_cm or student.height_cm
    student.last_activity_at = utcnow()
    student.last_signal_summary = assessment.ai_summary or assessment.assessment_summary

    db.session.add(
        StudentDailySignal(
            account_id=account_id,
            student_id=student.id,
            signal_date=assessment.assessment_date,
            signal_type="physical_assessment",
            source="professional",
            title="Avaliação física registrada",
            body=assessment.ai_summary or assessment.assessment_summary,
            payload_json={"assessment_id": str(assessment.id), "weight_kg": _float(assessment.weight_kg), "body_fat_percentage": _float(assessment.body_fat_percentage)},
            created_by_user_id=actor_user_id,
            created_at=utcnow(),
        )
    )
    db.session.add(
        StudentInteraction(
            account_id=account_id,
            student_id=student.id,
            interaction_type="physical_assessment",
            channel="app",
            title="Avaliação física",
            body=assessment.ai_summary or assessment.assessment_summary,
            created_by_user_id=actor_user_id,
            interaction_at=utcnow(),
            created_at=utcnow(),
        )
    )
    db.session.commit()
    return assessment


def create_physical_assessment_from_document(*, account_id, student_id, actor_user_id, data: dict, uploaded_file) -> PhysicalAssessment:
    if uploaded_file is None:
        raise ApiError("Arquivo de avaliação ausente", HTTPStatus.BAD_REQUEST)

    student_file = create_student_file(
        account_id=account_id,
        student_id=student_id,
        actor_user_id=actor_user_id,
        title=data.get("title") or "Avaliação física importada",
        file_category="physical_evaluation",
        uploaded_file=uploaded_file,
    )
    storage = current_app.extensions["storage_provider"]
    content = storage.open_bytes(student_file.storage_key)
    extracted_text = _extract_assessment_document_text(student_file.original_filename, student_file.mime_type, content)
    ai_result = current_app.extensions["ai_provider"].summarize_file(
        filename=student_file.original_filename,
        content=content,
        context={"student_name": student_file.student.full_name, "document_type": "physical_assessment"},
    )
    merged_text = "\n".join(part for part in [extracted_text, ai_result.extracted_text, ai_result.ai_summary] if part)
    extracted_values = _extract_physical_values_from_text(merged_text)
    extracted_values.update(_structured_physical_values(ai_result.structured_data or {}))
    extracted_values = {key: value for key, value in extracted_values.items() if value not in (None, "")}

    student_file.extracted_text = merged_text[:25000]
    student_file.ai_summary = ai_result.ai_summary
    student_file.extracted_structured_json = {**(ai_result.structured_data or {}), "physical_values": extracted_values}
    student_file.extraction_status = "completed"

    assessment_data = {
        **extracted_values,
        **{key: value for key, value in data.items() if value not in (None, "")},
        "title": data.get("title") or f"Avaliação importada - {student_file.original_filename}",
        "notes": data.get("notes") or f"Dados extraídos do arquivo {student_file.original_filename}. {ai_result.ai_summary}",
    }
    assessment = create_physical_assessment(
        account_id=account_id,
        student_id=student_id,
        actor_user_id=actor_user_id,
        data=assessment_data,
        files={},
    )
    student_file.extracted_structured_json = {
        **(student_file.extracted_structured_json or {}),
        "created_physical_assessment_id": str(assessment.id),
    }
    db.session.commit()
    return assessment


def list_physical_assessments(*, account_id, student_id) -> list[PhysicalAssessment]:
    require_student(account_id, student_id)
    return (
        PhysicalAssessment.query.filter_by(account_id=account_id, student_id=student_id)
        .order_by(PhysicalAssessment.assessment_date.desc(), PhysicalAssessment.created_at.desc())
        .all()
    )


def require_assessment(account_id, student_id, assessment_id) -> PhysicalAssessment:
    assessment = PhysicalAssessment.query.filter_by(account_id=account_id, student_id=student_id, id=assessment_id).first()
    if not assessment:
        raise ApiError("Avaliação física não encontrada", HTTPStatus.NOT_FOUND)
    return assessment


def generate_physical_assessment_insights(assessment: PhysicalAssessment | str) -> PhysicalAssessment:
    if not isinstance(assessment, PhysicalAssessment):
        assessment = PhysicalAssessment.query.get(assessment)
    if not assessment:
        raise ApiError("Avaliação física não encontrada", HTTPStatus.NOT_FOUND)

    previous = _previous_assessment(assessment)
    changes = _build_changes(previous, assessment) if previous else {}
    summary, insights, recommendations = _build_operational_reading(assessment, changes, bool(previous))

    assessment.assessment_summary = summary
    assessment.ai_summary = summary
    assessment.ai_insights = insights
    assessment.ai_recommendations = recommendations
    structured = {"summary": summary, "insights": insights, "recommendations": recommendations, "changes": changes}
    db.session.add(
        PhysicalAssessmentAIRun(
            assessment_id=assessment.id,
            provider="fitcopilot",
            model="rules-v1",
            prompt_version="physical-assessment-v1",
            raw_response=summary,
            structured_output=structured,
            created_at=utcnow(),
        )
    )
    if previous:
        comparison = PhysicalAssessmentComparison(
            student_id=assessment.student_id,
            from_assessment_id=previous.id,
            to_assessment_id=assessment.id,
            summary=summary,
            changes_json=changes,
            created_at=utcnow(),
        )
        db.session.add(comparison)
    db.session.flush()
    return assessment


def send_physical_assessment_whatsapp_summary(*, account_id, student_id, assessment_id, actor_user_id):
    student = require_student(account_id, student_id)
    assessment = require_assessment(account_id, student_id, assessment_id)
    changes = latest_physical_progress(student_id) or {}
    progress = changes.get("headline") or assessment.ai_summary or "Sua avaliação física foi registrada."
    message = (
        f"Olá {student.full_name.split()[0]}, sua avaliação física foi registrada.\n\n"
        f"{progress}\n\n"
        "Use isso como ponto de partida para acompanhar sua evolução com calma e consistência. 💪"
    )
    return send_manual_whatsapp_message(
        student=student,
        actor_user_id=actor_user_id,
        message_text=message,
        message_type="physical_assessment",
    )


def latest_physical_progress(student_id) -> dict | None:
    latest = (
        PhysicalAssessment.query.filter_by(student_id=student_id)
        .order_by(PhysicalAssessment.assessment_date.desc(), PhysicalAssessment.created_at.desc())
        .first()
    )
    if not latest:
        return None
    previous = _previous_assessment(latest)
    changes = _build_changes(previous, latest) if previous else {}
    headline = latest.ai_summary or latest.assessment_summary
    if changes.get("waist_cm", {}).get("delta") is not None:
        delta = changes["waist_cm"]["delta"]
        direction = "reduziu" if delta < 0 else "aumentou"
        headline = f"{direction.capitalize()} {abs(delta):.1f} cm de cintura desde a última avaliação."
    return {
        "latestAssessmentId": str(latest.id),
        "count": PhysicalAssessment.query.filter_by(student_id=student_id).count(),
        "headline": headline,
        "changes": changes,
    }


def serialize_assessment(assessment: PhysicalAssessment) -> dict:
    comparison = (
        PhysicalAssessmentComparison.query.filter_by(to_assessment_id=assessment.id)
        .order_by(PhysicalAssessmentComparison.created_at.desc())
        .first()
    )
    return {
        "id": str(assessment.id),
        "title": assessment.title or "Avaliação física",
        "notes": assessment.notes,
        "assessmentDate": assessment.assessment_date.isoformat(),
        "weightKg": _float(assessment.weight_kg),
        "heightCm": _float(assessment.height_cm),
        "bmi": _float(assessment.bmi),
        "bodyFatPercentage": _float(assessment.body_fat_percentage),
        "leanMassKg": _float(assessment.lean_mass_kg),
        "fatMassKg": _float(assessment.fat_mass_kg),
        "basalMetabolicRate": _float(assessment.basal_metabolic_rate),
        "visceralFatLevel": _float(assessment.visceral_fat_level),
        "bodyAge": _float(assessment.body_age),
        "hydrationPercentage": _float(assessment.hydration_percentage),
        "measurements": {
            "chestCm": _float(assessment.chest_cm),
            "waistCm": _float(assessment.waist_cm),
            "abdomenCm": _float(assessment.abdomen_cm),
            "hipCm": _float(assessment.hip_cm),
            "leftArmRelaxedCm": _float(assessment.left_arm_relaxed_cm),
            "rightArmRelaxedCm": _float(assessment.right_arm_relaxed_cm),
            "leftArmContractedCm": _float(assessment.left_arm_contracted_cm),
            "rightArmContractedCm": _float(assessment.right_arm_contracted_cm),
            "leftForearmCm": _float(assessment.left_forearm_cm),
            "rightForearmCm": _float(assessment.right_forearm_cm),
            "leftThighCm": _float(assessment.left_thigh_cm),
            "rightThighCm": _float(assessment.right_thigh_cm),
            "leftCalfCm": _float(assessment.left_calf_cm),
            "rightCalfCm": _float(assessment.right_calf_cm),
            "neckCm": _float(assessment.neck_cm),
            "shouldersCm": _float(assessment.shoulders_cm),
        },
        "health": {
            "restingHeartRate": _float(assessment.resting_heart_rate),
            "bloodPressure": assessment.blood_pressure,
            "postureNotes": assessment.posture_notes,
            "mobilityNotes": assessment.mobility_notes,
            "injuryNotes": assessment.injury_notes,
        },
        "summary": assessment.assessment_summary,
        "aiSummary": assessment.ai_summary,
        "aiInsights": assessment.ai_insights or [],
        "aiRecommendations": assessment.ai_recommendations or [],
        "comparison": {
            "summary": comparison.summary,
            "changes": comparison.changes_json,
        }
        if comparison
        else None,
        "photos": [
            {
                "id": str(photo.id),
                "type": photo.photo_type,
                "url": photo.file_url,
                "fileKey": photo.file_key,
            }
            for photo in sorted(assessment.photos, key=lambda item: item.photo_type)
        ],
        "createdAt": assessment.created_at.isoformat(),
        "updatedAt": assessment.updated_at.isoformat(),
    }


def _attach_photos(*, assessment: PhysicalAssessment, account_id, student_id, files: dict) -> None:
    if not files:
        return
    storage = current_app.extensions["storage_provider"]
    for photo_type in PHOTO_TYPES:
        uploaded = files.get(photo_type)
        if not uploaded:
            continue
        if uploaded.mimetype not in {"image/jpeg", "image/png", "image/webp"}:
            raise ApiError("Foto de avaliação deve ser PNG, JPG ou WEBP", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        content = uploaded.read()
        if not content:
            continue
        stored = storage.save(
            f"accounts/{account_id}/students/{student_id}/physical-assessments/{assessment.id}",
            uploaded.filename,
            content,
            uploaded.mimetype,
        )
        db.session.add(
            PhysicalAssessmentPhoto(
                assessment_id=assessment.id,
                file_key=stored.storage_key,
                file_url=stored.file_url,
                storage_provider=current_app.config.get("STORAGE_PROVIDER", "local"),
                photo_type=photo_type,
                created_at=utcnow(),
            )
        )


def _previous_assessment(assessment: PhysicalAssessment) -> PhysicalAssessment | None:
    return (
        PhysicalAssessment.query.filter(
            PhysicalAssessment.student_id == assessment.student_id,
            PhysicalAssessment.id != assessment.id,
            PhysicalAssessment.assessment_date <= assessment.assessment_date,
        )
        .order_by(PhysicalAssessment.assessment_date.desc(), PhysicalAssessment.created_at.desc())
        .first()
    )


def _build_changes(previous: PhysicalAssessment | None, current: PhysicalAssessment) -> dict:
    if not previous:
        return {}
    fields = {
        "weight_kg": "Peso",
        "body_fat_percentage": "BF%",
        "lean_mass_kg": "Massa magra",
        "fat_mass_kg": "Massa gorda",
        "waist_cm": "Cintura",
        "abdomen_cm": "Abdômen",
        "hip_cm": "Quadril",
        "chest_cm": "Peito",
        "left_thigh_cm": "Coxa esquerda",
        "right_thigh_cm": "Coxa direita",
    }
    changes = {}
    for field, label in fields.items():
        before = getattr(previous, field)
        after = getattr(current, field)
        if before is None or after is None:
            continue
        delta = float(after) - float(before)
        changes[field] = {"label": label, "from": float(before), "to": float(after), "delta": round(delta, 2)}
    return changes


def _build_operational_reading(assessment: PhysicalAssessment, changes: dict, has_previous: bool) -> tuple[str, list[str], list[str]]:
    insights: list[str] = []
    recommendations: list[str] = []
    if not has_previous:
        summary = "Primeira avaliação física registrada. Agora existe uma linha de base para acompanhar evolução real."
        insights.append("Linha de base corporal criada para peso, medidas, composição e fotos.")
        recommendations.append("Reavaliar em 30 a 45 dias para comparar tendência, não apenas número isolado.")
    else:
        waist_delta = changes.get("waist_cm", {}).get("delta")
        bf_delta = changes.get("body_fat_percentage", {}).get("delta")
        lean_delta = changes.get("lean_mass_kg", {}).get("delta")
        if waist_delta is not None:
            insights.append(("Cintura reduziu" if waist_delta < 0 else "Cintura aumentou") + f" {abs(waist_delta):.1f} cm desde a última avaliação.")
        if bf_delta is not None:
            insights.append(("Percentual de gordura caiu" if bf_delta < 0 else "Percentual de gordura subiu") + f" {abs(bf_delta):.1f} ponto(s).")
        if lean_delta is not None:
            insights.append(("Massa magra aumentou" if lean_delta > 0 else "Massa magra reduziu") + f" {abs(lean_delta):.1f} kg.")
        if not insights:
            insights.append("Avaliação comparada com a anterior, sem variações suficientes nas métricas preenchidas.")
        positive = any(item.get("delta", 0) < 0 for key, item in changes.items() if key in {"waist_cm", "abdomen_cm", "body_fat_percentage"})
        summary = "Evolução corporal positiva detectada." if positive else "Avaliação atualizada; acompanhar tendência nas próximas medições."
        recommendations.append("Manter leitura por tendência e cruzar medidas com treino, sono e alimentação da semana.")
        if lean_delta is not None and lean_delta <= 0:
            recommendations.append("Reforçar ingestão proteica e progressão de carga para proteger massa magra.")
        if bf_delta is not None and bf_delta > 0:
            recommendations.append("Revisar aderência alimentar e volume de atividade antes de aumentar restrição.")
    if assessment.posture_notes or assessment.mobility_notes or assessment.injury_notes:
        insights.append("Há observações de postura, mobilidade ou lesões para considerar no planejamento.")
    if not recommendations:
        recommendations.append("Usar essa avaliação para ajustar metas simples e visíveis para o próximo ciclo.")
    return summary, insights[:4], recommendations[:4]


def _extract_assessment_document_text(filename: str, mime_type: str, content: bytes) -> str:
    lower = (filename or "").lower()
    try:
        if mime_type == "application/pdf" or lower.endswith(".pdf"):
            reader = PdfReader(BytesIO(content))
            return "\n".join((page.extract_text() or "").strip() for page in reader.pages)
        if lower.endswith(".docx"):
            return _extract_docx_text(content)
        if lower.endswith(".xlsx"):
            return _extract_xlsx_text(content)
        return content.decode("utf-8", errors="ignore")
    except Exception:
        return content.decode("utf-8", errors="ignore")


def _extract_docx_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    text_nodes = []
    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            text_nodes.append(node.text)
    return " ".join(text_nodes)


def _extract_xlsx_text(content: bytes) -> str:
    values: list[str] = []
    with zipfile.ZipFile(BytesIO(content)) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter():
                if item.tag.endswith("}t") and item.text:
                    shared_strings.append(item.text)
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                continue
            root = ElementTree.fromstring(archive.read(name))
            for cell in root.iter():
                if not cell.tag.endswith("}c"):
                    continue
                cell_type = cell.attrib.get("t")
                raw = None
                for child in cell:
                    if child.tag.endswith("}v"):
                        raw = child.text
                        break
                if raw is None:
                    continue
                if cell_type == "s" and raw.isdigit() and int(raw) < len(shared_strings):
                    values.append(shared_strings[int(raw)])
                else:
                    values.append(raw)
    return " ".join(values)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _extract_physical_values_from_text(text: str) -> dict:
    aliases = {
        "weight_kg": ["peso", "weight"],
        "height_cm": ["altura", "estatura", "height"],
        "bmi": ["imc", "bmi"],
        "body_fat_percentage": ["gordura corporal", "percentual de gordura", "% gordura corporal", "bf", "body fat"],
        "lean_mass_kg": ["massa magra", "peso magro", "lean mass"],
        "fat_mass_kg": ["massa gorda", "peso gorduroso", "fat mass"],
        "basal_metabolic_rate": ["metabolismo basal", "bmr", "tmb"],
        "visceral_fat_level": ["gordura visceral", "visceral"],
        "body_age": ["idade corporal"],
        "hydration_percentage": ["hidratacao"],
        "chest_cm": ["peito", "torax"],
        "waist_cm": ["cintura"],
        "abdomen_cm": ["abdomen", "abdominal"],
        "hip_cm": ["quadril"],
        "shoulders_cm": ["ombros", "ombro"],
        "neck_cm": ["pescoco"],
        "left_arm_relaxed_cm": ["braco esquerdo relaxado", "braco e", "braco esquerdo"],
        "right_arm_relaxed_cm": ["braco direito relaxado", "braco d", "braco direito"],
        "left_arm_contracted_cm": ["braco esquerdo contraido"],
        "right_arm_contracted_cm": ["braco direito contraido"],
        "left_thigh_cm": ["coxa esquerda"],
        "right_thigh_cm": ["coxa direita"],
        "left_calf_cm": ["panturrilha esquerda"],
        "right_calf_cm": ["panturrilha direita"],
        "resting_heart_rate": ["frequencia cardiaca", "fc repouso"],
    }
    values: dict[str, str] = {}
    normalized = _normalize_text(text).replace("\r", "\n")
    for field, labels in aliases.items():
        for label in labels:
            pattern = rf"\b{re.escape(label)}\b\s*(?:\([^)\n]*\))?\s*[:\-]?\s*(\d+(?:[,.]\d+)?)\s*(?:kg|cm|m|%|bpm|kcal)?"
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if match:
                values[field] = match.group(1).replace(",", ".")
                break
    if values.get("height_cm"):
        try:
            height = Decimal(values["height_cm"])
            if height <= 3:
                values["height_cm"] = str(height * 100)
        except InvalidOperation:
            pass
    pressure = re.search(r"(?:pressao|pa|blood pressure)\s*[:\-]?\s*(\d{2,3}\s*/\s*\d{2,3})", normalized, flags=re.IGNORECASE)
    if pressure:
        values["blood_pressure"] = pressure.group(1).replace(" ", "")
    return values


def _structured_physical_values(data: dict) -> dict:
    values: dict[str, str] = {}
    candidates = {**data, **(data.get("physical_values") or {}), **(data.get("measurements") or {})}
    key_map = {
        "weight": "weight_kg",
        "weight_kg": "weight_kg",
        "height": "height_cm",
        "height_cm": "height_cm",
        "body_fat": "body_fat_percentage",
        "body_fat_percentage": "body_fat_percentage",
        "lean_mass": "lean_mass_kg",
        "lean_mass_kg": "lean_mass_kg",
        "fat_mass": "fat_mass_kg",
        "fat_mass_kg": "fat_mass_kg",
        "waist": "waist_cm",
        "waist_cm": "waist_cm",
        "abdomen": "abdomen_cm",
        "abdomen_cm": "abdomen_cm",
        "hip": "hip_cm",
        "hip_cm": "hip_cm",
        "chest": "chest_cm",
        "chest_cm": "chest_cm",
    }
    for source_key, target_key in key_map.items():
        value = candidates.get(source_key)
        if isinstance(value, (int, float, str)) and str(value).strip():
            values[target_key] = str(value).replace(",", ".")
    return values


def _decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ApiError("Valor numérico inválido na avaliação física", HTTPStatus.BAD_REQUEST) from None


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ApiError("Data da avaliação inválida", HTTPStatus.BAD_REQUEST) from None


def _calculate_bmi(weight: Decimal | None, height_cm: Decimal | None) -> Decimal | None:
    if not weight or not height_cm:
        return None
    height_m = Decimal(height_cm) / Decimal("100")
    if height_m <= 0:
        return None
    return (Decimal(weight) / (height_m * height_m)).quantize(Decimal("0.01"))


def _float(value) -> float | None:
    return float(value) if value is not None else None
