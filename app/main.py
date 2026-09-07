import os
import uuid
import qrcode
import io
import base64
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, Request, Form, HTTPException, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import engine, Base, get_db
from . import models, auth

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sistema de Gestión de Gimnasio")

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

        if db.query(models.Plan).count() == 0:
            db.add_all([
                models.Plan(nombre="Pase Libre Mensual", precio=25000.0, dias_duracion=30, descripcion="Acceso libre a sala de musculación y clases."),
                models.Plan(nombre="3 Veces por Semana", precio=18000.0, dias_duracion=30, descripcion="Hasta 3 accesos semanales.")
            ])
            db.commit()
    finally:
        db.close()

# --- AUTENTICACIÓN ---

@app.get("/", response_class=HTMLResponse)
def index(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user)):
    if not user:
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/dashboard")

@app.get("/login", response_class=HTMLResponse)
def login_view(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/login")
def login_action(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(models.UsuarioSistema).filter(models.UsuarioSistema.username == username.strip()).first()
    if not user or not auth.verify_password(password, user.password_hash) or not user.activo:
        return templates.TemplateResponse(request=request, name="login.html", context={"error": "Usuario o contraseña incorrectos."})

    token = auth.create_access_token(data={"sub": user.username, "rol": user.rol})
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(key="access_token", value=token, httponly=True, secure=True, samesite="lax")
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("access_token")
    return resp

# --- DASHBOARD ---

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login")

    total_socios = db.query(models.Socio).count()
    socios_activos = db.query(models.Socio).filter(models.Socio.estado_cuota == "ACTIVO").count()
    total_profes = db.query(models.Profesor).count()
    total_clases = db.query(models.Actividad).count()
    ultimos_accesos = db.query(models.RegistroAcceso).order_by(models.RegistroAcceso.fecha_hora.desc()).limit(10).all()

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "user": user,
        "total_socios": total_socios,
        "socios_activos": socios_activos,
        "total_profes": total_profes,
        "total_clases": total_clases,
        "ultimos_accesos": ultimos_accesos
    })

# --- GESTIÓN DE SOCIOS Y ASIGNACIÓN DE PLANES ---

@app.get("/socios", response_class=HTMLResponse)
def socios_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login")
    socios = db.query(models.Socio).order_by(models.Socio.id.desc()).all()
    planes = db.query(models.Plan).filter(models.Plan.activo == True).all()
    return templates.TemplateResponse(request=request, name="socios.html", context={
        "user": user,
        "socios": socios,
        "planes": planes
    })

@app.post("/socios/crear")
def crear_socio(
    dni: str = Form(...),
    nombre: str = Form(...),
    apellido: str = Form(...),
    fecha_nacimiento: str = Form(...),
    celular: str = Form(...),
    email: str = Form(...),
    plan_id: Optional[int] = Form(None),
    apto_medico_vencimiento: Optional[str] = Form(None),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    f_nac = datetime.strptime(fecha_nacimiento.strip(), "%Y-%m-%d").date()
    edad = calcular_edad(f_nac)
    f_apto = datetime.strptime(apto_medico_vencimiento.strip(), "%Y-%m-%d").date() if apto_medico_vencimiento and apto_medico_vencimiento.strip() else None
    token_qr = f"GYM-{dni.strip()}-{uuid.uuid4().hex[:8]}"

    nuevo_socio = models.Socio(
        dni=dni.strip(),
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        fecha_nacimiento=f_nac,
        edad=edad,
        celular=celular.strip(),
        email=email.strip().lower(),
        plan_id=plan_id,
        apto_medico_vencimiento=f_apto,
        qr_token=token_qr,
        estado_cuota="ACTIVO",
        fecha_vencimiento_cuota=date.today() + timedelta(days=30),
        bloqueado_manual=False
    )
    db.add(nuevo_socio)
    db.commit()
    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)

@app.post("/socios/asignar-plan/{socio_id}")
def asignar_plan_socio(
    socio_id: int,
    plan_id: int = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
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
        "qr_image": f"data:image/png;base64,{qr_b64}"
    })

@app.post("/socios/toggle-bloqueo/{socio_id}")
def toggle_bloqueo_socio(socio_id: int, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401)
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)
    socio.bloqueado_manual = not socio.bloqueado_manual
    db.commit()
    return RedirectResponse(url="/socios", status_code=status.HTTP_302_FOUND)

