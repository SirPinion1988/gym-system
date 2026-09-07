from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class UsuarioSistema(Base):
    __tablename__ = "usuarios_sistema"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nombre = Column(String(100), nullable=False)
    rol = Column(String(20), default="OPERADOR")
    activo = Column(Boolean, default=True)

class Plan(Base):
    __tablename__ = "planes"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    precio = Column(Float, nullable=False)
    dias_duracion = Column(Integer, default=30)
    descripcion = Column(String(200), nullable=True)
    activo = Column(Boolean, default=True)

class Profesor(Base):
    __tablename__ = "profesores"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    dni = Column(String(20), unique=True, nullable=False)
    celular = Column(String(30), nullable=False)
    especialidad = Column(String(100), nullable=True)
    activo = Column(Boolean, default=True)
    clases = relationship("Actividad", back_populates="profesor")

class Actividad(Base):
    __tablename__ = "actividades"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    dias = Column(String(100), nullable=False)
    horario = Column(String(50), nullable=False)
    cupo_maximo = Column(Integer, default=20)
    profesor_id = Column(Integer, ForeignKey("profesores.id"), nullable=True)
    profesor = relationship("Profesor", back_populates="clases")

class Socio(Base):
    __tablename__ = "socios"
    id = Column(Integer, primary_key=True, index=True)
    dni = Column(String(20), unique=True, index=True, nullable=False)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    fecha_nacimiento = Column(Date, nullable=False)
    edad = Column(Integer, nullable=False)
    celular = Column(String(30), nullable=False)
    email = Column(String(120), unique=True, index=True, nullable=False)
    apto_medico_vencimiento = Column(Date, nullable=True)
    qr_token = Column(String(100), unique=True, index=True, nullable=False)
    estado_cuota = Column(String(20), default="ACTIVO")
    fecha_vencimiento_cuota = Column(Date, nullable=True)
    plan_id = Column(Integer, ForeignKey("planes.id"), nullable=True)
    bloqueado_manual = Column(Boolean, default=False)
    
    # Datos de Tarjeta Vinculada (Débito Automático)
    tarjeta_tokenizada = Column(Boolean, default=False)
    tarjeta_marca = Column(String(30), nullable=True) # VISA, MASTERCARD
    tarjeta_ultimos4 = Column(String(4), nullable=True)
    
    creado_en = Column(DateTime, default=datetime.utcnow)

    plan = relationship("Plan")
    pagos = relationship("Pago", back_populates="socio")
    accesos = relationship("RegistroAcceso", back_populates="socio")

class Pago(Base):
    __tablename__ = "pagos"
    id = Column(Integer, primary_key=True, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    monto = Column(Float, nullable=False)
    metodo_pago = Column(String(50), default="EFECTIVO") # EFECTIVO, MERCADOPAGO, TARJETA_VINCULADA, TRANSFERENCIA
    concepto = Column(String(150), default="Pago de Cuota")
    fecha_pago = Column(DateTime, default=datetime.utcnow)
    socio = relationship("Socio", back_populates="pagos")

class Producto(Base):
    __tablename__ = "productos"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    categoria = Column(String(50), default="BEBIDAS")
    precio_venta = Column(Float, nullable=False)
    stock = Column(Integer, default=0)
    activo = Column(Boolean, default=True)

class VentaProducto(Base):
    __tablename__ = "ventas_productos"
    id = Column(Integer, primary_key=True, index=True)
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=False)
    cantidad = Column(Integer, default=1)
    total = Column(Float, nullable=False)
    metodo_pago = Column(String(50), default="EFECTIVO")
    fecha = Column(DateTime, default=datetime.utcnow)
    producto = relationship("Producto")

class RegistroAcceso(Base):
    __tablename__ = "registros_acceso"
    id = Column(Integer, primary_key=True, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    fecha_hora = Column(DateTime, default=datetime.utcnow)
    resultado = Column(String(20), nullable=False)
    motivo = Column(String(100), nullable=False)
    socio = relationship("Socio", back_populates="accesos")