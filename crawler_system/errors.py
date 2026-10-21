class CrawlerError(Exception):
    def __init__(self, code, message, action="Check network or target website and try again"):
        super().__init__(message)
        self.code = code
        self.action = action

    def as_dict(self):
        return {"code": self.code, "message": str(self), "action": self.action}

