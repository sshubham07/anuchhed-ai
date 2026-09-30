"""Domain errors (standards §4). Each has a stable `code`; the API maps them to HTTP."""


class SamvidhanError(Exception):
    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    default_message: str = "Something went wrong"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class NotFoundError(SamvidhanError):
    code = "NOT_FOUND"
    http_status = 404
    default_message = "Not found"


class ValidationFailedError(SamvidhanError):
    code = "VALIDATION_ERROR"
    http_status = 422
    default_message = "Invalid request"


class ServiceUnavailableError(SamvidhanError):
    code = "SERVICE_UNAVAILABLE"
    http_status = 503
    default_message = "Service unavailable"


class InvalidSourceError(SamvidhanError):
    """The ingestion input is unusable (not a text PDF, wrong document layout). CLI exit code 2."""

    code = "INVALID_SOURCE"
    http_status = 422
    default_message = "The source document cannot be ingested"
