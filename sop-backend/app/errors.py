class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, **details):
        self.status = status
        self.error = {"code": code, "message": message, **details}
        super().__init__(message)
