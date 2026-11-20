"""Вход и текущий пользователь."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from .. import auth, db

router = APIRouter(prefix="/api/auth")


class LoginIn(BaseModel):
    name: str
    password: str


@router.post("/login")
def login(body: LoginIn):
    u = auth.login(body.name, body.password)
    if not u:
        db.audit(body.name[:40], "auth.fail", "")
        raise HTTPException(401, "Неверное имя пользователя или пароль")
    db.audit(u["name"], "auth.login", "")
    return {"token": u["token"], "user": {"name": u["name"], "role": u["role"]}}


@router.get("/me")
def me(user=Depends(auth.current_user)):
    return {"name": user["name"], "role": user["role"], "anonymous": bool(user.get("anon")), "require_login": auth.require_login()}
