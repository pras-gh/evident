"""One error shape for refusals the app acts on: `{"error": …, "message": …}`
plus whatever the page needs to act (the usage behind a paywall, the plans).
"""
from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, **extra) -> None:
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra

    def body(self) -> dict:
        return {"error": self.error, "message": self.message, **self.extra}