# --- MÓDULO PROFESORES Y CLASES ---

@app.get("/clases-profesores", response_class=HTMLResponse)
def clases_profesores_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login")
    profesores = db.query(models.Profesor).order_by(models.Profesor.id.desc()).all()
    actividades = db.query(models.Actividad).order_by(models.Actividad.id.desc()).all()
    return templates.TemplateResponse(request=request, name="clases_profesores.html", context={
        "user": user,
        "profesores": profesores,
        "actividades": actividades
    })

@app.post("/profesores/crear")
def crear_profesor(
    nombre: str = Form(...),
    apellido: str = Form(...),
    dni: str = Form(...),
    celular: str = Form(...),
    especialidad: str = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")
    profe = models.Profesor(
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        dni=dni.strip(),
        celular=celular.strip(),
        especialidad=especialidad.strip()
    )
    db.add(profe)
    db.commit()
    return RedirectResponse(url="/clases-profesores", status_code=status.HTTP_302_FOUND)

@app.post("/clases/crear")
def crear_clase(
    nombre: str = Form(...),
    dias: str = Form(...),
    horario: str = Form(...),
    cupo_maximo: int = Form(20),
    profesor_id: Optional[int] = Form(None),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")
    clase = models.Actividad(
        nombre=nombre.strip(),
        dias=dias.strip(),
        horario=horario.strip(),
        cupo_maximo=cupo_maximo,
        profesor_id=profesor_id
    )
    db.add(clase)
    db.commit()
    return RedirectResponse(url="/clases-profesores", status_code=status.HTTP_302_FOUND)

# --- MÓDULO PLANES Y PAGOS (CAJA) ---

@app.get("/caja-pagos", response_class=HTMLResponse)
def pagos_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login")
    pagos = db.query(models.Pago).order_by(models.Pago.id.desc()).limit(25).all()
    socios = db.query(models.Socio).all()
    planes = db.query(models.Plan).all()
    return templates.TemplateResponse(request=request, name="caja_pagos.html", context={
        "user": user,
        "pagos": pagos,
        "socios": socios,
        "planes": planes
    })

@app.post("/pagos/registrar")
def registrar_pago(
    socio_id: int = Form(...),
    plan_id: int = Form(...),
    metodo_pago: str = Form(...),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")
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

    nuevo_pago = models.Pago(
        socio_id=socio.id,
        monto=plan.precio,
        metodo_pago=metodo_pago,
        concepto=f"Pago: {plan.nombre}"
    )
    db.add(nuevo_pago)
    db.commit()
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
        return RedirectResponse(url="/login")
    p = models.Plan(nombre=nombre.strip(), precio=precio, dias_duracion=dias_duracion, descripcion=descripcion.strip())
    db.add(p)
    db.commit()
    return RedirectResponse(url="/caja-pagos", status_code=status.HTTP_302_FOUND)

# --- MÓDULO PRODUCTOS / KIOSCO ---

@app.get("/kiosco", response_class=HTMLResponse)
def kiosco_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/login")
    productos = db.query(models.Producto).filter(models.Producto.activo == True).order_by(models.Producto.nombre.asc()).all()
    ventas = db.query(models.VentaProducto).order_by(models.VentaProducto.id.desc()).limit(15).all()
    return templates.TemplateResponse(request=request, name="kiosco.html", context={
        "user": user,
        "productos": productos,
        "ventas": ventas
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
        return RedirectResponse(url="/login")
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
        return RedirectResponse(url="/login")
    prod = db.query(models.Producto).get(producto_id)
    if not prod or prod.stock < cantidad:
        return RedirectResponse(url="/kiosco?error=stock_insuficiente", status_code=status.HTTP_302_FOUND)

    prod.stock -= cantidad
    total = prod.precio_venta * cantidad
    venta = models.VentaProducto(producto_id=prod.id, cantidad=cantidad, total=total, metodo_pago=metodo_pago)
    db.add(venta)
    db.commit()
    return RedirectResponse(url="/kiosco", status_code=status.HTTP_302_FOUND)

# --- PORTAL DEL SOCIO (LOGIN DNI + CORREO) ---

@app.get("/socio/login", response_class=HTMLResponse)
def socio_login_view(request: Request):
    return templates.TemplateResponse(request=request, name="socio_login.html", context={"error": None})

@app.post("/socio/login")
def socio_login_action(request: Request, dni: str = Form(...), email: str = Form(...), db: Session = Depends(get_db)):
    socio = db.query(models.Socio).filter(
        models.Socio.dni == dni.strip(),
        models.Socio.email == email.strip().lower()
    ).first()

    if not socio:
        return templates.TemplateResponse(request=request, name="socio_login.html", context={"error": "Datos no registrados"})

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

    plan_asignado = socio.plan
    if not plan_asignado:
        # Si aún no tiene plan, toma el primer plan activo como referencia
        plan_asignado = db.query(models.Plan).filter(models.Plan.activo == True).first()

    return templates.TemplateResponse(request=request, name="socio_credencial.html", context={
        "socio": socio,
        "plan": plan_asignado,
        "habilitado": habilitado,
        "cuota_al_dia": cuota_al_dia,
        "apto_al_dia": apto_al_dia,
        "qr_image": f"data:image/png;base64,{qr_b64}"
    })

# --- PAGO DE CUOTA ONLINE POR EL SOCIO ---

@app.post("/socio/pagar-cuota/{socio_id}")
def socio_pagar_cuota_online(
    socio_id: int,
    metodo: str = Form("MERCADOPAGO"),
    db: Session = Depends(get_db)
):
    socio = db.query(models.Socio).get(socio_id)
    if not socio:
        raise HTTPException(status_code=404)

    plan = socio.plan or db.query(models.Plan).filter(models.Plan.activo == True).first()
    if not plan:
        raise HTTPException(status_code=400, detail="No hay plan definido para este socio.")

    # Registro de la transacción y renovación automática
    hoy = date.today()
    if socio.fecha_vencimiento_cuota and socio.fecha_vencimiento_cuota > hoy:
        socio.fecha_vencimiento_cuota = socio.fecha_vencimiento_cuota + timedelta(days=plan.dias_duracion)
    else:
        socio.fecha_vencimiento_cuota = hoy + timedelta(days=plan.dias_duracion)

    socio.estado_cuota = "ACTIVO"
    socio.plan_id = plan.id

    nuevo_pago = models.Pago(
        socio_id=socio.id,
        monto=plan.precio,
        metodo_pago=metodo,
        concepto=f"Abono Online: {plan.nombre}"
    )
    db.add(nuevo_pago)
    db.commit()

    return RedirectResponse(url=f"/socio/credencial/{socio.id}?pago_exitoso=1", status_code=status.HTTP_302_FOUND)

# --- SIMULADOR DE MOLINETE ---

@app.get("/molinete", response_class=HTMLResponse)
def molinete_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user)):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request=request, name="molinete.html", context={"user": user})

@app.post("/api/molinete/validar")
def validar_molinete(token: str = Form(...), db: Session = Depends(get_db)):
    socio = db.query(models.Socio).filter(models.Socio.qr_token == token.strip()).first()
    hoy = date.today()

    if not socio:
        return JSONResponse({"abrir": False, "motivo": "QR no registrado", "color": "red"})

    if socio.bloqueado_manual:
        db.add(models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="BLOQUEADO_MANUAL"))
        db.commit()
        return JSONResponse({"abrir": False, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Bloqueado por Administración", "color": "red"})

    if socio.estado_cuota != "ACTIVO" or (socio.fecha_vencimiento_cuota and socio.fecha_vencimiento_cuota < hoy):
        db.add(models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="CUOTA_VENCIDA"))
        db.commit()
        return JSONResponse({"abrir": False, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Cuota Vencida", "color": "red"})

    if socio.apto_medico_vencimiento and socio.apto_medico_vencimiento < hoy:
        db.add(models.RegistroAcceso(socio_id=socio.id, resultado="DENEGADO", motivo="APTO_MEDICO_VENCIDO"))
        db.commit()
        return JSONResponse({"abrir": False, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Apto Médico Vencido", "color": "yellow"})

    db.add(models.RegistroAcceso(socio_id=socio.id, resultado="PERMITIDO", motivo="OK"))
    db.commit()
    return JSONResponse({"abrir": True, "socio": f"{socio.nombre} {socio.apellido}", "motivo": "Acceso Permitido", "color": "green"})