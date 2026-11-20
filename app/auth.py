"""Аутентификация: pbkdf2-hmac-sha256, случайные токены, роли и зависимости FastAPI."""
import hashlib, hmac, secrets, re
from fastapi import Header, HTTPException, Depends
from . import db

ROLES = {"researcher": 0, "annotator": 1, "admin": 2}
ITERATIONS = 120_000
ANON = {"id": None, "name": "anonymous", "role": "researcher", "anon": True}


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()
    return h, salt


def verify_password(password, pw_hash, salt):
    if not pw_hash or not salt:
        return False
    h, _ = hash_password(password, salt)
    return hmac.compare_digest(h, pw_hash)


def new_token():
    return secrets.token_urlsafe(32)


def get_setting(key, default=None):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r and r["value"] is not None else default


def require_login():
    return str(get_setting("require_login", "false")).lower() in ("1", "true", "yes")


def create_user(name, password, role):
    if role not in ROLES:
        raise ValueError("Роль должна быть admin, annotator или researcher")
    name = (name or "").strip()
    if not re.fullmatch(r"[\w.@-]{2,40}", name):
        raise ValueError("Имя: 2-40 символов (буквы, цифры, . _ - @)")
    if len(password or "") < 6:
        raise ValueError("Пароль должен быть не короче 6 символов")
    if db.q1("SELECT 1 FROM users WHERE name=?", (name,)):
        raise ValueError("Пользователь с таким именем уже есть")
    h, salt = hash_password(password)
    db.ex("INSERT INTO users(name,role,pw_hash,salt,token,created) VALUES(?,?,?,?,?,?)", (name, role, h, salt, new_token(), db.now()))
    return name


def login(name, password):
    u = db.q1("SELECT * FROM users WHERE name=?", ((name or "").strip(),))
    if not u or not verify_password(password or "", u["pw_hash"], u["salt"]):
        return None
    if not u["token"]:
        u["token"] = new_token()
        db.ex("UPDATE users SET token=? WHERE id=?", (u["token"], u["id"]))
    return u


def current_user(authorization: str = Header(None)):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token:
        u = db.q1("SELECT id,name,role FROM users WHERE token=?", (token,))
        if not u:
            raise HTTPException(401, "Недействительный токен: войдите заново")
        return {**u, "anon": False}
    if require_login():
        raise HTTPException(401, "Требуется вход")
    return dict(ANON)


def require_role(role):
    need = ROLES[role]

    def dep(user=Depends(current_user)):
        if ROLES[user["role"]] < need:
            if user.get("anon"):
                raise HTTPException(401, "Для этого действия нужно войти")
            names = {"annotator": "разметчика", "admin": "администратора"}
            raise HTTPException(403, "Недостаточно прав: нужна роль %s" % names.get(role, role))
        return user
    return dep


annotator = require_role("annotator")
admin = require_role("admin")


def ensure_admin():
    """Создаёт admin со случайным паролем (data/admin_password.txt, 600), если пользователей нет."""
    if db.q1("SELECT 1 FROM users LIMIT 1"):
        return None
    pw = secrets.token_urlsafe(9)
    create_user("admin", pw, "admin")
    p = db.DB_PATH.parent / "admin_password.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("admin\n%s\n" % pw, encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return pw
