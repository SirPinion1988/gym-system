from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, date
from .database import Base

# ==========================================================
# 1. TABLA: USUARIOS DEL SISTEMA (ADMINISTRADORES Y RECEPCIÓN)
# ==========================================================
class UsuarioSistema(Base):
    __tablename__ = "usuarios_sistema"

    id = Column(Integer, primary_key=True, index=True)
    # Nombre de usuario único para login de administración (ej: "admin")
    username = Column(String(50), unique=True, index=True, nullable=False)
    # Contraseña cifrada con algoritmo bcrypt (nunca se guarda en texto plano)
    password_hash = Column(String(255), nullable=False)
    # Nombre visible del operador en la cabecera
    nombre = Column(String(100), nullable=False)
    # Rol de usuario: 'ADMIN' (acceso completo) u 'OPERADOR' (recepción diaria)
    rol = Column(String(20), default="OPERADOR")
    # Permite inhabilitar el acceso de un empleado sin borrar su historial
    activo = Column(Boolean, default=True)


# ==========================================================
# 2. TABLA: PLANES Y MEMBRESÍAS
# ==========================================================
class Plan(Base):
    __tablename__ = "planes"

    id = Column(Integer, primary_key=True, index=True)
    # Nombre comercial del abono (ej: "Pase Libre Musculación", "3 Días por Semana")
    nombre = Column(String(100), nullable=False)
    # Tarifa mensual o arancel que se cobra al socio
    precio = Column(Float, nullable=False)
    # Días de validez que suma a la fecha de vencimiento (habitualmente 30 días)
    dias_duracion = Column(Integer, default=30)
    # Descripción visible en recepción y credencial del socio
    descripcion = Column(String(200), nullable=True)
    # Si está activo aparece en el selector para nuevos cobros
    activo = Column(Boolean, default=True)


# ==========================================================
# 3. TABLA: PROFESORES, ENTRENADORES Y SUELDOS
# ==========================================================
class Profesor(Base):
    __tablename__ = "profesores"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    dni = Column(String(20), unique=True, nullable=False)
    celular = Column(String(30), nullable=False)
    # Área de desempeño (ej: "Musculación", "Spinning", "Crossfit")
    especialidad = Column(String(100), nullable=True)
    # Sueldo pactado para el cálculo de costos en el balance
    sueldo = Column(Float, default=0.0)
    # Modalidad de cobro del docente: 'MENSUAL' o 'POR_HORA'
    tipo_sueldo = Column(String(30), default="MENSUAL")
    activo = Column(Boolean, default=True)

    # Relaciones con clases a cargo, rutinas diseñadas y liquidaciones cobradas
    clases = relationship("Actividad", back_populates="profesor")
    rutinas_creadas = relationship("Rutina", back_populates="profesor")
    pagos_recibidos = relationship("PagoProfesor", back_populates="profesor", cascade="all, delete-orphan")


# ==========================================================
# 4. TABLA: LIQUIDACIÓN REAL DE SUELDOS A DOCENTES
# ==========================================================
class PagoProfesor(Base):
    __tablename__ = "pagos_profesores"

    id = Column(Integer, primary_key=True, index=True)
    # Clave foránea al profesor que recibe el dinero
    profesor_id = Column(Integer, ForeignKey("profesores.id"), nullable=False)
    # Importe abonado que se descuenta del balance de caja
    monto = Column(Float, nullable=False)
    # Motivo (ej: "Adelanto quincena", "Sueldo mes vencido", "Horas extra")
    concepto = Column(String(150), default="Liquidación de Sueldo / Honorarios")
    # Medio utilizado: 'EFECTIVO' o 'TRANSFERENCIA'
    metodo_pago = Column(String(50), default="EFECTIVO")
    fecha_pago = Column(DateTime, default=datetime.utcnow)

    profesor = relationship("Profesor", back_populates="pagos_recibidos")


# ==========================================================
# 5. TABLA: CLASES GRUPALES Y HORARIOS
# ==========================================================
class Actividad(Base):
    __tablename__ = "actividades"

    id = Column(Integer, primary_key=True, index=True)
    # Nombre de la clase (ej: "Yoga", "Funcional")
    nombre = Column(String(100), nullable=False)
    # Días semanales (ej: "Lunes, Miércoles y Viernes")
    dias = Column(String(100), nullable=False)
    # Rango horario (ej: "19:00 a 20:00")
    horario = Column(String(50), nullable=False)
    # Capacidad máxima permitida por turno
    cupo_maximo = Column(Integer, default=20)
    profesor_id = Column(Integer, ForeignKey("profesores.id"), nullable=True)

    profesor = relationship("Profesor", back_populates="clases")


