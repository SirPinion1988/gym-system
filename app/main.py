# ==========================================================
# SERVIDOR BACKEND FASTAPI - SISTEMA DE GESTIÓN DE GIMNASIO
# ==========================================================
import os
import uuid
import qrcode
import io
import csv
import base64
import urllib.parse
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional
from PIL import Image

from fastapi import FastAPI, Depends, Request, Form, HTTPException, status, Response, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

import mercadopago

# Control de flujo / Rate Limiting para blindar rutas contra spam y fuerza bruta
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .database import engine, Base, get_db
from . import models, auth, billing

# Creación automática de tablas en Supabase/PostgreSQL si aún no existen
Base.metadata.create_all(bind=engine)

# Limitador de tasa por IP
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Sistema de Gestión de Gimnasio - GymPro")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ==========================================================
# VARIABLES DE ENTORNO Y CONFIGURACIÓN COMERCIAL
# ==========================================================
GYM_NOMBRE = os.getenv("EMPRESA_RAZON_SOCIAL", "Barbie Gym & Fitness")
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "TEST-0000000000000000-000000-0000000000000000-0000000000000000-000000000")
mp_sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "https://dancebri.onrender.com")

CURRENT_FILE = Path(__file__).resolve()
APP_DIR = CURRENT_FILE.parent
ROOT_DIR = APP_DIR.parent

POSSIBLE_TEMPLATE_DIRS = [ROOT_DIR / "templates", APP_DIR / "templates"]
TEMPLATE_DIR = next((p for p in POSSIBLE_TEMPLATE_DIRS if p.exists() and p.is_dir()), ROOT_DIR / "templates")
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

POSSIBLE_STATIC_DIRS = [ROOT_DIR / "static", APP_DIR / "static"]
STATIC_DIR = next((p for p in POSSIBLE_STATIC_DIRS if p.exists() and p.is_dir()), ROOT_DIR / "static")
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ==========================================================
# UTILIDADES: COMPRESIÓN DE IMÁGENES Y CÓDIGO QR
# ==========================================================
def optimizar_imagen(archivo_bytes: bytes, max_ancho: int = 1000, calidad: int = 70) -> Optional[str]:
    """Comprime imágenes a JPEG < 100 KB para no agotar la base de datos de Supabase."""
    if not archivo_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(archivo_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        if img.width > max_ancho:
            ratio = max_ancho / float(img.width)
            alto = int(float(img.height) * float(ratio))
            img = img.resize((max_ancho, alto), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=calidad, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"[IMAGEN ERROR]: {e}")
        return None


def calcular_edad(fecha_nac: date) -> int:
    hoy = date.today()
    return hoy.year - fecha_nac.year - ((hoy.month, hoy.day) < (fecha_nac.month, fecha_nac.day))


def sumar_un_anio(fecha: date) -> date:
    try:
        return fecha.replace(year=fecha.year + 1)
    except ValueError:
        return fecha + (date(fecha.year + 1, 3, 1) - date(fecha.year, 3, 1))


def generar_qr_base64(texto: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(texto)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


# ==========================================================
# KEEP-ALIVE (SALUD) E INICIALIZACIÓN DE SUPERUSUARIO MASTER
# ==========================================================
@app.get("/health")
def health_check():
    """Ping de 2ms para que monitores externos mantengan el servidor despierto."""
    return {"status": "healthy", "app": GYM_NOMBRE, "timestamp": datetime.utcnow().isoformat()}


@app.on_event("startup")
def startup_db_init():
    """Crea o actualiza automáticamente a Sirpinion y admin."""
    from .database import SessionLocal
    db = SessionLocal()
    try:
        # 1. SUPERUSUARIO MASTER: 'Sirpinion'
        master = db.query(models.UsuarioSistema).filter_by(username="Sirpinion").first()
        if not master:
            master_user = models.UsuarioSistema(
                username="Sirpinion",
                password_hash=auth.hash_password("admin123"),
                nombre="SuperUsuario Master",
                rol="MASTER",
                email="programacionifts2026@gmail.com",
                activo=True
            )
            db.add(master_user)
            db.commit()
        else:
            master.rol = "MASTER"
            master.email = "programacionifts2026@gmail.com"
            master.password_hash = auth.hash_password("admin123")
            master.activo = True
            db.commit()

        # 2. ADMINISTRADOR GENERAL: 'admin'
        admin = db.query(models.UsuarioSistema).filter_by(username="admin").first()
        if not admin:
            admin_user = models.UsuarioSistema(
                username="admin",
                password_hash=auth.hash_password("admin123"),
                nombre="Administrador General",
                rol="ADMIN",
                email="admin@gympro.com",
                activo=True
            )
            db.add(admin_user)
            db.commit()
        else:
            if admin.rol != "MASTER":
                admin.rol = "ADMIN"
            admin.activo = True
            db.commit()

        # 3. Membresías estándar si no hay ninguna
        if db.query(models.Plan).count() == 0:
            db.add_all([
                models.Plan(nombre="Pase Libre Mensual", precio=25000.0, dias_duracion=30, descripcion="Acceso libre e ilimitado."),
                models.Plan(nombre="3 Veces por Semana", precio=18000.0, dias_duracion=30, descripcion="Hasta 3 ingresos semanales.")
            ])
            db.commit()
    except Exception as e:
        print(f"[STARTUP ALERTA]: {e}")
    finally:
        db.close()


def procesar_factura_y_mail(socio_id: int, pago_id: int, monto: float, concepto: str):
    """Genera comprobante fiscal ARCA y lo despacha por correo."""
    from .database import SessionLocal
    db = SessionLocal()
    try:
        socio = db.query(models.Socio).get(socio_id)
        pago = db.query(models.Pago).get(pago_id)
        if not socio or not pago:
            return

        arca_data = billing.emitir_factura_arca(f"{socio.nombre} {socio.apellido}", socio.dni, monto, concepto)
        factura = models.Factura(
            pago_id=pago.id,
            socio_id=socio.id,
            tipo_comprobante=arca_data["tipo_comprobante"],
            punto_venta=arca_data["punto_venta"],
            numero_comprobante=arca_data["numero_comprobante"],
            cae=arca_data["cae"],
            cae_vencimiento=arca_data["cae_vencimiento"],
            monto_total=monto
        )
        db.add(factura)
        db.commit()

        pdf_bytes = billing.generar_pdf_factura(f"{socio.nombre} {socio.apellido}", socio.dni, socio.email, arca_data, concepto)
        nro_fmt = f"{arca_data['punto_venta']:04d}-{arca_data['numero_comprobante']:08d}"
        enviado = billing.enviar_correo_factura(socio.email, socio.nombre, pdf_bytes, nro_fmt)
        if enviado:
            factura.enviada_por_mail = True
            db.commit()
    except Exception as e:
        print(f"[FACTURA ERROR]: {e}")
    finally:
        db.close()


# ==========================================================
# RUTAS DE ACCESO (LOGIN COMPATIBLE CON CELULARES Y SAFARI)
# ==========================================================
@app.get("/", response_class=HTMLResponse)
def index_hub(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user)):
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="login_hub.html", context={"error_admin": None, "error_socio": None, "gym_nombre": GYM_NOMBRE})


