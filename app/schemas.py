from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date, datetime

# ==========================================================
# 1. ESQUEMAS: USUARIOS DEL SISTEMA (ADMIN / OPERADORES)
# ==========================================================
class UsuarioBase(BaseModel):
    # Nombre de usuario único para ingresar
    username: str
    nombre: str
    rol: Optional[str] = "OPERADOR"

class UsuarioCreate(UsuarioBase):
    # Contraseña en texto plano requerida solo al momento del alta
    password: str

class UsuarioResponse(UsuarioBase):
    id: int
    activo: bool

    class Config:
        # Permite a Pydantic leer directamente modelos de SQLAlchemy
        from_attributes = True


# ==========================================================
# 2. ESQUEMAS: PLANES Y MEMBRESÍAS
# ==========================================================
class PlanBase(BaseModel):
    nombre: str
    precio: float
    dias_duracion: int = 30
    descripcion: Optional[str] = None
    activo: bool = True

class PlanCreate(PlanBase):
    pass

class PlanResponse(PlanBase):
    id: int

    class Config:
        from_attributes = True


# ==========================================================
# 3. ESQUEMAS: PROFESORES Y DOCENTES
# ==========================================================
class ProfesorBase(BaseModel):
    nombre: str
    apellido: str
    dni: str
    celular: str
    especialidad: Optional[str] = None
    sueldo: float = 0.0
    tipo_sueldo: str = "MENSUAL"
    activo: bool = True

class ProfesorCreate(ProfesorBase):
    pass

class ProfesorResponse(ProfesorBase):
    id: int

    class Config:
        from_attributes = True


# ==========================================================
# 4. ESQUEMAS: ACTIVIDADES / CLASES COLECTIVAS
# ==========================================================
class ActividadBase(BaseModel):
    nombre: str
    dias: str
    horario: str
    cupo_maximo: int = 20
    profesor_id: Optional[int] = None

class ActividadCreate(ActividadBase):
    pass

class ActividadResponse(ActividadBase):
    id: int
    profesor: Optional[ProfesorResponse] = None

    class Config:
        from_attributes = True


# ==========================================================
# 5. ESQUEMAS: SOCIOS DEL GIMNASIO
# ==========================================================
class SocioBase(BaseModel):
    dni: str
    nombre: str
    apellido: str
    fecha_nacimiento: date
    celular: str
    email: EmailStr
    plan_id: Optional[int] = None

class SocioCreate(SocioBase):
    apto_medico_realizacion: Optional[date] = None

class SocioResponse(SocioBase):
    id: int
    edad: int
    foto_base64: Optional[str] = None
    apto_medico_base64: Optional[str] = None
    apto_medico_realizacion: Optional[date] = None
    apto_medico_vencimiento: Optional[date] = None
    qr_token: str
    estado_cuota: str
    fecha_vencimiento_cuota: Optional[date] = None
    bloqueado_manual: bool
    habilitacion_manual_hasta: Optional[date] = None
    creado_en: datetime

    class Config:
        from_attributes = True


# ==========================================================
# 6. ESQUEMAS: EVOLUCIÓN FÍSICA Y MEDIDAS CORPORALES
# ==========================================================
class EvolucionBase(BaseModel):
    fecha: date
    peso_kg: float
    altura_cm: Optional[float] = None
    porcentaje_grasa: Optional[float] = None
    cintura_cm: Optional[float] = None
    pecho_cm: Optional[float] = None
    brazo_cm: Optional[float] = None
    cadera_cm: Optional[float] = None
    notas: Optional[str] = None

class EvolucionCreate(EvolucionBase):
    pass

class EvolucionResponse(EvolucionBase):
    id: int
    socio_id: int
    imc: Optional[float] = None

    class Config:
        from_attributes = True


# ==========================================================
# 7. ESQUEMAS: RUTINAS Y EJERCICIOS
# ==========================================================
class EjercicioRutinaBase(BaseModel):
    dia_grupo: str
    ejercicio: str
    series: int = 4
    repeticiones: str = "10-12"
    peso_sugerido: Optional[str] = ""
    descanso: Optional[str] = "60s"
    completado: bool = False

class EjercicioRutinaCreate(EjercicioRutinaBase):
    pass

class EjercicioRutinaResponse(EjercicioRutinaBase):
    id: int
    rutina_id: int

    class Config:
        from_attributes = True

class RutinaBase(BaseModel):
    nombre_rutina: str
    objetivo: Optional[str] = None
    activa: bool = True

class RutinaCreate(RutinaBase):
    socio_id: int
    profesor_id: Optional[int] = None

class RutinaResponse(RutinaBase):
    id: int
    socio_id: int
    profesor_id: Optional[int] = None
    creado_en: datetime
    ejercicios: List[EjercicioRutinaResponse] = []

    class Config:
        from_attributes = True