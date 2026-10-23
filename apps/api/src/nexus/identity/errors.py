"""Fixed identity failures. Messages stay free of passwords and emails."""


class IdentityError(Exception):
    """An operator or credential failure with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
