import os
from datetime import datetime, timedelta
from typing import Optional

# Librería para hash y verificación de contraseñas de forma segura
import bcrypt
# Librería para generación y decodificación de tokens de sesión JWT
from jose import JWTError, jwt
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.orm import Session

from .database import get_db
from . import models

# CLAVE SECRETA Y CONFIGURACIÓN DE TOKEN
# SECRET_KEY se lee desde las variables de entorno de Render o .env.
# Si no existe, usa una por defecto (cámbiala en producción por seguridad).
SECRET_KEY = os.getenv("SECRET_KEY", "gympro_super_secret_jwt_key_2026_production")
ALGORITHM = "HS256"
# Tiempo de expiración de la sesión iniciada: 12 horas
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12


def hash_password(password: str) -> str:
    """
    Toma una contraseña en texto plano, genera una sal aleatoria (salt)
    y devuelve la contraseña encriptada con bcrypt para guardarla en la base de datos.
    """
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Compara la contraseña ingresada en el formulario de login contra el hash
    almacenado en la base de datos. Devuelve True si coinciden.
    """
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Genera un token firmado JWT que contiene los datos del usuario (username, rol)
    y una fecha de expiración automática.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Agrega la fecha de vencimiento a la carga útil (payload) del token
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[models.UsuarioSistema]:
    """
    Middleware de autenticación para FastAPI:
    1. Lee la cookie 'access_token' del navegador.
    2. Decodifica y verifica la firma del JWT.
    3. Busca al usuario en la tabla 'usuarios_sistema'.
    4. Si es válido y está activo, retorna el objeto del usuario logueado.
    5. Si el token expiró o es inválido, retorna None (redirigiendo a login).
    """
    token = request.cookies.get("access_token")
    if not token:
        return None

    try:
        # Decodifica el token verificando la firma con SECRET_KEY
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
    except JWTError:
        # Si el token fue manipulado o ya caducó, se invalida
        return None

    # Consulta a la base de datos para asegurar que el usuario aún exista y esté activo
    user = db.query(models.UsuarioSistema).filter(
        models.UsuarioSistema.username == username,
        models.UsuarioSistema.activo == True
    ).first()

    return user