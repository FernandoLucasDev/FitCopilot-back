from __future__ import annotations

from flask import Blueprint, request

from app.common.api import success_response
from app.common.request import parse_json
from app.students.portal_schemas import StudentOtpRequestInput, StudentOtpVerifyInput
from app.students.portal_services import build_student_portal_payload, request_student_otp, require_student_session, verify_student_otp


student_portal_bp = Blueprint("student_portal", __name__)


@student_portal_bp.post("/student-auth/request-otp")
def request_otp():
    payload = parse_json(StudentOtpRequestInput)
    return success_response(request_student_otp(email=payload.email, requested_by_ip=request.remote_addr), 202)


@student_portal_bp.post("/student-auth/verify-otp")
def verify_otp():
    payload = parse_json(StudentOtpVerifyInput)
    return success_response(verify_student_otp(email=payload.email, code=payload.code))


@student_portal_bp.get("/student-portal/me")
def student_portal_me():
    student = require_student_session()
    return success_response(build_student_portal_payload(student))