@app.get("/login", response_class=HTMLResponse)
def login_view(request: Request):
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.post("/login/admin")
@limiter.limit("15/minute")
def login_admin_action(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Login personal (Sirpinion, Administradores y Operadores).
    Usa .strip() para evitar que el teclado móvil meta espacios al final.
    """
    clean_user = username.strip()
    clean_pass = password.strip()

    # Busca sin importar mayúsculas o minúsculas que el celular suele poner automáticamente
    user = db.query(models.UsuarioSistema).filter(
        func.lower(models.UsuarioSistema.username) == func.lower(clean_user)
    ).first()

    if not user or not auth.verify_password(clean_pass, user.password_hash) or not user.activo:
        return templates.TemplateResponse(
            request=request, 
            name="login_hub.html", 
            context={"error_admin": "Credenciales inválidas o cuenta inactiva.", "error_socio": None, "gym_nombre": GYM_NOMBRE}
        )

    token = auth.create_access_token(data={"sub": user.username, "rol": user.rol})
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    # CONFIGURACIÓN ROBUSTA DE COOKIE PARA CELULARES (IOS SAFARI / ANDROID CHROME):
    # samesite="lax", path="/" y max_age garantizan que no sea eliminada por el móvil.
    es_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=es_https,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 días de sesión activa
        path="/"
    )
    return resp


@app.post("/login/socio")
@limiter.limit("20/minute")
def login_socio_action(
    request: Request,
    dni: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Acceso del socio:
    Usuario = DNI (sin puntos ni espacios)
    Contraseña = Correo electrónico
    """
    clean_dni = "".join([c for c in dni if c.isdigit()])  # Extrae solo números por si ponen puntos
    clean_email = email.strip().lower()

    socio = db.query(models.Socio).filter(
        models.Socio.dni == clean_dni,
        func.lower(models.Socio.email) == clean_email
    ).first()

    if not socio:
        return templates.TemplateResponse(
            request=request, 
            name="login_hub.html", 
            context={"error_socio": "DNI o correo no encontrados. Verifica que coincidan con tu registro.", "error_admin": None, "gym_nombre": GYM_NOMBRE}
        )

    return RedirectResponse(url=f"/socio/credencial/{socio.id}", status_code=status.HTTP_302_FOUND)


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie("access_token", path="/")
    return resp


# ==========================================================
# PERSONAL: ROLES, PERMISOS Y CAMBIO DE CONTRASEÑA
# ==========================================================
@app.get("/usuarios", response_class=HTMLResponse)
def usuarios_sistema_view(
    request: Request,
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user or user.rol not in ["MASTER", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Acceso denegado.")

    usuarios = db.query(models.UsuarioSistema).order_by(models.UsuarioSistema.id.asc()).all()
    return templates.TemplateResponse(request=request, name="usuarios.html", context={"user": user, "usuarios": usuarios, "gym_nombre": GYM_NOMBRE})


@app.post("/usuarios/crear")
def crear_usuario_sistema(
    nombre: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    email: str = Form(...),
    rol: str = Form("OPERADOR"),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user or user.rol not in ["MASTER", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Acceso denegado.")

    if user.rol == "ADMIN" and rol == "MASTER":
        raise HTTPException(status_code=403, detail="Solo Sirpinion puede asignar rol MASTER.")

    existe = db.query(models.UsuarioSistema).filter_by(username=username.strip()).first()
    if existe:
        raise HTTPException(status_code=400, detail="El usuario ya existe.")

    nuevo_usuario = models.UsuarioSistema(
        nombre=nombre.strip(),
        username=username.strip(),
        password_hash=auth.hash_password(password.strip()),
        email=email.strip().lower(),
        rol=rol,
        activo=True
    )
    db.add(nuevo_usuario)
    db.commit()
    return RedirectResponse(url="/usuarios", status_code=status.HTTP_302_FOUND)


@app.post("/usuarios/toggle-estado/{usuario_id}")
def toggle_estado_usuario(
    usuario_id: int,
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user or user.rol not in ["MASTER", "ADMIN"]:
        raise HTTPException(status_code=403)
    target = db.query(models.UsuarioSistema).get(usuario_id)
    if not target or target.id == user.id:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta.")
    if user.rol == "ADMIN" and target.rol == "MASTER":
        raise HTTPException(status_code=403, detail="No tienes permisos para suspender al SuperUsuario.")

    target.activo = not target.activo
    db.commit()
    return RedirectResponse(url="/usuarios", status_code=status.HTTP_302_FOUND)


@app.post("/usuarios/cambiar-mi-password")
def cambiar_mi_password(
    password_actual: str = Form(...),
    password_nueva: str = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)
    if not auth.verify_password(password_actual.strip(), user.password_hash):
        return RedirectResponse(url="/dashboard?error=clave_actual_incorrecta", status_code=status.HTTP_302_FOUND)

    user.password_hash = auth.hash_password(password_nueva.strip())
    db.commit()
    return RedirectResponse(url="/dashboard?exito=clave_modificada", status_code=status.HTTP_302_FOUND)


@app.post("/recuperar-password/solicitar")
def solicitar_recuperacion_password(
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    usuario = db.query(models.UsuarioSistema).filter_by(email=email.strip().lower()).first()
    if usuario:
        token = uuid.uuid4().hex
        usuario.reset_token = token
        usuario.reset_token_expira = datetime.utcnow() + timedelta(minutes=30)
        db.commit()
        link_recuperar = f"{APP_PUBLIC_URL}/recuperar-password/confirmar?token={token}"
        background_tasks.add_task(billing.enviar_correo_recuperacion_password, usuario.email, usuario.nombre, link_recuperar)

    return RedirectResponse(url="/?aviso=recuperacion_enviada", status_code=status.HTTP_302_FOUND)


# ==========================================================
# DASHBOARD
# ==========================================================
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    try:
        total_socios = db.query(models.Socio).count() or 0
        socios_activos = db.query(models.Socio).filter(models.Socio.estado_cuota == "ACTIVO").count() or 0
        total_profes = db.query(models.Profesor).count() or 0
        total_clases = db.query(models.Actividad).count() or 0
        ultimos_accesos = db.query(models.RegistroAcceso).options(joinedload(models.RegistroAcceso.socio)).order_by(models.RegistroAcceso.fecha_hora.desc()).limit(10).all() or []
    except Exception as e:
        print(f"[DASHBOARD ERROR]: {e}")
        total_socios, socios_activos, total_profes, total_clases, ultimos_accesos = 0, 0, 0, 0, []

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "user": user,
        "total_socios": total_socios,
        "socios_activos": socios_activos,
        "total_profes": total_profes,
        "total_clases": total_clases,
        "ultimos_accesos": ultimos_accesos,
        "gym_nombre": GYM_NOMBRE
    })


# ==========================================================
# SOCIOS Y CERTIFICADOS MÉDICOS
# ==========================================================
@app.get("/socios", response_class=HTMLResponse)
def socios_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    socios = db.query(models.Socio).order_by(models.Socio.id.desc()).all()
    planes = db.query(models.Plan).filter(models.Plan.activo == True).all()
    hoy = date.today()
    return templates.TemplateResponse(request=request, name="socios.html", context={
        "user": user,
        "socios": socios,
        "planes": planes,
        "hoy": hoy,
        "gym_nombre": GYM_NOMBRE
    })


@app.post("/socios/crear")
async def crear_socio(
    background_tasks: BackgroundTasks,
    dni: str = Form(...),
    nombre: str = Form(...),
    apellido: str = Form(...),
    fecha_nacimiento: str = Form(...),
    celular: str = Form(...),
    email: str = Form(...),
    plan_id: Optional[int] = Form(None),
    apto_medico_realizacion: Optional[str] = Form(None),
    foto: Optional[UploadFile] = File(None),
    foto_apto: Optional[UploadFile] = File(None),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    clean_dni = "".join([c for c in dni if c.isdigit()])
    f_nac = datetime.strptime(fecha_nacimiento.strip(), "%Y-%m-%d").date()
    edad = calcular_edad(f_nac)
    
    f_realizacion = None
    f_vto_apto = None
    if apto_medico_realizacion and apto_medico_realizacion.strip():
        f_realizacion = datetime.strptime(apto_medico_realizacion.strip(), "%Y-%m-%d").date()
        f_vto_apto = sumar_un_anio(f_realizacion)

    token_qr = f"GYM-{clean_dni}-{uuid.uuid4().hex[:8]}"

    foto_b64 = None
    if foto and foto.filename:
        contenido = await foto.read()
        foto_b64 = optimizar_imagen(contenido, max_ancho=500, calidad=75)

    apto_b64 = None
    if foto_apto and foto_apto.filename:
        contenido_apto = await foto_apto.read()
        apto_b64 = optimizar_imagen(contenido_apto, max_ancho=1000, calidad=70)

    nuevo_socio = models.Socio(
        dni=clean_dni,
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        fecha_nacimiento=f_nac,
        edad=edad,
        celular=celular.strip(),
        email=email.strip().lower(),
        plan_id=plan_id,
        foto_base64=foto_b64,
        apto_medico_base64=apto_b64,
        apto_medico_realizacion=f_realizacion,
        apto_medico_vencimiento=f_vto_apto,
        qr_token=token_qr,
        estado_cuota="ACTIVO",
        fecha_vencimiento_cuota=date.today() + timedelta(days=30),
        bloqueado_manual=False
    )
    db.add(nuevo_socio)
    db.commit()

    url_cred = f"{APP_PUBLIC_URL}/socio/credencial/{nuevo_socio.id}"
    background_tasks.add_task(
        billing.enviar_correo_bienvenida,
        destinatario=nuevo_socio.email,
        nombre=nuevo_socio.nombre,
        dni=nuevo_socio.dni,
        url_credencial=url_cred
    )

    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)


@app.post("/socios/editar/{socio_id}")
async def editar_socio(
    socio_id: int,
    nombre: str = Form(...),
    apellido: str = Form(...),
    dni: str = Form(...),
    fecha_nacimiento: str = Form(...),
    celular: str = Form(...),
    email: str = Form(...),
    plan_id: Optional[int] = Form(None),
    apto_medico_realizacion: Optional[str] = Form(None),
    foto: Optional[UploadFile] = File(None),
    foto_apto: Optional[UploadFile] = File(None),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)

    socio.nombre = nombre.strip()
    socio.apellido = apellido.strip()
    socio.dni = "".join([c for c in dni if c.isdigit()])
    
    f_nac = datetime.strptime(fecha_nacimiento.strip(), "%Y-%m-%d").date()
    socio.fecha_nacimiento = f_nac
    socio.edad = calcular_edad(f_nac)
    socio.celular = celular.strip()
    socio.email = email.strip().lower()
    socio.plan_id = plan_id

    if foto and foto.filename:
        contenido = await foto.read()
        opt = optimizar_imagen(contenido, max_ancho=500, calidad=75)
        if opt:
            socio.foto_base64 = opt

    if foto_apto and foto_apto.filename:
        contenido_apto = await foto_apto.read()
        opt_apto = optimizar_imagen(contenido_apto, max_ancho=1000, calidad=70)
        if opt_apto:
            socio.apto_medico_base64 = opt_apto

    if apto_medico_realizacion and apto_medico_realizacion.strip():
        f_realiz = datetime.strptime(apto_medico_realizacion.strip(), "%Y-%m-%d").date()
        socio.apto_medico_realizacion = f_realiz
        socio.apto_medico_vencimiento = sumar_un_anio(f_realiz)
    else:
        socio.apto_medico_realizacion = None
        socio.apto_medico_vencimiento = None

    db.commit()
    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)


# ELIMINAR SOCIO DEFINITIVAMENTE (CASCADE EN TABLAS DEPENDIENTES)
@app.post("/socios/eliminar/{socio_id}")
def eliminar_socio_definitivo(
    socio_id: int, 
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), 
    db: Session = Depends(get_db)
):
    if not user or user.rol not in ["MASTER", "ADMIN"]:
        raise HTTPException(status_code=403, detail="No tienes permisos para eliminar socios.")

    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404, detail="Socio no encontrado.")

    try:
        db.delete(socio)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[ERROR ELIMINAR SOCIO]: {e}")
        raise HTTPException(status_code=500, detail="Error al eliminar socio.")

    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)


@app.get("/api/socios/{socio_id}/apto-medico")
def api_obtener_apto_medico(socio_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404, detail="Socio no encontrado")
    return JSONResponse({"socio": f"{socio.nombre} {socio.apellido}", "apto_base64": socio.apto_medico_base64})


@app.post("/socios/anular-cuota/{socio_id}")
def anular_cuota_socio(socio_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)
    socio.estado_cuota = "INACTIVO"
    socio.fecha_vencimiento_cuota = date.today() - timedelta(days=1)
    db.commit()
    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)


