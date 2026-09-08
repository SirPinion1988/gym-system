import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Carga las variables de entorno definidas en el archivo local .env (si existe)
load_dotenv()

# Lee la URL de la base de datos configurada en Render o en el archivo .env local.
# Si no encuentra ninguna, utiliza una base SQLite de prueba temporal en memoria/archivo.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

# Render y algunos proveedores entregan la URL con el prefijo antiguo "postgres://",
# pero SQLAlchemy 2.0 exige obligatoriamente "postgresql://". Esta línea realiza la corrección automática.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# CONFIGURACIÓN DEL MOTOR DE BASE DE DATOS (ENGINE)
if "sqlite" in DATABASE_URL:
    # Si estamos en entorno SQLite de prueba local, evitamos problemas de concurrencia de hilos
    engine = create_engine(
        DATABASE_URL, 
        connect_args={"check_same_thread": False}
    )
else:
    # CONFIGURACIÓN DE PRODUCCIÓN PARA SUPABASE (POSTGRESQL):
    # - pool_size: Mantiene hasta 10 conexiones abiertas listas para usar.
    # - max_overflow: Permite hasta 20 conexiones adicionales en picos de mucho tráfico.
    # - pool_timeout: Si todas las conexiones están ocupadas, espera 30 segundos antes de fallar.
    # - pool_recycle: Cierra y renueva las conexiones cada 5 minutos (300s) para evitar sockets caídos.
    # - pool_pre_ping: Comprueba que la conexión siga viva antes de mandar una consulta SQL.
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=300,
        pool_pre_ping=True
    )

# Fábrica de sesiones de base de datos. Se usa en cada endpoint para interactuar con las tablas.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Clase base de la que heredan todos los modelos de tablas en app/models.py
Base = declarative_base()

# GENERADOR DE SESIONES PARA FASTAPI (DEPENDENCY INJECTION)
# Se pasa como argumento en los endpoints con: db: Session = Depends(get_db)
def get_db():
    db = SessionLocal()
    try:
        # Entrega la sesión activa al endpoint que la solicitó
        yield db
    finally:
        # Garantiza que, sin importar si la petición fue exitosa o dio error, la conexión se libere
        db.close()