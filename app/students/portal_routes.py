from __future__ import annotations

from flask import Blueprint, current_app, request

from app.common.api import success_response
from app.common.request import parse_json
from app.common.security.rate_limit import check_rate_limit, client_ip
from app.students.portal_schemas import StudentOtpRequestInput, StudentOtpVerifyInput, StudentWorkoutSessionInput
from app.students.portal_services import (
    build_student_portal_payload,
    create_student_portal_session,
    request_student_otp,
    require_student_session,
    verify_student_otp,
)


student_portal_bp = Blueprint("student_portal", __name__)


@student_portal_bp.post("/student-auth/request-otp")
def request_otp():
    payload = parse_json(StudentOtpRequestInput)
    check_rate_limit(
        key=f"student-otp:{client_ip()}:{payload.email.lower()}",
        limit=int(current_app.config.get("OTP_RATE_LIMIT_PER_HOUR", 5)),
        window_seconds=3600,
    )
    return success_response(request_student_otp(email=payload.email, requested_by_ip=request.remote_addr), 202)


@student_portal_bp.post("/student-auth/verify-otp")
def verify_otp():
    payload = parse_json(StudentOtpVerifyInput)
    return success_response(verify_student_otp(email=payload.email, code=payload.code))


@student_portal_bp.get("/student-portal/me")
def student_portal_me():
    student = require_student_session()
    return success_response(build_student_portal_payload(student))


@student_portal_bp.post("/student-portal/workout-sessions")
def create_student_workout_session():
    student = require_student_session()
    payload = parse_json(StudentWorkoutSessionInput)
    return success_response(create_student_portal_session(student=student, payload=payload), 201)