@app.post("/socios/gestionar-permiso/{socio_id}")
def gestionar_permiso_socio(
    socio_id: int,
    accion: str = Form(...),
    dias_duracion: int = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)

    hoy = date.today()
    if accion == "habilitar":
        socio.bloqueado_manual = False
        socio.habilitacion_manual_hasta = hoy + timedelta(days=dias_duracion)
    else:
        socio.bloqueado_manual = True
        socio.habilitacion_manual_hasta = None

    db.commit()
    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)


@app.post("/socios/asignar-plan/{socio_id}")
def asignar_plan_socio(socio_id: int, plan_id: int = Form(...), user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)
    socio.plan_id = plan_id
    db.commit()
    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)


@app.get("/socios/qr/{socio_id}")
def ver_qr_socio(socio_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)
    qr_b64 = generar_qr_base64(socio.qr_token)
    return JSONResponse({
        "socio": f"{socio.nombre} {socio.apellido}",
        "dni": socio.dni,
        "token": socio.qr_token,
        "foto": socio.foto_base64,
        "qr_image": f"data:image/png;base64,{qr_b64}"
    })


@app.get("/api/socios/{socio_id}/accesos")
def api_historial_accesos_socio(socio_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404, detail="Socio no encontrado")

    accesos = db.query(models.RegistroAcceso).filter(models.RegistroAcceso.socio_id == socio_id).order_by(models.RegistroAcceso.fecha_hora.desc()).limit(50).all()
    hoy = date.today()
    primer_dia_mes = date(hoy.year, hoy.month, 1)
    accesos_mes = db.query(models.RegistroAcceso).filter(
        models.RegistroAcceso.socio_id == socio_id,
        models.RegistroAcceso.resultado == "PERMITIDO",
        models.RegistroAcceso.fecha_hora >= primer_dia_mes
    ).count()

    lista = [{"id": a.id, "fecha": a.fecha_hora.strftime("%d/%m/%Y"), "hora": a.fecha_hora.strftime("%H:%M:%S"), "resultado": a.resultado, "motivo": a.motivo} for a in accesos]

    return JSONResponse({
        "socio": f"{socio.nombre} {socio.apellido}",
        "dni": socio.dni,
        "foto": socio.foto_base64,
        "accesos_mes": accesos_mes,
        "total_registros": len(lista),
        "historial": lista
    })


