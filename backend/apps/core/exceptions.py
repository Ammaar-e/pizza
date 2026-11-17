import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class DomainError(APIException):
    """Business-rule violation with a machine readable code."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "domain_error"
    default_detail = "Request could not be processed."

    def __init__(self, detail=None, code=None, errors=None, status_code=None):
        super().__init__(detail=detail, code=code)
        self.errors = errors or []
        if status_code:
            self.status_code = status_code
        self.code = code or self.default_code


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class ManagerAuthorizationRequired(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "manager_authorization_required"
    default_detail = "This operation requires manager authorization."


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    request = context.get("request")
    body = {
        "error": {
            "code": getattr(exc, "code", None) or getattr(exc, "default_code", "error"),
            "message": response.data.get("detail") if isinstance(response.data, dict) and "detail" in response.data else "Validation failed" if response.status_code == 400 else str(exc),
            "details": None if isinstance(response.data, dict) and set(response.data.keys()) == {"detail"} else response.data,
            "errors": getattr(exc, "errors", None) or None,
            "request_id": getattr(request, "request_id", None) if request is not None else None,
        }
    }
    response.data = body
    return response
