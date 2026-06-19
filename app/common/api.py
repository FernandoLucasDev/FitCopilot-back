from __future__ import annotations

from http import HTTPStatus

from flask import jsonify
from pydantic import ValidationError


class ApiError(Exception):
    def __init__(self, message: str, status_code: int = HTTPStatus.BAD_REQUEST, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def success_response(data=None, status_code: int = HTTPStatus.OK):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), status_code


def error_response(message: str, status_code: int, details: dict | None = None):
    payload = {"ok": False, "error": {"message": message, "details": details or {}}}
    return jsonify(payload), status_code


def register_error_handlers(app) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return error_response(error.message, error.status_code, error.details)

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return error_response(
            "Revise os campos enviados e tente novamente.",
            HTTPStatus.UNPROCESSABLE_ENTITY,
            {"errors": error.errors()},
        )

    @app.errorhandler(404)
    def handle_not_found(_error):
        return error_response("Recurso não encontrado", HTTPStatus.NOT_FOUND)

    @app.errorhandler(500)
    def handle_internal_error(_error):
        return error_response("Tivemos um erro interno. Tente novamente em instantes.", HTTPStatus.INTERNAL_SERVER_ERROR)