@app.get("/api/socio/link-pago/{socio_id}")
def api_generar_link_pago(socio_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404, detail="Socio no encontrado")

    plan = socio.plan or db.query(models.Plan).filter(models.Plan.activo == True).first()
    if not plan:
        raise HTTPException(status_code=400, detail="No hay plan configurado")

    link_pago = f"{APP_PUBLIC_URL}/socio/credencial/{socio.id}"
    try:
        preference_data = {
            "items": [{"title": f"Abono {plan.nombre} - {socio.nombre} {socio.apellido}", "quantity": 1, "unit_price": float(plan.precio), "currency_id": "ARS"}],
            "payer": {"email": socio.email, "name": socio.nombre, "surname": socio.apellido, "identification": {"type": "DNI", "number": socio.dni}},
            "external_reference": f"SOCIO-{socio.id}-{int(datetime.utcnow().timestamp())}",
            "notification_url": f"{APP_PUBLIC_URL}/api/pagos/webhook",
            "back_urls": {
                "success": f"{APP_PUBLIC_URL}/socio/credencial/{socio.id}?pago_exitoso=1",
                "failure": f"{APP_PUBLIC_URL}/socio/credencial/{socio.id}?error_pago=1",
                "pending": f"{APP_PUBLIC_URL}/socio/credencial/{socio.id}?pago_pendiente=1"
            },
            "auto_return": "approved"
        }
        pref_res = mp_sdk.preference().create(preference_data)
        init_point = pref_res["response"].get("init_point")
        if init_point:
            link_pago = init_point
    except Exception as e:
        print(f"[MP ERROR]: {e}")

    cel_clean = "".join([c for c in socio.celular if c.isdigit()])
    mensaje = f"Hola {socio.nombre}! Te enviamos el link de {GYM_NOMBRE} para abonar tu cuota de {plan.nombre} (${plan.precio:.2f}): {link_pago}"
    whatsapp_url = f"https://wa.me/{cel_clean}?text={urllib.parse.quote(mensaje)}" if cel_clean else f"https://wa.me/?text={urllib.parse.quote(mensaje)}"

    return JSONResponse({"socio": f"{socio.nombre} {socio.apellido}", "link_pago": link_pago, "whatsapp_url": whatsapp_url, "monto": plan.precio, "plan": plan.nombre})


