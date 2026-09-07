import os
import uuid
import qrcode
import io
import base64
from pathlib import Path
from datetime import date, datetime, timedelta
from fastapi import FastAPI, Depends, Request, Form, HTTPException, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import engine, Base, get_db
from . import models, auth

# Crea las tablas si no existen
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sistema de Gestión de Gimnasio")

# --- DETECCIÓN INTELIGENTE DE DIRECTORIOS (LINUX FRIENDLY) ---
CURRENT_FILE = Path(__file__).resolve()
APP_DIR = CURRENT_FILE.parent
ROOT_DIR = APP_DIR.parent

# Buscar posibles ubicaciones de templates
POSSIBLE_TEMPLATE_DIRS = [
    ROOT_DIR / "templates",
    APP_DIR / "templates",
    ROOT_DIR / "Templates",
    APP_DIR / "Templates",
]

TEMPLATE_DIR = None
for p in POSSIBLE_TEMPLATE_DIRS:
    if p.exists() and p.is_dir():
        TEMPLATE_DIR = p
        break

if not TEMPLATE_DIR:
    # Si no existe ninguna, usamos por defecto ROOT_DIR / "templates"
    TEMPLATE_DIR = ROOT_DIR / "templates"
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

print(f"--> [DEBUG] Directorio de templates seleccionado: {TEMPLATE_DIR}")
if TEMPLATE_DIR.exists():
    print(f"--> [DEBUG] Archivos en templates: {[f.name for f in TEMPLATE_DIR.iterdir()]}")

# Buscar posibles ubicaciones de static
POSSIBLE_STATIC_DIRS = [
    ROOT_DIR / "static",
    APP_DIR / "static",
]
STATIC_DIR = ROOT_DIR / "static"
for p in POSSIBLE_STATIC_DIRS:
    if p.exists() and p.is_dir():
        STATIC_DIR = p
        break

STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def calcular_edad(fecha_nac: date) -> int:
    hoy = date.today()
    return hoy.year - fecha_nac.year - ((hoy.month, hoy.day) < (fecha_nac.month, fecha_nac.day))


def generar_qr_base64(texto: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(texto)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@app.on_event("startup")
def startup_db_init():
    from .database import SessionLocal
    db = SessionLocal()
    try:
        admin = db.query(models.UsuarioSistema).filter_by(username="admin").first()
        if not admin:
            admin_user = models.UsuarioSistema(
                username="admin",
                password_hash=auth.hash_password("admin123"),
                nombre="Administrador General",
                rol="ADMIN",
                activo=True
            )
            db.add(admin_user)
            db.commit()
    finally:
        db.close()


# --- RUTAS DE ADMINISTRACIÓN / OPERADOR ---

@app.get("/", response_class=HTMLResponse)
def index(request: Request, user: models.UsuarioSistema = Depends(auth.get_current_user)):
    if not user:
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/dashboard")


@app.get("/login", response_class=HTMLResponse)
def login_view(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None}
    )

@app.post("/login")
def login_action(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(models.UsuarioSistema).filter(models.UsuarioSistema.username == username).first()
    if not user or not auth.verify_password(password, user.password_hash) or not user.activo:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Credenciales inválidas o usuario inactivo"}
        )

    token = auth.create_access_token(data={"sub": user.username, "rol": user.rol})
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(key="access_token", value=token, httponly=True)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("access_token")
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: models.UsuarioSistema = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login")

    total_socios = db.query(models.Socio).count()
    socios_activos = db.query(models.Socio).filter(models.Socio.estado_cuota == "ACTIVO").count()
    total_actividades = db.query(models.Actividad).count()
    ultimos_accesos = db.query(models.RegistroAcceso).order_by(models.RegistroAcceso.fecha_hora.desc()).limit(10).all()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "total_socios": total_socios,
        "socios_activos": socios_activos,
        "total_actividades": total_actividades,
        "ultimos_accesos": ultimos_accesos
    })


# --- GESTIÓN DE SOCIOS ---

@app.get("/socios", response_class=HTMLResponse)
def socios_view(request: Request, user: models.UsuarioSistema = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login")
    socios = db.query(models.Socio).order_by(models.Socio.id.desc()).all()
    return templates.TemplateResponse("socios.html", {"request": request, "user": user, "socios": socios})


@app.post("/socios/crear")
def crear_socio(
    dni: str = Form(...),
    nombre: str = Form(...),
    apellido: str = Form(...),
    fecha_nacimiento: str = Form(...),
    celular: str = Form(...),
    email: str = Form(...),
    apto_medico_vencimiento: str = Form(None),
    user: models.UsuarioSistema = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    f_nac = datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date()
    edad = calcular_edad(f_nac)
    f_apto = datetime.strptime(apto_medico_vencimiento, "%Y-%m-%d").date() if apto_medico_vencimiento else None

    token_qr = f"GYM-{dni.strip()}-{uuid.uuid4().hex[:8]}"

    nuevo_socio = models.Socio(
        dni=dni.strip(),
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        fecha_nacimiento=f_nac,
        edad=edad,
        celular=celular.strip(),
        email=email.strip().lower(),
        apto_medico_vencimiento=f_apto,
        qr_token=token_qr,
        estado_cuota="ACTIVO",
        fecha_vencimiento_cuota=date.today() + timedelta(days=30),
        bloqueado_manual=False
    )
    db.add(nuevo_socio)
    db.commit()
    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)


@app.get("/socios/qr/{socio_id}")
def ver_qr_socio(socio_id: int, user: models.UsuarioSistema = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404, detail="Socio no encontrado")

    qr_b64 = generar_qr_base64(socio.qr_token)
    return JSONResponse({
        "socio": f"{socio.nombre} {socio.apellido}",
        "dni": socio.dni,
        "token": socio.qr_token,
        "qr_image": f"data:image/png;base64,{qr_b64}"
    })


@app.post("/socios/toggle-bloqueo/{socio_id}")
def toggle_bloqueo_socio(
    socio_id: int,
    user: models.UsuarioSistema = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        raise HTTPException(status_code=401)

    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)

    socio.bloqueado_manual = not socio.bloqueado_manual
    db.commit()
    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)


