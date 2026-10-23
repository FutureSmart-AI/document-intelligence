from fastapi import HTTPException



class MissingDataException(HTTPException):
    def __init__(self, detail: str = "No valid data provided. Please provide either a file, text, or weblink."):
        super().__init__(status_code=400, detail=detail)