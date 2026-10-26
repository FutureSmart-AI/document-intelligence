"""Single operator login for an independent installation; no hosted identity call."""
import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/auth")


def local_profile(request: Request):
    owner = os.environ["STANDALONE_OWNER_EMAIL"].strip().lower()
    if request.session.get("di_owner") != owner:
        return None
    return {"email": owner, "user_id": owner, "name": "Workspace owner"}


class Login(BaseModel):
    password: str


@router.post("/login")
async def login(body: Login, request: Request):
    expected = os.environ["STANDALONE_PASSWORD"]
    if not expected or not hmac.compare_digest(body.password.encode(), expected.encode()):
        raise HTTPException(401, "Incorrect workspace password")
    request.session.clear()
    request.session["di_owner"] = os.environ["STANDALONE_OWNER_EMAIL"].strip().lower()
    return local_profile(request)


@router.get("/profile")
async def profile(request: Request):
    value = local_profile(request)
    if not value:
        raise HTTPException(401, "Not authenticated")
    return value


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": "signed_out"}
