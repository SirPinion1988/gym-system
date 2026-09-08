import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

# Ajuste automático de URL para PostgreSQL de Supabase
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Control de flujo de conexiones hacia la base de datos
if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL, 
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,          # Conexiones base activas
        max_overflow=20,       # Picos máximos temporales en horas pico
        pool_timeout=30,       # Segundos de espera antes de error por saturación
        pool_recycle=300,      # Renovar conexiones cada 5 minutos para evitar caídas de socket
        pool_pre_ping=True     # Verifica si la conexión sigue viva antes de ejecutar una query
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()