from pydantic import BaseModel, EmailStr
from datetime import date, datetime
from typing import Optional

class SocioCreate(BaseModel):
    dni: str
    nombre: str
    apellido: str
    fecha_nacimiento: date
    celular: Optional[str] = None
    email: EmailStr
    apto_medico_vencimiento: Optional[date] = None

class SocioOut(BaseModel):
    id: int
    dni: str
    nombre: str
    apellido: str
    edad: int
    email: str
    estado_cuota: str
    qr_token: str
    class Config:
        from_attributes = True

class ActividadCreate(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    profesor_id: Optional[int] = None
    dias_horarios: str
    cupo_maximo: int
    precio_mensual: float

class ProfesorCreate(BaseModel):
    nombre: str
    apellido: str
    especialidad: str
    telefono: str