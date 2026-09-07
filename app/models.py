from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Numeric, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class UsuarioSistema(Base):
    __tablename__ = "usuarios_sistema"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    nombre = Column(String(100), nullable=False)
    rol = Column(String(20), default="OPERADOR")  # 'ADMIN' o 'OPERADOR'
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Profesor(Base):
    __tablename__ = "profesores"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    especialidad = Column(String(100))
    telefono = Column(String(30))
    activo = Column(Boolean, default=True)

    actividades = relationship("Actividad", back_populates="profesor")


class Actividad(Base):
    __tablename__ = "actividades"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    descripcion = Column(Text)
    profesor_id = Column(Integer, ForeignKey("profesores.id"))
    dias_horarios = Column(String(150))
    cupo_maximo = Column(Integer, default=30)
    precio_mensual = Column(Numeric(10, 2), nullable=False)
    activa = Column(Boolean, default=True)

    profesor = relationship("Profesor", back_populates="actividades")


class Socio(Base):
    __tablename__ = "socios"

    id = Column(Integer, primary_key=True, index=True)
    dni = Column(String(20), unique=True, nullable=False, index=True)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    fecha_nacimiento = Column(Date, nullable=False)
    edad = Column(Integer)
    celular = Column(String(30))
    email = Column(String(150), unique=True, nullable=False)
    apto_medico_url = Column(String(255), nullable=True)
    apto_medico_vencimiento = Column(Date, nullable=True)
    qr_token = Column(String(100), unique=True, nullable=False, index=True)
    estado_cuota = Column(String(20), default="PENDIENTE")  # 'ACTIVO', 'VENCIDO', 'PENDIENTE'
    fecha_vencimiento_cuota = Column(Date, nullable=True)
    bloqueado_manual = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    accesos = relationship("RegistroAcceso", back_populates="socio")


class RegistroAcceso(Base):
    __tablename__ = "registros_acceso"

    id = Column(Integer, primary_key=True, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"))
    fecha_hora = Column(DateTime, default=datetime.utcnow)
    resultado = Column(String(20), nullable=False)  # 'PERMITIDO', 'DENEGADO'
    motivo = Column(String(100), nullable=False)

    socio = relationship("Socio", back_populates="accesos")