from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, date
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
    sueldo = Column(Float, default=0.0)
    tipo_sueldo = Column(String(30), default="MENSUAL")
    activo = Column(Boolean, default=True)
    clases = relationship("Actividad", back_populates="profesor")
    rutinas_creadas = relationship("Rutina", back_populates="profesor")
    pagos_recibidos = relationship("PagoProfesor", back_populates="profesor", cascade="all, delete-orphan")

class PagoProfesor(Base):
    __tablename__ = "pagos_profesores"
    id = Column(Integer, primary_key=True, index=True)
    profesor_id = Column(Integer, ForeignKey("profesores.id"), nullable=False)
    monto = Column(Float, nullable=False)
    concepto = Column(String(150), default="Liquidación de Sueldo / Honorarios")
    metodo_pago = Column(String(50), default="EFECTIVO")
    fecha_pago = Column(DateTime, default=datetime.utcnow)
    profesor = relationship("Profesor", back_populates="pagos_recibidos")

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
    foto_base64 = Column(Text, nullable=True)
    apto_medico_realizacion = Column(Date, nullable=True)
    apto_medico_vencimiento = Column(Date, nullable=True)
    qr_token = Column(String(100), unique=True, index=True, nullable=False)
    estado_cuota = Column(String(20), default="ACTIVO")
    fecha_vencimiento_cuota = Column(Date, nullable=True)
    plan_id = Column(Integer, ForeignKey("planes.id"), nullable=True)
    bloqueado_manual = Column(Boolean, default=False)
    habilitacion_manual_hasta = Column(Date, nullable=True)
    tarjeta_tokenizada = Column(Boolean, default=False)
    tarjeta_marca = Column(String(30), nullable=True)
    tarjeta_ultimos4 = Column(String(4), nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow)

    plan = relationship("Plan")
    pagos = relationship("Pago", back_populates="socio", cascade="all, delete-orphan")
    facturas = relationship("Factura", back_populates="socio", cascade="all, delete-orphan")
    accesos = relationship("RegistroAcceso", back_populates="socio", cascade="all, delete-orphan")
    rutinas = relationship("Rutina", back_populates="socio", cascade="all, delete-orphan")
    evoluciones = relationship("EvolucionSocio", back_populates="socio", cascade="all, delete-orphan", order_by="desc(EvolucionSocio.fecha)")

class EvolucionSocio(Base):
    __tablename__ = "evolucion_socio"
    id = Column(Integer, primary_key=True, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    fecha = Column(Date, default=date.today)
    peso_kg = Column(Float, nullable=True)
    altura_cm = Column(Float, nullable=True)
    porcentaje_grasa = Column(Float, nullable=True)
    cintura_cm = Column(Float, nullable=True)
    pecho_cm = Column(Float, nullable=True)
    brazo_cm = Column(Float, nullable=True)
    cadera_cm = Column(Float, nullable=True)
    notas = Column(Text, nullable=True)

    socio = relationship("Socio", back_populates="evoluciones")

    @property
    def imc(self):
        if self.peso_kg and self.altura_cm and self.altura_cm > 0:
            altura_m = self.altura_cm / 100.0
            return round(self.peso_kg / (altura_m ** 2), 1)
        return None

class Rutina(Base):
    __tablename__ = "rutinas"
    id = Column(Integer, primary_key=True, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    profesor_id = Column(Integer, ForeignKey("profesores.id"), nullable=True)
    nombre_rutina = Column(String(120), nullable=False)
    objetivo = Column(String(150), nullable=True)
    activa = Column(Boolean, default=True)
    creado_en = Column(DateTime, default=datetime.utcnow)

    socio = relationship("Socio", back_populates="rutinas")
    profesor = relationship("Profesor", back_populates="rutinas_creadas")
    ejercicios = relationship("EjercicioRutina", back_populates="rutina", cascade="all, delete-orphan")

class EjercicioRutina(Base):
    __tablename__ = "ejercicios_rutina"
    id = Column(Integer, primary_key=True, index=True)
    rutina_id = Column(Integer, ForeignKey("rutinas.id"), nullable=False)
    dia_grupo = Column(String(50), nullable=False)
    ejercicio = Column(String(120), nullable=False)
    series = Column(Integer, default=4)
    repeticiones = Column(String(30), default="10-12")
    peso_sugerido = Column(String(30), default="")
    descanso = Column(String(30), default="60s")
    completado = Column(Boolean, default=False)

    rutina = relationship("Rutina", back_populates="ejercicios")

class Pago(Base):
    __tablename__ = "pagos"
    id = Column(Integer, primary_key=True, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    monto = Column(Float, nullable=False)
    metodo_pago = Column(String(50), default="EFECTIVO")
    concepto = Column(String(150), default="Pago de Cuota")
    external_payment_id = Column(String(100), unique=True, nullable=True)
    fecha_pago = Column(DateTime, default=datetime.utcnow)
    socio = relationship("Socio", back_populates="pagos")
    factura = relationship("Factura", back_populates="pago", uselist=False, cascade="all, delete-orphan")

class Factura(Base):
    __tablename__ = "facturas"
    id = Column(Integer, primary_key=True, index=True)
    pago_id = Column(Integer, ForeignKey("pagos.id"), nullable=False)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    tipo_comprobante = Column(String(10), default="C")
    punto_venta = Column(Integer, default=1)
    numero_comprobante = Column(Integer, nullable=False)
    cae = Column(String(50), nullable=False)
    cae_vencimiento = Column(Date, nullable=False)
    monto_total = Column(Float, nullable=False)
    fecha_emision = Column(DateTime, default=datetime.utcnow)
    enviada_por_mail = Column(Boolean, default=False)

    socio = relationship("Socio", back_populates="facturas")
    pago = relationship("Pago", back_populates="factura")

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