# ==========================================================
# 6. TABLA: SOCIOS DEL GIMNASIO
# ==========================================================
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

    # Imágenes almacenadas en Base64 con compresión automática
    foto_base64 = Column(Text, nullable=True)          # Foto carnet del socio
    apto_medico_base64 = Column(Text, nullable=True)   # Escaneo/foto del certificado médico

    # Fechas de control médico (vencimiento estándar a 1 año)
    apto_medico_realizacion = Column(Date, nullable=True)
    apto_medico_vencimiento = Column(Date, nullable=True)

    # Identificador único e irrepetible para validar el QR en el molinete
    qr_token = Column(String(100), unique=True, index=True, nullable=False)
    # Estado de la membresía: 'ACTIVO' o 'INACTIVO'
    estado_cuota = Column(String(20), default="ACTIVO")
    fecha_vencimiento_cuota = Column(Date, nullable=True)

    plan_id = Column(Integer, ForeignKey("planes.id"), nullable=True)

    # Moduladores manuales de acceso por recepción:
    bloqueado_manual = Column(Boolean, default=False)         # Bloquea el paso aunque la cuota esté al día
    habilitacion_manual_hasta = Column(Date, nullable=True)   # Prórroga especial de paso aunque la cuota esté vencida

    # Campos reservados para tarjeta de crédito/débito tokenizada en pasarela
    tarjeta_tokenizada = Column(Boolean, default=False)
    tarjeta_marca = Column(String(30), nullable=True)
    tarjeta_ultimos4 = Column(String(4), nullable=True)

    creado_en = Column(DateTime, default=datetime.utcnow)

    # Relaciones en cascada: si se borra un socio, se limpian sus registros dependientes
    plan = relationship("Plan")
    pagos = relationship("Pago", back_populates="socio", cascade="all, delete-orphan")
    facturas = relationship("Factura", back_populates="socio", cascade="all, delete-orphan")
    accesos = relationship("RegistroAcceso", back_populates="socio", cascade="all, delete-orphan")
    rutinas = relationship("Rutina", back_populates="socio", cascade="all, delete-orphan")
    evoluciones = relationship("EvolucionSocio", back_populates="socio", cascade="all, delete-orphan", order_by="desc(EvolucionSocio.fecha)")


# ==========================================================
# 7. TABLA: EVOLUCIÓN FÍSICA Y MEDIDAS CORPORALES
# ==========================================================
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

    # Propiedad calculada en memoria para el Índice de Masa Corporal (IMC)
    @property
    def imc(self):
        if self.peso_kg and self.altura_cm and self.altura_cm > 0:
            altura_m = self.altura_cm / 100.0
            return round(self.peso_kg / (altura_m ** 2), 1)
        return None


# ==========================================================
# 8. TABLAS: RUTINAS Y EJERCICIOS (100% INCLUIDAS / SIN COSTO)
# ==========================================================
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
    # Día o grupo muscular (ej: "Día 1 - Pecho y Bíceps")
    dia_grupo = Column(String(50), nullable=False)
    ejercicio = Column(String(120), nullable=False)
    series = Column(Integer, default=4)
    repeticiones = Column(String(30), default="10-12")
    peso_sugerido = Column(String(30), default="")
    descanso = Column(String(30), default="60s")
    # Estado interactivo que el socio tilda desde su celular
    completado = Column(Boolean, default=False)

    rutina = relationship("Rutina", back_populates="ejercicios")


# ==========================================================
# 9. TABLA: COBROS Y PAGOS (CUOTAS)
# ==========================================================
class Pago(Base):
    __tablename__ = "pagos"

    id = Column(Integer, primary_key=True, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    monto = Column(Float, nullable=False)
    metodo_pago = Column(String(50), default="EFECTIVO")
    concepto = Column(String(150), default="Pago de Cuota")
    # ID de transacción de Mercado Pago para evitar duplicados en el Webhook
    external_payment_id = Column(String(100), unique=True, nullable=True)
    fecha_pago = Column(DateTime, default=datetime.utcnow)

    socio = relationship("Socio", back_populates="pagos")
    # Relación 1 a 1 con su factura fiscal generada
    factura = relationship("Factura", back_populates="pago", uselist=False, cascade="all, delete-orphan")


# ==========================================================
# 10. TABLA: FACTURAS ELECTRÓNICAS (ARCA / EX AFIP)
# ==========================================================
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


# ==========================================================
# 11. TABLA: PRODUCTOS Y KIOSCO (PUNTO DE VENTA)
# ==========================================================
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


# ==========================================================
# 12. TABLA: AUDITORÍA Y CONTROL DE ACCESOS (MOLINETE)
# ==========================================================
class RegistroAcceso(Base):
    __tablename__ = "registros_acceso"

    id = Column(Integer, primary_key=True, index=True)
    socio_id = Column(Integer, ForeignKey("socios.id"), nullable=False)
    fecha_hora = Column(DateTime, default=datetime.utcnow)
    # Resultado del intento: 'PERMITIDO' o 'DENEGADO'
    resultado = Column(String(20), nullable=False)
    # Razón técnica (ej: "OK", "CUOTA_VENCIDA", "APTO_MEDICO_VENCIDO", "BLOQUEADO_MANUAL")
    motivo = Column(String(100), nullable=False)

    socio = relationship("Socio", back_populates="accesos")