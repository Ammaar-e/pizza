import logging

from django.db import IntegrityError
from django.db.models import ProtectedError, RestrictedError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler, set_rollback

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


def _unhandled(exc, context):
    """API clients always get JSON, never Django's HTML error page (which browsers then fail to parse as
    "Unexpected token '<'"). The full traceback goes to the server log with the request id."""
    request = context.get("request")
    request_id = getattr(request, "request_id", None) if request is not None else None
    set_rollback()
    if isinstance(exc, (ProtectedError, RestrictedError)):
        used_by = sorted({str(o._meta.verbose_name_plural) for o in list(exc.protected_objects if isinstance(exc, ProtectedError)
                                                                           else exc.restricted_objects)[:50]})
        return Response({"error": {"code": "in_use", "message": "This is still used by other records "
                                   f"({', '.join(used_by)}). Change or remove those first, or mark it inactive.",
                                   "details": None, "errors": None, "request_id": request_id}}, status=409)
    if isinstance(exc, IntegrityError):
        logger.warning("Integrity error on %s (request %s): %s", getattr(request, "path", "?"), request_id, exc)
        return Response({"error": {"code": "conflict", "message": "This conflicts with existing data "
                                   "(for example a duplicate code or number) or a required link is missing.",
                                   "details": None, "errors": None, "request_id": request_id}}, status=409)
    logger.error("Unhandled error on %s (request %s)", getattr(request, "path", "?"), request_id, exc_info=exc)
    return Response({"error": {"code": "server_error", "message": "Internal server error. The details are in "
                               f"the server log (request id {request_id}).", "details": None, "errors": None,
                               "request_id": request_id}}, status=500)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return _unhandled(exc, context)
    request = context.get("request")
    body = {
        "error": {
            "code": getattr(exc, "code", None) or getattr(getattr(exc, "detail", None), "code", None)
            or getattr(exc, "default_code", "error"),
            "message": response.data.get("detail") if isinstance(response.data, dict) and "detail" in response.data else "Validation failed" if response.status_code == 400 else str(exc),
            "details": None if isinstance(response.data, dict) and set(response.data.keys()) == {"detail"} else response.data,
            "errors": getattr(exc, "errors", None) or None,
            "request_id": getattr(request, "request_id", None) if request is not None else None,
        }
    }
    response.data = body
    return response
