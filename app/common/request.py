from __future__ import annotations

from flask import request


def parse_json(model_cls):
    payload = request.get_json(silent=False) or {}
    return model_cls.model_validate(payload)
