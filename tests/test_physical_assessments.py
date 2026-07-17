from __future__ import annotations

from io import BytesIO
from pathlib import Path

from app.physical.models import PhysicalAssessment, PhysicalAssessmentComparison, PhysicalAssessmentPhoto


def test_physical_assessment_history_comparison_and_panel(client, auth_headers, seeded_data):
    student_id = seeded_data["student"].id

    first = client.post(
        f"/api/v1/students/{student_id}/physical-assessments",
        headers=auth_headers,
        json={
            "title": "Avaliação inicial",
            "assessment_date": "2026-04-01",
            "weight_kg": 82,
            "height_cm": 178,
            "body_fat_percentage": 20,
            "lean_mass_kg": 65,
            "waist_cm": 92,
            "abdomen_cm": 96,
        },
    )
    assert first.status_code == 201
    first_payload = first.get_json()["data"]["assessment"]
    assert first_payload["bmi"] is not None
    assert "linha de base" in first_payload["aiSummary"].lower()

    second = client.post(
        f"/api/v1/students/{student_id}/physical-assessments",
        headers=auth_headers,
        json={
            "title": "Reavaliação",
            "assessment_date": "2026-05-01",
            "weight_kg": 80.5,
            "height_cm": 178,
            "body_fat_percentage": 18.5,
            "lean_mass_kg": 65.8,
            "waist_cm": 89.5,
            "abdomen_cm": 93,
        },
    )
    assert second.status_code == 201
    second_payload = second.get_json()["data"]["assessment"]
    assert second_payload["comparison"]["changes"]["waist_cm"]["delta"] == -2.5
    assert PhysicalAssessment.query.filter_by(student_id=student_id).count() == 2
    assert PhysicalAssessmentComparison.query.filter_by(student_id=student_id).count() == 1

    panel = client.get(f"/api/v1/students/{student_id}/panel", headers=auth_headers)
    assert panel.status_code == 200
    panel_payload = panel.get_json()["data"]
    assert len(panel_payload["physicalAssessments"]) == 2
    assert "cintura" in panel_payload["physicalProgress"]["headline"].lower()


def test_physical_assessment_accepts_photo_upload(client, auth_headers, seeded_data):
    student_id = seeded_data["student"].id
    response = client.post(
        f"/api/v1/students/{student_id}/physical-assessments",
        headers=auth_headers,
        data={
            "title": "Avaliação com foto",
            "assessment_date": "2026-05-10",
            "weight_kg": "78",
            "front": (BytesIO(b"fake image bytes"), "front.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    payload = response.get_json()["data"]["assessment"]
    assert payload["photos"][0]["type"] == "front"
    assert PhysicalAssessmentPhoto.query.count() == 1


def test_physical_assessment_imports_real_pdf_document(client, auth_headers, seeded_data):
    student_id = seeded_data["student"].id
    pdf_path = Path(__file__).resolve().parents[2] / "Dados e Plano Alimentar - Fernando.pdf"

    response = client.post(
        f"/api/v1/students/{student_id}/physical-assessments",
        headers=auth_headers,
        data={
            "extract_only": "true",
            "assessment_file": (BytesIO(pdf_path.read_bytes()), pdf_path.name),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()["data"]["assessment"]
    assert payload["weightKg"] == 65.6
    assert payload["heightCm"] == 184.0
    assert payload["bodyFatPercentage"] == 14.3
    assert payload["measurements"]["waistCm"] == 74.4
