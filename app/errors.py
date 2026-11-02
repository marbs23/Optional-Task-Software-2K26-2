class DomainError(Exception):
    status_code = 500

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    status_code = 409
