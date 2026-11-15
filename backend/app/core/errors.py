"""Domain errors translated to HTTP responses by the API layer (Milestone 8)."""


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"


class RuleViolationError(DomainError):
    status_code = 422
    code = "RULE_VIOLATION"