# --- PORTAL PÚBLICO DEL SOCIO (DNI + MAIL) ---

@app.get("/socio/login", response_class=HTMLResponse)
def socio_login_view(request: Request):
    return templates.TemplateResponse("socio_login.html", {"request": request, "error": None})


@app.post("/socio/login")
def socio_login_action(
    request: Request,
    dni: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    socio = db.query(models.Socio).filter(
        models.Socio.dni == dni.strip(),
        models.Socio.email == email.strip().lower()
    ).first()

    if not socio:
        return templates.TemplateResponse("socio_login.html", {
            "request": request,
            "error": "Los datos no coinciden con ningún socio registrado."
        })

    return RedirectResponse(url=f"/socio/credencial/{socio.id}", status_code=status.HTTP_302_FOUND)


@app.get("/socio/credencial/{socio_id}", response_class=HTMLResponse)
def socio_credencial_view(socio_id: int, request: Request, db: Session = Depends(get_db)):
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        return RedirectResponse(url="/socio/login")

    hoy = date.today()
    cuota_al_dia = (socio.estado_cuota == "ACTIVO") and (not socio.fecha_vencimiento_cuota or socio.fecha_vencimiento_cuota >= hoy)
    apto_al_dia = not socio.apto_medico_vencimiento or socio.apto_medico_vencimiento >= hoy
    habilitado = cuota_al_dia and apto_al_dia and not socio.bloqueado_manual

    qr_b64 = generar_qr_base64(socio.qr_token)

    return templates.TemplateResponse("socio_credencial.html", {
        "request": request,
        "socio": socio,
        "habilitado": habilitado,
        "cuota_al_dia": cuota_al_dia,
        "apto_al_dia": apto_al_dia,
        "qr_image": f"data:image/png;base64,{qr_b64}"
    })


# --- VALIDACIÓN DEL MOLINETE ---

@app.get("/molinete", response_class=HTMLResponse)
def molinete_view(request: Request, user: models.UsuarioSistema = Depends(auth.get_current_user)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("molinete.html", {"request": request, "user": user})


@app.post("/api/molinete/validar")
def validar_molinete(token: str = Form(...), db: Session = Depends(get_db)):
    socio = db.query(models.Socio).filter(models.Socio.qr_token == token.strip()).first()
    hoy = date.today()

    if not socio:
        return JSONResponse({"abrir": False, "motivo": "QR no registrado", "color": "red"})

    if socio.bloqueado_manual:
        log = models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="BLOQUEO_ADMINISTRATIVO")
        db.add(log)
        db.commit()
        return JSONResponse({
            "abrir": False,
            "socio": f"{socio.nombre} {socio.apellido}",
            "motivo": "Acceso Bloqueado por Administración",
            "color": "red"
        })

    if socio.estado_cuota != "ACTIVO" or (socio.fecha_vencimiento_cuota and socio.fecha_vencimiento_cuota < hoy):
        log = models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="CUOTA_VENCIDA")
        db.add(log)
        db.commit()
        return JSONResponse({
            "abrir": False,
            "socio": f"{socio.nombre} {socio.apellido}",
            "motivo": "Cuota Vencida o Inactiva",
            "color": "red"
        })

    if socio.apto_medico_vencimiento and socio.apto_medico_vencimiento < hoy:
        log = models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="APTO_MEDICO_VENCIDO")
        db.add(log)
        db.commit()
        return JSONResponse({
            "abrir": False,
            "socio": f"{socio.nombre} {socio.apellido}",
            "motivo": "Apto Médico Vencido",
            "color": "yellow"
        })

    log = models.RegistroAcceso(socio_id=socio.id, resultado="PERMITIDO", motivo="OK")
    db.add(log)
    db.commit()
    return JSONResponse({
        "abrir": True,
        "socio": f"{socio.nombre} {socio.apellido}",
        "motivo": "Acceso Permitido",
        "color": "green"
    })


# --- INTEGRACIÓN MERCADO PAGO ---

@app.post("/api/pagos/crear-preferencia/{socio_id}")
def crear_pago_mercadopago(socio_id: int, db: Session = Depends(get_db)):
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)

    return {
        "status": "ready_for_mercadopago",
        "init_point": f"https://sandbox.mercadopago.com/checkout/mock-payment-{socio.id}",
        "mensaje": "Endpoint preparado para vincular con MP_ACCESS_TOKEN"
    }