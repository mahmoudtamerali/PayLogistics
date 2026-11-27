"""
Custom exception hierarchy for SecureCare backend.

These exceptions carry HTTP status semantics and are translated into
standardised API responses by the handler framework.
"""


class SecureCareError(Exception):
    """Base exception for all application errors."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str = "An internal error occurred"):
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Client errors (4xx)
# ---------------------------------------------------------------------------

class BadRequestError(SecureCareError):
    status_code = 400
    error_code = "BAD_REQUEST"

    def __init__(self, message: str = "Invalid request"):
        super().__init__(message)


class ValidationError(SecureCareError):
    status_code = 400
    error_code = "VALIDATION_ERROR"

    def __init__(self, message: str = "Validation failed"):
        super().__init__(message)


class AuthenticationError(SecureCareError):
    status_code = 401
    error_code = "UNAUTHENTICATED"

    def __init__(self, message: str = "Authentication required"):
        super().__init__(message)


class ForbiddenError(SecureCareError):
    status_code = 403
    error_code = "FORBIDDEN"

    def __init__(self, message: str = "You are not authorized to perform this operation"):
        super().__init__(message)


class NotFoundError(SecureCareError):
    status_code = 404
    error_code = "NOT_FOUND"

    def __init__(self, message: str = "Resource not found"):
        super().__init__(message)


class ConflictError(SecureCareError):
    status_code = 409
    error_code = "CONFLICT"

    def __init__(self, message: str = "Resource conflict"):
        super().__init__(message)


class PayloadTooLargeError(SecureCareError):
    status_code = 413
    error_code = "PAYLOAD_TOO_LARGE"

    def __init__(self, message: str = "Request payload too large"):
        super().__init__(message)


class UnprocessableEntityError(SecureCareError):
    status_code = 422
    error_code = "UNPROCESSABLE_ENTITY"

    def __init__(self, message: str = "Unprocessable entity"):
        super().__init__(message)


class TooManyRequestsError(SecureCareError):
    status_code = 429
    error_code = "TOO_MANY_REQUESTS"

    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message)


# ---------------------------------------------------------------------------
# Server errors (5xx)
# ---------------------------------------------------------------------------

class InternalError(SecureCareError):
    status_code = 500
    error_code = "INTERNAL_ERROR"

    def __init__(self, message: str = "An internal error occurred"):
        super().__init__(message)


class ServiceUnavailableError(SecureCareError):
    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"

    def __init__(self, message: str = "Service temporarily unavailable"):
        super().__init__(message)


# ---------------------------------------------------------------------------
# Domain-specific errors
# ---------------------------------------------------------------------------

class PatientNotFoundError(NotFoundError):
    error_code = "PATIENT_NOT_FOUND"

    def __init__(self, message: str = "Patient not found"):
        super().__init__(message)


class AppointmentNotFoundError(NotFoundError):
    error_code = "APPOINTMENT_NOT_FOUND"

    def __init__(self, message: str = "Appointment not found"):
        super().__init__(message)


class AssignmentNotFoundError(NotFoundError):
    error_code = "ASSIGNMENT_NOT_FOUND"

    def __init__(self, message: str = "Assignment not found"):
        super().__init__(message)


class FileNotFoundError_(NotFoundError):
    error_code = "FILE_NOT_FOUND"

    def __init__(self, message: str = "File not found"):
        super().__init__(message)


class NoteNotFoundError(NotFoundError):
    error_code = "NOTE_NOT_FOUND"

    def __init__(self, message: str = "Visit note not found"):
        super().__init__(message)


class InvalidStateTransitionError(ConflictError):
    error_code = "INVALID_STATE_TRANSITION"

    def __init__(self, message: str = "Invalid state transition"):
        super().__init__(message)


class DoubleBookingError(ConflictError):
    error_code = "DOUBLE_BOOKING"

    def __init__(self, message: str = "Time slot conflict"):
        super().__init__(message)


class FileScanPendingError(ConflictError):
    error_code = "FILE_SCAN_PENDING"

    def __init__(self, message: str = "File is pending malware scan"):
        super().__init__(message)


class FileQuarantinedError(ConflictError):
    error_code = "FILE_QUARANTINED"

    def __init__(self, message: str = "File has been quarantined"):
        super().__init__(message)


class AssignmentRequiredError(ForbiddenError):
    error_code = "ASSIGNMENT_REQUIRED"

    def __init__(self, message: str = "Doctor-patient assignment required"):
        super().__init__(message)
