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


class LLMUnavailableError(SamvidhanError):
    """Every configured model failed (after retries and fallback)."""

    code = "LLM_UNAVAILABLE"
    http_status = 503
    default_message = "The language model is unavailable right now. Please try again shortly."


class BusyError(SamvidhanError):
    """Too many answers streaming on this instance (`MAX_CONCURRENT_STREAMS`, HLD §13.2)."""

    code = "BUSY"
    http_status = 503
    default_message = "The service is busy right now. Please try again shortly."


class LLMBusyError(BusyError):
    """Every configured model is near its daily budget cap (HLD §8.6)."""

    default_message = "The service is busy right now. Please try again later."


class EmptyMessageError(SamvidhanError):
    code = "EMPTY_MESSAGE"
    http_status = 422
    default_message = "The message is empty."


class MessageTooLongError(SamvidhanError):
    code = "MESSAGE_TOO_LONG"
    http_status = 422
    default_message = "The message is too long."


class SessionFullError(SamvidhanError):
    """The session reached `MAX_MESSAGES_PER_SESSION` (HLD §13.2)."""

    code = "SESSION_FULL"
    http_status = 409
    default_message = "This chat is full. Please start a new chat."


class RateLimitedError(SamvidhanError):
    code = "RATE_LIMITED"
    http_status = 429
    default_message = "Too many requests. Please slow down."

    def __init__(self, retry_after_s: int, message: str | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