# ==========================================================
# CRON JOB: AVISOS PREVENTIVOS DE VENCIMIENTO (7 Y 1 DÍA)
# ==========================================================
@app.get("/api/cron/verificar-vencimientos")
def cron_verificar_vencimientos(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    hoy = date.today()
    vto_7 = hoy + timedelta(days=7)
    vto_1 = hoy + timedelta(days=1)

    socios_7 = db.query(models.Socio).filter(models.Socio.fecha_vencimiento_cuota == vto_7, models.Socio.estado_cuota == "ACTIVO").all()
    socios_1 = db.query(models.Socio).filter(models.Socio.fecha_vencimiento_cuota == vto_1, models.Socio.estado_cuota == "ACTIVO").all()

    for s in socios_7:
        link = f"{APP_PUBLIC_URL}/socio/credencial/{s.id}"
        background_tasks.add_task(billing.enviar_aviso_vencimiento, s.email, s.nombre, 7, s.fecha_vencimiento_cuota.strftime('%d/%m/%Y'), link)

    for s in socios_1:
        link = f"{APP_PUBLIC_URL}/socio/credencial/{s.id}"
        background_tasks.add_task(billing.enviar_aviso_vencimiento, s.email, s.nombre, 1, s.fecha_vencimiento_cuota.strftime('%d/%m/%Y'), link)

    return JSONResponse({"status": "ok", "avisos_7_dias": len(socios_7), "avisos_1_dia": len(socios_1)})


# ==========================================================
# RUTINAS Y EJERCICIOS (3x10 ESTÁNDAR)
# ==========================================================
@app.get("/rutinas", response_class=HTMLResponse)
def rutinas_admin_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    try:
        rutinas = db.query(models.Rutina).options(
            joinedload(models.Rutina.socio),
            joinedload(models.Rutina.profesor),
            joinedload(models.Rutina.ejercicios)
        ).order_by(models.Rutina.id.desc()).all() or []
    except Exception:
        rutinas = []

    try:
        socios = db.query(models.Socio).order_by(models.Socio.apellido.asc()).all() or []
    except Exception:
        socios = []

    try:
        profesores = db.query(models.Profesor).filter(models.Profesor.activo == True).order_by(models.Profesor.apellido.asc()).all() or []
    except Exception:
        profesores = []

    return templates.TemplateResponse(request=request, name="rutinas_admin.html", context={
        "user": user,
        "rutinas": rutinas,
        "socios": socios,
        "profesores": profesores,
        "gym_nombre": GYM_NOMBRE
    })


@app.post("/rutinas/crear")
def crear_rutina(
    socio_id: int = Form(...),
    profesor_id: Optional[str] = Form(None),
    nombre_rutina: str = Form(...),
    objetivo: Optional[str] = Form(""),
    plantilla: Optional[str] = Form("PERSONALIZADA"),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    profe_id = int(profesor_id) if profesor_id and profesor_id.strip() and profesor_id != "" else None
    db.query(models.Rutina).filter(models.Rutina.socio_id == socio_id).update({"activa": False})

    nueva_rutina = models.Rutina(
        socio_id=socio_id,
        profesor_id=profe_id,
        nombre_rutina=nombre_rutina.strip(),
        objetivo=objetivo.strip() if objetivo else "General",
        activa=True
    )
    db.add(nueva_rutina)
    db.commit()

    if plantilla == "HIPERTROFIA":
        ejercicios_plantilla = [
            ("Día 1 - Pecho y Bíceps", "Press Banca Plano con Barra", 3, "10", "60kg", "60s"),
            ("Día 1 - Pecho y Bíceps", "Aperturas en Banco Plano", 3, "10", "14kg", "60s"),
            ("Día 1 - Pecho y Bíceps", "Curl de Bíceps con Barra Z", 3, "10", "25kg", "60s"),
            ("Día 2 - Espalda y Tríceps", "Jalón al Pecho en Polea", 3, "10", "55kg", "60s"),
            ("Día 2 - Espalda y Tríceps", "Remo Unilateral con Mancuerna", 3, "10", "22kg", "60s"),
            ("Día 2 - Espalda y Tríceps", "Extensiones de Tríceps en Polea Alta", 3, "10", "30kg", "60s"),
            ("Día 3 - Piernas y Hombros", "Sentadilla Libre con Barra", 3, "10", "70kg", "90s"),
            ("Día 3 - Piernas y Hombros", "Prensa 45°", 3, "10", "120kg", "90s"),
            ("Día 3 - Piernas y Hombros", "Press Militar con Barra", 3, "10", "35kg", "60s"),
        ]
        for d, ej, s, rep, p, desc in ejercicios_plantilla:
            db.add(models.EjercicioRutina(rutina_id=nueva_rutina.id, dia_grupo=d, ejercicio=ej, series=s, repeticiones=rep, peso_sugerido=p, descanso=desc))
        db.commit()
    elif plantilla == "FUERZA":
        ejercicios_plantilla = [
            ("Día 1 - Tren Superior", "Press de Banca Plano con Barra", 3, "10", "Pesado", "120s"),
            ("Día 1 - Tren Superior", "Remo con Barra", 3, "10", "Pesado", "120s"),
            ("Día 2 - Tren Inferior", "Sentadilla Libre con Barra", 3, "10", "Pesado", "150s"),
            ("Día 2 - Tren Inferior", "Peso Muerto Convencional", 3, "10", "Pesado", "180s"),
        ]
        for d, ej, s, rep, p, desc in ejercicios_plantilla:
            db.add(models.EjercicioRutina(rutina_id=nueva_rutina.id, dia_grupo=d, ejercicio=ej, series=s, repeticiones=rep, peso_sugerido=p, descanso=desc))
        db.commit()
    elif plantilla == "PERDIDA_PESO":
        ejercicios_plantilla = [
            ("Circuito A", "Sentadilla Libre con Barra", 3, "10", "Ligero", "45s"),
            ("Circuito A", "Crunches Abdominales", 3, "15", "Corporal", "30s"),
            ("Circuito B", "Estocadas / Zancadas con Mancuerna", 3, "10", "10kg", "45s"),
            ("Circuito B", "Plancha Isométrica (3x45s)", 3, "45s", "Corporal", "45s"),
        ]
        for d, ej, s, rep, p, desc in ejercicios_plantilla:
            db.add(models.EjercicioRutina(rutina_id=nueva_rutina.id, dia_grupo=d, ejercicio=ej, series=s, repeticiones=rep, peso_sugerido=p, descanso=desc))
        db.commit()

    return RedirectResponse(url="/rutinas", status_code=status.HTTP_302_FOUND)


@app.post("/rutinas/{rutina_id}/ejercicio/crear")
def crear_ejercicio_rutina(
    rutina_id: int,
    dia_grupo: str = Form(...),
    ejercicio: str = Form(...),
    series: int = Form(3),
    repeticiones: str = Form("10"),
    peso_sugerido: Optional[str] = Form(""),
    descanso: Optional[str] = Form("60s"),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)
    nuevo_ej = models.EjercicioRutina(
        rutina_id=rutina_id,
        dia_grupo=dia_grupo.strip(),
        ejercicio=ejercicio.strip(),
        series=series,
        repeticiones=repeticiones.strip(),
        peso_sugerido=peso_sugerido.strip() if peso_sugerido else "",
        descanso=descanso.strip() if descanso else "60s",
        completado=False
    )
    db.add(nuevo_ej)
    db.commit()
    return RedirectResponse(url="/rutinas", status_code=status.HTTP_302_FOUND)


@app.post("/rutinas/ejercicio/eliminar/{ejercicio_id}")
def eliminar_ejercicio_rutina(ejercicio_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    ej = db.query(models.EjercicioRutina).get(ejercicio_id)
    if ej:
        db.delete(ej)
        db.commit()
    return RedirectResponse(url="/rutinas", status_code=status.HTTP_302_FOUND)


@app.post("/rutinas/eliminar/{rutina_id}")
def eliminar_rutina_completa(rutina_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    rutina = db.query(models.Rutina).get(rutina_id)
    if rutina:
        db.delete(rutina)
        db.commit()
    return RedirectResponse(url="/rutinas", status_code=status.HTTP_302_FOUND)


@app.post("/api/socio/rutina/toggle-ejercicio/{ejercicio_id}")
def api_toggle_ejercicio_socio(ejercicio_id: int, db: Session = Depends(get_db)):
    ej = db.query(models.EjercicioRutina).get(ejercicio_id)
    if not ej:
        raise HTTPException(status_code=404, detail="Ejercicio no encontrado")

    ej.completado = not ej.completado
    db.commit()

    total = db.query(models.EjercicioRutina).filter(models.EjercicioRutina.rutina_id == ej.rutina_id).count() or 1
    hechos = db.query(models.EjercicioRutina).filter(models.EjercicioRutina.rutina_id == ej.rutina_id, models.EjercicioRutina.completado == True).count() or 0
    porcentaje = int((hechos / total) * 100)

    return JSONResponse({"ejercicio_id": ej.id, "completado": ej.completado, "porcentaje": porcentaje, "hechos": hechos, "total": total})


@app.post("/api/socio/rutina/reiniciar/{rutina_id}")
def api_reiniciar_rutina_socio(rutina_id: int, db: Session = Depends(get_db)):
    db.query(models.EjercicioRutina).filter(models.EjercicioRutina.rutina_id == rutina_id).update({"completado": False})
    db.commit()
    return JSONResponse({"status": "reset_ok"})


# ==========================================================
# CLASES Y PROFESORES
# ==========================================================
@app.get("/clases-profesores", response_class=HTMLResponse)
def clases_profesores_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    try:
        profesores = db.query(models.Profesor).order_by(models.Profesor.id.desc()).all()
    except Exception:
        profesores = []
    try:
        actividades = db.query(models.Actividad).options(joinedload(models.Actividad.profesor)).order_by(models.Actividad.id.desc()).all()
    except Exception:
        actividades = []

    return templates.TemplateResponse(request=request, name="clases_profesores.html", context={
        "user": user, "profesores": profesores, "actividades": actividades, "gym_nombre": GYM_NOMBRE
    })


@app.post("/profesores/crear")
def crear_profesor(
    nombre: str = Form(...),
    apellido: str = Form(...),
    dni: str = Form(...),
    celular: str = Form(...),
    especialidad: str = Form(...),
    sueldo: float = Form(0.0),
    tipo_sueldo: str = Form("MENSUAL"),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    profe = models.Profesor(
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        dni=dni.strip(),
        celular=celular.strip(),
        especialidad=especialidad.strip(),
        sueldo=sueldo if sueldo else 0.0,
        tipo_sueldo=tipo_sueldo if tipo_sueldo else "MENSUAL"
    )
    db.add(profe)
    db.commit()
    return RedirectResponse(url="/clases-profesores", status_code=status.HTTP_302_FOUND)


@app.post("/profesores/pagar/{profesor_id}")
def liquidar_sueldo_profesor(
    profesor_id: int,
    monto: float = Form(...),
    concepto: str = Form("Liquidación Sueldo Mensual"),
    metodo_pago: str = Form("EFECTIVO"),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)
    profe = db.query(models.Profesor).get(profesor_id)
    if not profe:
        raise HTTPException(status_code=404)

    pago_sueldo = models.PagoProfesor(profesor_id=profesor_id, monto=monto, concepto=concepto.strip(), metodo_pago=metodo_pago)
    db.add(pago_sueldo)
    db.commit()
    return RedirectResponse(url="/clases-profesores", status_code=status.HTTP_302_FOUND)


# ELIMINAR PROFESOR
@app.post("/profesores/eliminar/{profesor_id}")
def eliminar_profesor(
    profesor_id: int, 
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), 
    db: Session = Depends(get_db)
):
    if not user or user.rol not in ["MASTER", "ADMIN"]:
        raise HTTPException(status_code=403)
    
    profe = db.query(models.Profesor).get(profesor_id)
    if profe:
        db.delete(profe)
        db.commit()
    return RedirectResponse(url="/clases-profesores", status_code=status.HTTP_302_FOUND)


@app.post("/clases/crear")
def crear_clase(
    nombre: str = Form(...),
    dias: str = Form(...),
    horario: str = Form(...),
    cupo_maximo: int = Form(20),
    profesor_id: Optional[str] = Form(None),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    pid = int(profesor_id) if profesor_id and profesor_id.strip() and profesor_id.strip() != "" else None
    clase = models.Actividad(nombre=nombre.strip(), dias=dias.strip(), horario=horario.strip(), cupo_maximo=cupo_maximo, profesor_id=pid)
    db.add(clase)
    db.commit()
    return RedirectResponse(url="/clases-profesores", status_code=status.HTTP_302_FOUND)


# ELIMINAR CLASE / ACTIVIDAD
@app.post("/clases/eliminar/{actividad_id}")
def eliminar_clase(
    actividad_id: int, 
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), 
    db: Session = Depends(get_db)
):
    if not user or user.rol not in ["MASTER", "ADMIN"]:
        raise HTTPException(status_code=403)
    
    actividad = db.query(models.Actividad).get(actividad_id)
    if actividad:
        db.delete(actividad)
        db.commit()
    return RedirectResponse(url="/clases-profesores", status_code=status.HTTP_302_FOUND)


# ==========================================================
# CAJA Y PLANES
# ==========================================================
@app.get("/caja-pagos", response_class=HTMLResponse)
def pagos_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    try:
        pagos = db.query(models.Pago).options(joinedload(models.Pago.socio)).order_by(models.Pago.id.desc()).limit(30).all() or []
    except Exception:
        pagos = []
    try:
        socios = db.query(models.Socio).order_by(models.Socio.apellido.asc()).all() or []
    except Exception:
        socios = []
    try:
        planes = db.query(models.Plan).order_by(models.Plan.id.asc()).all() or []
    except Exception:
        planes = []

    return templates.TemplateResponse(request=request, name="caja_pagos.html", context={
        "user": user, "pagos": pagos, "socios": socios, "planes": planes, "gym_nombre": GYM_NOMBRE
    })


@app.post("/pagos/registrar")
def registrar_pago(
    background_tasks: BackgroundTasks,
    socio_id: int = Form(...),
    plan_id: int = Form(...),
    metodo_pago: str = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    socio = db.query(models.Socio).get(socio_id)
    plan = db.query(models.Plan).get(plan_id)
    if not socio or not plan:
        raise HTTPException(status_code=404)

    hoy = date.today()
    if socio.fecha_vencimiento_cuota and socio.fecha_vencimiento_cuota > hoy:
        socio.fecha_vencimiento_cuota = socio.fecha_vencimiento_cuota + timedelta(days=plan.dias_duracion)
    else:
        socio.fecha_vencimiento_cuota = hoy + timedelta(days=plan.dias_duracion)

    socio.estado_cuota = "ACTIVO"
    socio.plan_id = plan.id

    concepto = f"Pago Mostrador: {plan.nombre}"
    nuevo_pago = models.Pago(socio_id=socio.id, monto=plan.precio, metodo_pago=metodo_pago, concepto=concepto)
    db.add(nuevo_pago)
    db.commit()

    background_tasks.add_task(procesar_factura_y_mail, socio.id, nuevo_pago.id, plan.precio, concepto)
    return RedirectResponse(url="/caja-pagos", status_code=status.HTTP_302_FOUND)


@app.post("/pagos/editar/{pago_id}")
def editar_pago(
    pago_id: int,
    monto: float = Form(...),
    metodo_pago: str = Form(...),
    concepto: str = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)
    pago = db.query(models.Pago).get(pago_id)
    if not pago:
        raise HTTPException(status_code=404, detail="Cobro no encontrado")

    pago.monto = monto
    pago.metodo_pago = metodo_pago
    pago.concepto = concepto.strip()
    db.commit()
    return RedirectResponse(url="/caja-pagos", status_code=status.HTTP_302_FOUND)


@app.post("/pagos/eliminar/{pago_id}")
def eliminar_pago(pago_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    pago = db.query(models.Pago).get(pago_id)
    if not pago:
        raise HTTPException(status_code=404)

    try:
        socio = pago.socio
        if socio:
            pagos_anteriores = db.query(models.Pago).filter(models.Pago.socio_id == socio.id, models.Pago.id != pago.id).order_by(models.Pago.fecha_pago.desc()).all()
            if not pagos_anteriores:
                socio.estado_cuota = "INACTIVO"
                socio.fecha_vencimiento_cuota = date.today() - timedelta(days=1)
            else:
                dias_restar = socio.plan.dias_duracion if socio.plan else 30
                if socio.fecha_vencimiento_cuota:
                    socio.fecha_vencimiento_cuota = socio.fecha_vencimiento_cuota - timedelta(days=dias_restar)
                    if socio.fecha_vencimiento_cuota < date.today():
                        socio.estado_cuota = "INACTIVO"

        db.query(models.Factura).filter(models.Factura.pago_id == pago.id).delete(synchronize_session=False)
        db.delete(pago)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[ERROR ANULAR]: {e}")

    return RedirectResponse(url="/caja-pagos", status_code=status.HTTP_302_FOUND)


@app.post("/planes/crear")
def crear_plan(
    nombre: str = Form(...),
    precio: float = Form(...),
    dias_duracion: int = Form(30),
    descripcion: str = Form(""),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    p = models.Plan(nombre=nombre.strip(), precio=precio, dias_duracion=dias_duracion, descripcion=descripcion.strip(), activo=True)
    db.add(p)
    db.commit()
    return RedirectResponse(url="/caja-pagos", status_code=status.HTTP_302_FOUND)


@app.post("/planes/editar/{plan_id}")
def editar_plan(
    plan_id: int,
    nombre: str = Form(...),
    precio: float = Form(...),
    dias_duracion: int = Form(...),
    descripcion: str = Form(""),
    activo: Optional[str] = Form(None),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)
    plan = db.query(models.Plan).get(plan_id)
    if not plan:
        raise HTTPException(status_code=404)

    plan.nombre = nombre.strip()
    plan.precio = precio
    plan.dias_duracion = dias_duracion
    plan.descripcion = descripcion.strip()
    plan.activo = True if activo == "on" else False
    db.commit()
    return RedirectResponse(url="/caja-pagos", status_code=status.HTTP_302_FOUND)


# ELIMINAR PLAN DE MEMBRESÍA
@app.post("/planes/eliminar/{plan_id}")
def eliminar_plan(
    plan_id: int, 
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), 
    db: Session = Depends(get_db)
):
    if not user or user.rol not in ["MASTER", "ADMIN"]:
        raise HTTPException(status_code=403)
    
    plan = db.query(models.Plan).get(plan_id)
    if plan:
        db.delete(plan)
        db.commit()
    return RedirectResponse(url="/caja-pagos", status_code=status.HTTP_302_FOUND)


# ==========================================================
# KIOSCO Y PUNTO DE VENTA
# ==========================================================
@app.get("/kiosco", response_class=HTMLResponse)
def kiosco_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    try:
        productos = db.query(models.Producto).filter(models.Producto.activo == True).order_by(models.Producto.nombre.asc()).all() or []
    except Exception:
        productos = []
    try:
        ventas = db.query(models.VentaProducto).options(joinedload(models.VentaProducto.producto)).order_by(models.VentaProducto.id.desc()).limit(20).all() or []
    except Exception:
        ventas = []

    return templates.TemplateResponse(request=request, name="kiosco.html", context={
        "user": user, "productos": productos, "ventas": ventas, "gym_nombre": GYM_NOMBRE
    })


@app.post("/productos/crear")
def crear_producto(
    nombre: str = Form(...),
    categoria: str = Form(...),
    precio_venta: float = Form(...),
    stock: int = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    prod = models.Producto(nombre=nombre.strip(), categoria=categoria, precio_venta=precio_venta, stock=stock)
    db.add(prod)
    db.commit()
    return RedirectResponse(url="/kiosco", status_code=status.HTTP_302_FOUND)


@app.post("/productos/vender")
def vender_producto(
    producto_id: int = Form(...),
    cantidad: int = Form(...),
    metodo_pago: str = Form("EFECTIVO"),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    prod = db.query(models.Producto).get(producto_id)
    if not prod or prod.stock < cantidad:
        return RedirectResponse(url="/kiosco?error=stock_insuficiente", status_code=status.HTTP_302_FOUND)

    prod.stock -= cantidad
    total = prod.precio_venta * cantidad
    venta = models.VentaProducto(producto_id=prod.id, cantidad=cantidad, total=total, metodo_pago=metodo_pago)
    db.add(venta)
    db.commit()
    return RedirectResponse(url="/kiosco", status_code=status.HTTP_302_FOUND)


# ELIMINAR PRODUCTO DEL KIOSCO
@app.post("/productos/eliminar/{producto_id}")
def eliminar_producto(
    producto_id: int, 
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), 
    db: Session = Depends(get_db)
):
    if not user or user.rol not in ["MASTER", "ADMIN"]:
        raise HTTPException(status_code=403)
    
    prod = db.query(models.Producto).get(producto_id)
    if prod:
        db.delete(prod)
        db.commit()
    return RedirectResponse(url="/kiosco", status_code=status.HTTP_302_FOUND)


@app.post("/api/kiosco/crear-cobro-mp")
def api_kiosco_cobro_mp(
    producto_id: int = Form(...),
    cantidad: int = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)
    prod = db.query(models.Producto).get(producto_id)
    if not prod or prod.stock < cantidad:
        raise HTTPException(status_code=400, detail="Stock insuficiente.")

    total = float(prod.precio_venta * cantidad)
    external_ref = f"KIOSCO-{prod.id}-{cantidad}-{int(datetime.utcnow().timestamp())}"
    link_pago = "#"
    qr_b64 = None
    try:
        preference_data = {
            "items": [{"title": f"Kiosco: {prod.nombre} (x{cantidad})", "quantity": 1, "unit_price": total, "currency_id": "ARS"}],
            "external_reference": external_ref,
            "notification_url": f"{APP_PUBLIC_URL}/api/pagos/webhook",
            "back_urls": {
                "success": f"{APP_PUBLIC_URL}/kiosco?pago_exitoso=1",
                "failure": f"{APP_PUBLIC_URL}/kiosco?error_pago=1",
                "pending": f"{APP_PUBLIC_URL}/kiosco?pago_pendiente=1"
            },
            "auto_return": "approved"
        }
        pref_result = mp_sdk.preference().create(preference_data)
        link_pago = pref_result["response"].get("init_point")
        qr_b64 = generar_qr_base64(link_pago)
    except Exception as e:
        print(f"[KIOSCO ERROR]: {e}")

    return JSONResponse({"producto": prod.nombre, "cantidad": cantidad, "total": total, "link_pago": link_pago, "qr_image": f"data:image/png;base64,{qr_b64}" if qr_b64 else None, "external_reference": external_ref})


# ==========================================================
# EXPORTACIONES CSV
# ==========================================================
@app.get("/exportar/pagos-csv")
def exportar_pagos_csv(user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    pagos = db.query(models.Pago).order_by(models.Pago.id.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID", "Fecha", "Socio DNI", "Socio Nombre", "Concepto", "Metodo Pago", "Monto", "CAE ARCA"])
    for p in pagos:
        dni = p.socio.dni if p.socio else "N/A"
        nombre = f"{p.socio.nombre} {p.socio.apellido}" if p.socio else "Eliminado"
        cae_str = p.factura.cae if p.factura else "N/A"
        writer.writerow([p.id, p.fecha_pago.strftime("%Y-%m-%d %H:%M"), dni, nombre, p.concepto, p.metodo_pago, p.monto, cae_str])
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=balance_pagos.csv"})


@app.get("/exportar/ventas-csv")
def exportar_ventas_csv(user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    ventas = db.query(models.VentaProducto).order_by(models.VentaProducto.id.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID", "Fecha", "Producto", "Cantidad", "Metodo Pago", "Total"])
    for v in ventas:
        prod_nom = v.producto.nombre if v.producto else "Eliminado"
        writer.writerow([v.id, v.fecha.strftime("%Y-%m-%d %H:%M"), prod_nom, v.cantidad, v.metodo_pago, v.total])
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=balance_ventas_kiosco.csv"})


@app.get("/exportar/socios-csv")
def exportar_socios_csv(user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socios = db.query(models.Socio).order_by(models.Socio.id.asc()).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID", "DNI", "Nombre", "Apellido", "Email", "Celular", "Plan", "Estado Cuota", "Vencimiento Cuota", "Vencimiento Apto", "Acceso Especial"])
    for s in socios:
        plan_nombre = s.plan.nombre if s.plan else "Sin Plan"
        venc_cuota = s.fecha_vencimiento_cuota.strftime("%Y-%m-%d") if s.fecha_vencimiento_cuota else "N/A"
        venc_apto = s.apto_medico_vencimiento.strftime("%Y-%m-%d") if s.apto_medico_vencimiento else "Sin Apto"
        permiso = s.habilitacion_manual_hasta.strftime("%Y-%m-%d") if s.habilitacion_manual_hasta else "No"
        writer.writerow([s.id, s.dni, s.nombre, s.apellido, s.email, s.celular, plan_nombre, s.estado_cuota, venc_cuota, venc_apto, permiso])
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=padron_socios.csv"})


@app.get("/exportar/accesos-csv")
def exportar_accesos_csv(user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    accesos = db.query(models.RegistroAcceso).options(joinedload(models.RegistroAcceso.socio)).order_by(models.RegistroAcceso.fecha_hora.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID", "Fecha", "Hora", "DNI Socio", "Nombre Socio", "Resultado", "Motivo"])
    for a in accesos:
        dni = a.socio.dni if a.socio else "N/A"
        nombre = f"{a.socio.nombre} {a.socio.apellido}" if a.socio else "Eliminado"
        fecha_str = a.fecha_hora.strftime("%Y-%m-%d") if a.fecha_hora else "-"
        hora_str = a.fecha_hora.strftime("%H:%M:%S") if a.fecha_hora else "-"
        writer.writerow([a.id, fecha_str, hora_str, dni, nombre, a.resultado, a.motivo])
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=auditoria_accesos_molinete.csv"})


# ==========================================================
# DESCARGA DE COMPROBANTE ARCA
# ==========================================================
@app.get("/factura/descargar/{factura_id}")
def descargar_factura_pdf(factura_id: int, db: Session = Depends(get_db)):
    factura = db.query(models.Factura).get(factura_id)
    if not factura or not factura.socio:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    factura_data = {
        "tipo_comprobante": factura.tipo_comprobante,
        "punto_venta": factura.punto_venta,
        "numero_comprobante": factura.numero_comprobante,
        "cae": factura.cae,
        "cae_vencimiento": factura.cae_vencimiento,
        "monto": factura.monto_total
    }
    pdf_bytes = billing.generar_pdf_factura(
        socio_nombre=f"{factura.socio.nombre} {factura.socio.apellido}",
        socio_dni=factura.socio.dni,
        socio_email=factura.socio.email,
        factura_data=factura_data,
        concepto=factura.pago.concepto if factura.pago else "Cuota Gimnasio"
    )
    nro_fmt = f"{factura.punto_venta:04d}-{factura.numero_comprobante:08d}"
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=Factura_ARCA_{nro_fmt}.pdf"})


# ==========================================================
# CREDENCIAL DIGITAL DEL SOCIO
# ==========================================================
@app.get("/socio/credencial/{socio_id}", response_class=HTMLResponse)
def socio_credencial_view(socio_id: int, request: Request, db: Session = Depends(get_db)):
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    hoy = date.today()
    cuota_al_dia = (socio.estado_cuota == "ACTIVO") and (not socio.fecha_vencimiento_cuota or socio.fecha_vencimiento_cuota >= hoy)
    apto_al_dia = socio.apto_medico_vencimiento and socio.apto_medico_vencimiento >= hoy
    permiso_especial = socio.habilitacion_manual_hasta and socio.habilitacion_manual_hasta >= hoy
    habilitado = (not socio.bloqueado_manual) and (permiso_especial or (cuota_al_dia and apto_al_dia))
    qr_b64 = generar_qr_base64(socio.qr_token)

    plan_asignado = socio.plan or db.query(models.Plan).filter(models.Plan.activo == True).first()
    init_point_mp = None
    if not cuota_al_dia and plan_asignado:
        try:
            preference_data = {
                "items": [{"title": f"Abono {plan_asignado.nombre} - {socio.nombre} {socio.apellido}", "quantity": 1, "unit_price": float(plan_asignado.precio), "currency_id": "ARS"}],
                "payer": {"email": socio.email, "name": socio.nombre, "surname": socio.apellido, "identification": {"type": "DNI", "number": socio.dni}},
                "external_reference": f"SOCIO-{socio.id}-{int(datetime.utcnow().timestamp())}",
                "notification_url": f"{APP_PUBLIC_URL}/api/pagos/webhook",
                "back_urls": {
                    "success": f"{APP_PUBLIC_URL}/socio/credencial/{socio.id}?pago_exitoso=1",
                    "failure": f"{APP_PUBLIC_URL}/socio/credencial/{socio.id}?error_pago=1",
                    "pending": f"{APP_PUBLIC_URL}/socio/credencial/{socio.id}?pago_pendiente=1"
                },
                "auto_return": "approved"
            }
            pref_result = mp_sdk.preference().create(preference_data)
            init_point_mp = pref_result["response"].get("init_point")
        except Exception:
            pass

    facturas = db.query(models.Factura).filter(models.Factura.socio_id == socio.id).order_by(models.Factura.id.desc()).all() or []
    rutina_activa = db.query(models.Rutina).options(joinedload(models.Rutina.ejercicios)).filter(models.Rutina.socio_id == socio.id, models.Rutina.activa == True).first()

    porcentaje_rutina = 0
    if rutina_activa and len(rutina_activa.ejercicios) > 0:
        completados = sum(1 for e in rutina_activa.ejercicios if e.completado)
        porcentaje_rutina = int((completados / len(rutina_activa.ejercicios)) * 100)

    return templates.TemplateResponse(request=request, name="socio_credencial.html", context={
        "socio": socio,
        "plan": plan_asignado,
        "habilitado": habilitado,
        "cuota_al_dia": cuota_al_dia,
        "apto_al_dia": apto_al_dia,
        "permiso_especial": permiso_especial,
        "qr_image": f"data:image/png;base64,{qr_b64}",
        "init_point_mp": init_point_mp,
        "facturas": facturas,
        "rutina": rutina_activa,
        "porcentaje_rutina": porcentaje_rutina,
        "gym_nombre": GYM_NOMBRE
    })


# ==========================================================
# WEBHOOK DE MERCADO PAGO
# ==========================================================
@app.post("/api/pagos/webhook")
async def mercadopago_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        data = await request.json()
    except Exception:
        data = {}

    query_params = dict(request.query_params)
    topic = data.get("type") or query_params.get("topic") or query_params.get("type")
    data_id = (data.get("data", {}) or {}).get("id") or query_params.get("id") or query_params.get("data.id")

    if topic in ["payment", "payment.created", "payment.updated"] and data_id:
        try:
            payment_info = mp_sdk.payment().get(data_id)
            resp = payment_info.get("response", {})
            status_pago = resp.get("status")

            if status_pago == "approved":
                ext_ref = resp.get("external_reference", "")
                monto = float(resp.get("transaction_amount", 0.0))
                payment_id_str = str(data_id)

                if ext_ref.startswith("KIOSCO-"):
                    parts = ext_ref.split("-")
                    producto_id = int(parts[1])
                    cantidad = int(parts[2])

                    venta_existente = db.query(models.VentaProducto).filter_by(metodo_pago=f"MP-{payment_id_str}").first()
                    if not venta_existente:
                        prod = db.query(models.Producto).get(producto_id)
                        if prod and prod.stock >= cantidad:
                            prod.stock -= cantidad
                            nueva_venta = models.VentaProducto(producto_id=prod.id, cantidad=cantidad, total=monto, metodo_pago=f"MP-{payment_id_str}")
                            db.add(nueva_venta)
                            db.commit()
                    return JSONResponse({"status": "kiosco_processed"})

                pago_existente = db.query(models.Pago).filter_by(external_payment_id=payment_id_str).first()
                if pago_existente:
                    return JSONResponse({"status": "already_processed"})

                if ext_ref.startswith("SOCIO-"):
                    socio_id = int(ext_ref.split("-")[1])
                    socio = db.query(models.Socio).get(socio_id)
                    if socio:
                        plan = socio.plan or db.query(models.Plan).filter(models.Plan.activo == True).first()
                        dias = plan.dias_duracion if plan else 30

                        hoy = date.today()
                        if socio.fecha_vencimiento_cuota and socio.fecha_vencimiento_cuota > hoy:
                            socio.fecha_vencimiento_cuota = socio.fecha_vencimiento_cuota + timedelta(days=dias)
                        else:
                            socio.fecha_vencimiento_cuota = hoy + timedelta(days=dias)

                        socio.estado_cuota = "ACTIVO"
                        concepto = f"Abono Online MP: {plan.nombre if plan else 'Cuota'}"
                        nuevo_pago = models.Pago(socio_id=socio.id, monto=monto, metodo_pago="MERCADOPAGO", concepto=concepto, external_payment_id=payment_id_str)
                        db.add(nuevo_pago)
                        db.commit()

                        background_tasks.add_task(procesar_factura_y_mail, socio.id, nuevo_pago.id, monto, concepto)
        except Exception as e:
            print(f"[WEBHOOK ERROR]: {e}")

    return JSONResponse({"status": "received"})


# ==========================================================
# MOLINETE Y VALIDACIÓN QR
# ==========================================================
@app.get("/molinete", response_class=HTMLResponse)
def molinete_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="molinete.html", context={"user": user, "gym_nombre": GYM_NOMBRE})


@app.post("/api/molinete/validar")
@limiter.limit("60/minute")
def validar_molinete(request: Request, token: str = Form(...), db: Session = Depends(get_db)):
    socio = db.query(models.Socio).filter(models.Socio.qr_token == token.strip()).first()
    hoy = date.today()

    if not socio:
        return JSONResponse({"abrir": False, "motivo": "QR no registrado", "color": "red", "foto": None})

    foto_url = socio.foto_base64

    if socio.bloqueado_manual:
        db.add(models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="BLOQUEADO_MANUAL"))
        db.commit()
        return JSONResponse({"abrir": False, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Bloqueado por Administración", "color": "red", "foto": foto_url})

    if socio.habilitacion_manual_hasta and socio.habilitacion_manual_hasta >= hoy:
        db.add(models.RegistroAcceso(socio_id=socio.id, resultado="PERMITIDO", motivo="PERMISO_ESPECIAL"))
        db.commit()
        return JSONResponse({"abrir": True, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Acceso Permitido (Permiso Especial)", "color": "green", "foto": foto_url})

    if socio.estado_cuota != "ACTIVO" or (socio.fecha_vencimiento_cuota and socio.fecha_vencimiento_cuota < hoy):
        db.add(models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="CUOTA_VENCIDA"))
        db.commit()
        return JSONResponse({"abrir": False, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Falta Regularizar Cuota", "color": "red", "foto": foto_url})

    if not socio.apto_medico_vencimiento or socio.apto_medico_vencimiento < hoy:
        db.add(models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="APTO_MEDICO_VENCIDO"))
        db.commit()
        return JSONResponse({"abrir": False, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Falta Regularizar Apto Médico", "color": "yellow", "foto": foto_url})

    db.add(models.RegistroAcceso(socio_id=socio.id, resultado="PERMITIDO", motivo="OK"))
    db.commit()
    return JSONResponse({"abrir": True, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Acceso Permitido", "color": "green", "foto": foto_url})