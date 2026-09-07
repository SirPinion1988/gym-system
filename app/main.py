import os
import uuid
import qrcode
import io
import csv
import base64
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, Request, Form, HTTPException, status, Response, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

import mercadopago

from .database import engine, Base, get_db
from . import models, auth, billing

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sistema de Gestión de Gimnasio - GymPro")

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "TEST-0000000000000000-000000-00000000000000000000000000000000-000000000")
mp_sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "https://gimnasio-app-0qhn.onrender.com")

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
        else:
            admin.rol = "ADMIN"
            admin.activo = True
            db.commit()

        if db.query(models.Plan).count() == 0:
            db.add_all([
                models.Plan(nombre="Pase Libre Mensual", precio=25000.0, dias_duracion=30, descripcion="Acceso libre a musculación y clases."),
                models.Plan(nombre="3 Veces por Semana", precio=18000.0, dias_duracion=30, descripcion="Hasta 3 ingresos semanales.")
            ])
            db.commit()
    except Exception as e:
        print(f"Alerta startup: {e}")
    finally:
        db.close()


def procesar_factura_y_mail(socio_id: int, pago_id: int, monto: float, concepto: str):
    from .database import SessionLocal
    db = SessionLocal()
    try:
        socio = db.query(models.Socio).get(socio_id)
        pago = db.query(models.Pago).get(pago_id)
        if not socio or not pago:
            return

        arca_data = billing.emitir_factura_arca(
            socio_nombre=f"{socio.nombre} {socio.apellido}",
            socio_dni=socio.dni,
            monto=monto,
            concepto=concepto
        )

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

        pdf_bytes = billing.generar_pdf_factura(
            socio_nombre=f"{socio.nombre} {socio.apellido}",
            socio_dni=socio.dni,
            socio_email=socio.email,
            factura_data=arca_data,
            concepto=concepto
        )

        nro_fmt = f"{arca_data['punto_venta']:04d}-{arca_data['numero_comprobante']:08d}"
        enviado = billing.enviar_correo_factura(
            destinatario=socio.email,
            nombre_socio=socio.nombre,
            pdf_bytes=pdf_bytes,
            nro_factura=nro_fmt
        )
        if enviado:
            factura.enviada_por_mail = True
            db.commit()
    except Exception as e:
        print(f"Error facturacion/email: {e}")
    finally:
        db.close()


# --- RUTAS DE LOGIN Y PORTAL INICIAL ---

@app.get("/", response_class=HTMLResponse)
def index_hub(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user)):
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="login_hub.html", context={"error_admin": None, "error_socio": None})


@app.get("/login", response_class=HTMLResponse)
def login_view(request: Request):
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.post("/login/admin")
def login_admin_action(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(models.UsuarioSistema).filter(models.UsuarioSistema.username == username.strip()).first()
    if not user or not auth.verify_password(password, user.password_hash) or not user.activo:
        return templates.TemplateResponse(request=request, name="login_hub.html", context={"error_admin": "Credenciales inválidas o cuenta inactiva.", "error_socio": None})

    token = auth.create_access_token(data={"sub": user.username, "rol": user.rol})
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(key="access_token", value=token, httponly=True, secure=True, samesite="lax")
    return resp


@app.post("/login/socio")
def login_socio_action(
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
        return templates.TemplateResponse(request=request, name="login_hub.html", context={"error_socio": "DNI o correo no coinciden con nuestros registros.", "error_admin": None})

    return RedirectResponse(url=f"/socio/credencial/{socio.id}", status_code=status.HTTP_302_FOUND)


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie("access_token")
    return resp


# --- USUARIOS DEL SISTEMA ---

@app.get("/usuarios", response_class=HTMLResponse)
def usuarios_sistema_view(
    request: Request,
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    if user.rol != "ADMIN":
        raise HTTPException(status_code=403, detail="Acceso denegado.")

    usuarios = db.query(models.UsuarioSistema).order_by(models.UsuarioSistema.id.asc()).all()
    return templates.TemplateResponse(request=request, name="usuarios.html", context={"user": user, "usuarios": usuarios})


@app.post("/usuarios/crear")
def crear_usuario_sistema(
    nombre: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    rol: str = Form("OPERADOR"),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user or user.rol != "ADMIN":
        raise HTTPException(status_code=403, detail="Acceso denegado.")

    existe = db.query(models.UsuarioSistema).filter_by(username=username.strip()).first()
    if existe:
        raise HTTPException(status_code=400, detail="El nombre de usuario ya está en uso.")

    nuevo_usuario = models.UsuarioSistema(
        nombre=nombre.strip(),
        username=username.strip(),
        password_hash=auth.hash_password(password),
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
    if not user or user.rol != "ADMIN":
        raise HTTPException(status_code=403)
    target = db.query(models.UsuarioSistema).get(usuario_id)
    if not target or target.id == user.id:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta.")

    target.activo = not target.activo
    db.commit()
    return RedirectResponse(url="/usuarios", status_code=status.HTTP_302_FOUND)


# --- DASHBOARD ---

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
        print(f"Error dashboard: {e}")
        total_socios, socios_activos, total_profes, total_clases, ultimos_accesos = 0, 0, 0, 0, []

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "total_socios": total_socios,
            "socios_activos": socios_activos,
            "total_profes": total_profes,
            "total_clases": total_clases,
            "ultimos_accesos": ultimos_accesos
        }
    )


# --- MÓDULO SOCIOS ---

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
        "hoy": hoy
    })


@app.post("/socios/crear")
async def crear_socio(
    dni: str = Form(...),
    nombre: str = Form(...),
    apellido: str = Form(...),
    fecha_nacimiento: str = Form(...),
    celular: str = Form(...),
    email: str = Form(...),
    plan_id: Optional[int] = Form(None),
    apto_medico_realizacion: Optional[str] = Form(None),
    foto: Optional[UploadFile] = File(None),
    user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    f_nac = datetime.strptime(fecha_nacimiento.strip(), "%Y-%m-%d").date()
    edad = calcular_edad(f_nac)
    
    f_realizacion = None
    f_vto_apto = None
    if apto_medico_realizacion and apto_medico_realizacion.strip():
        f_realizacion = datetime.strptime(apto_medico_realizacion.strip(), "%Y-%m-%d").date()
        f_vto_apto = sumar_un_anio(f_realizacion)

    token_qr = f"GYM-{dni.strip()}-{uuid.uuid4().hex[:8]}"

    foto_b64 = None
    if foto and foto.filename:
        contenido = await foto.read()
        if contenido:
            tipo_mime = foto.content_type or "image/jpeg"
            b64_str = base64.b64encode(contenido).decode('utf-8')
            foto_b64 = f"data:{tipo_mime};base64,{b64_str}"

    nuevo_socio = models.Socio(
        dni=dni.strip(),
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        fecha_nacimiento=f_nac,
        edad=edad,
        celular=celular.strip(),
        email=email.strip().lower(),
        plan_id=plan_id,
        foto_base64=foto_b64,
        apto_medico_realizacion=f_realizacion,
        apto_medico_vencimiento=f_vto_apto,
        qr_token=token_qr,
        estado_cuota="ACTIVO",
        fecha_vencimiento_cuota=date.today() + timedelta(days=30),
        bloqueado_manual=False
    )
    db.add(nuevo_socio)
    db.commit()
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
    socio.dni = dni.strip()
    
    f_nac = datetime.strptime(fecha_nacimiento.strip(), "%Y-%m-%d").date()
    socio.fecha_nacimiento = f_nac
    socio.edad = calcular_edad(f_nac)
    socio.celular = celular.strip()
    socio.email = email.strip().lower()
    socio.plan_id = plan_id

    if foto and foto.filename:
        contenido = await foto.read()
        if contenido:
            tipo_mime = foto.content_type or "image/jpeg"
            b64_str = base64.b64encode(contenido).decode('utf-8')
            socio.foto_base64 = f"data:{tipo_mime};base64,{b64_str}"

    if apto_medico_realizacion and apto_medico_realizacion.strip():
        f_realiz = datetime.strptime(apto_medico_realizacion.strip(), "%Y-%m-%d").date()
        socio.apto_medico_realizacion = f_realiz
        socio.apto_medico_vencimiento = sumar_un_anio(f_realiz)
    else:
        socio.apto_medico_realizacion = None
        socio.apto_medico_vencimiento = None

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


# --- CLASES Y PROFESORES ---

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
    clase = models.Actividad(
        nombre=nombre.strip(),
        dias=dias.strip(),
        horario=horario.strip(),
        cupo_maximo=cupo_maximo,
        profesor_id=pid
    )
    db.add(clase)
    db.commit()
    return RedirectResponse(url="/clases-profesores", status_code=status.HTTP_302_FOUND)


# --- MÓDULO CAJA Y CUOTAS (BLINDADO CON JOINEDLOAD Y PROTECCIÓN CONTRA ERRORES) ---

@app.get("/caja-pagos", response_class=HTMLResponse)
def pagos_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    
    try:
        pagos = db.query(models.Pago).options(joinedload(models.Pago.socio)).order_by(models.Pago.id.desc()).limit(30).all() or []
    except Exception as e:
        print(f"Error cargando pagos: {e}")
        pagos = []

    try:
        socios = db.query(models.Socio).order_by(models.Socio.apellido.asc()).all() or []
    except Exception as e:
        print(f"Error cargando socios: {e}")
        socios = []

    try:
        planes = db.query(models.Plan).order_by(models.Plan.id.asc()).all() or []
    except Exception as e:
        print(f"Error cargando planes: {e}")
        planes = []

    return templates.TemplateResponse(request=request, name="caja_pagos.html", context={
        "user": user,
        "pagos": pagos,
        "socios": socios,
        "planes": planes
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
    nuevo_pago = models.Pago(
        socio_id=socio.id,
        monto=plan.precio,
        metodo_pago=metodo_pago,
        concepto=concepto
    )
    db.add(nuevo_pago)
    db.commit()

    background_tasks.add_task(procesar_factura_y_mail, socio.id, nuevo_pago.id, plan.precio, concepto)

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


# --- MÓDULO KIOSCO (PÁGINA EXCLUSIVA) ---

@app.get("/kiosco", response_class=HTMLResponse)
def kiosco_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    
    try:
        productos = db.query(models.Producto).filter(models.Producto.activo == True).order_by(models.Producto.nombre.asc()).all() or []
    except Exception as e:
        print(f"Error cargando productos: {e}")
        productos = []

    try:
        ventas = db.query(models.VentaProducto).options(joinedload(models.VentaProducto.producto)).order_by(models.VentaProducto.id.desc()).limit(20).all() or []
    except Exception as e:
        print(f"Error cargando ventas: {e}")
        ventas = []

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


# --- BALANCE Y FINANZAS PROFESIONAL ---

@app.get("/balance", response_class=HTMLResponse)
def balance_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    hoy = date.today()
    primer_dia_mes = date(hoy.year, hoy.month, 1)
    primer_dia_anio = date(hoy.year, 1, 1)

    cuotas_hoy = db.query(func.coalesce(func.sum(models.Pago.monto), 0.0)).filter(func.date(models.Pago.fecha_pago) == hoy).scalar()
    cuotas_mes = db.query(func.coalesce(func.sum(models.Pago.monto), 0.0)).filter(models.Pago.fecha_pago >= primer_dia_mes).scalar()
    cuotas_anio = db.query(func.coalesce(func.sum(models.Pago.monto), 0.0)).filter(models.Pago.fecha_pago >= primer_dia_anio).scalar()

    kiosco_hoy = db.query(func.coalesce(func.sum(models.VentaProducto.total), 0.0)).filter(func.date(models.VentaProducto.fecha) == hoy).scalar()
    kiosco_mes = db.query(func.coalesce(func.sum(models.VentaProducto.total), 0.0)).filter(models.VentaProducto.fecha >= primer_dia_mes).scalar()
    kiosco_anio = db.query(func.coalesce(func.sum(models.VentaProducto.total), 0.0)).filter(models.VentaProducto.fecha >= primer_dia_anio).scalar()

    total_ingresos_hoy = cuotas_hoy + kiosco_hoy
    total_ingresos_mes = cuotas_mes + kiosco_mes
    total_ingresos_anio = cuotas_anio + kiosco_anio

    egreso_sueldos_mensual = db.query(func.coalesce(func.sum(models.Profesor.sueldo), 0.0)).filter(models.Profesor.activo == True).scalar()
    meses_transcurridos = hoy.month
    egreso_sueldos_anual = egreso_sueldos_mensual * meses_transcurridos

    ganancia_neta_mes = total_ingresos_mes - egreso_sueldos_mensual
    ganancia_neta_anio = total_ingresos_anio - egreso_sueldos_anual

    limite_proximo = hoy + timedelta(days=7)
    vencidos = db.query(models.Socio).filter(models.Socio.fecha_vencimiento_cuota < hoy).order_by(models.Socio.fecha_vencimiento_cuota.asc()).all()
    proximos_vencer = db.query(models.Socio).filter(
        models.Socio.fecha_vencimiento_cuota >= hoy,
        models.Socio.fecha_vencimiento_cuota <= limite_proximo
    ).order_by(models.Socio.fecha_vencimiento_cuota.asc()).all()

    ultimos_pagos_cuotas = db.query(models.Pago).order_by(models.Pago.id.desc()).limit(10).all()
    ultimas_ventas_kiosco = db.query(models.VentaProducto).order_by(models.VentaProducto.id.desc()).limit(10).all()

    return templates.TemplateResponse(request=request, name="balance.html", context={
        "user": user,
        "cuotas_hoy": cuotas_hoy,
        "kiosco_hoy": kiosco_hoy,
        "total_ingresos_hoy": total_ingresos_hoy,
        "total_ingresos_mes": total_ingresos_mes,
        "total_ingresos_anio": total_ingresos_anio,
        "egreso_sueldos_mensual": egreso_sueldos_mensual,
        "ganancia_neta_mes": ganancia_neta_mes,
        "ganancia_neta_anio": ganancia_neta_anio,
        "vencidos": vencidos,
        "proximos_vencer": proximos_vencer,
        "ultimos_pagos_cuotas": ultimos_pagos_cuotas,
        "ultimas_ventas_kiosco": ultimas_ventas_kiosco
    })


# --- EXPORTACIONES CSV ---

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


# --- FACTURA ARCA DESCARGA ---

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
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Factura_ARCA_{nro_fmt}.pdf"}
    )


# --- CREDENCIAL DIGITAL ---

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
                "items": [
                    {
                        "title": f"Abono {plan_asignado.nombre} - Socio {socio.nombre} {socio.apellido}",
                        "quantity": 1,
                        "unit_price": float(plan_asignado.precio),
                        "currency_id": "ARS"
                    }
                ],
                "payer": {
                    "email": socio.email,
                    "name": socio.nombre,
                    "surname": socio.apellido,
                    "identification": {"type": "DNI", "number": socio.dni}
                },
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

    ultima_factura = db.query(models.Factura).filter(models.Factura.socio_id == socio.id).order_by(models.Factura.id.desc()).first()

    return templates.TemplateResponse(request=request, name="socio_credencial.html", context={
        "socio": socio,
        "plan": plan_asignado,
        "habilitado": habilitado,
        "cuota_al_dia": cuota_al_dia,
        "apto_al_dia": apto_al_dia,
        "permiso_especial": permiso_especial,
        "qr_image": f"data:image/png;base64,{qr_b64}",
        "init_point_mp": init_point_mp,
        "ultima_factura": ultima_factura
    })


# --- WEBHOOK MP ---

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

                        nuevo_pago = models.Pago(
                            socio_id=socio.id,
                            monto=monto,
                            metodo_pago="MERCADOPAGO",
                            concepto=concepto,
                            external_payment_id=payment_id_str
                        )
                        db.add(nuevo_pago)
                        db.commit()

                        background_tasks.add_task(procesar_factura_y_mail, socio.id, nuevo_pago.id, monto, concepto)
        except Exception as e:
            print(f"Error Webhook MP: {e}")

    return JSONResponse({"status": "received"})


# --- SIMULADOR MOLINETE ---

@app.get("/molinete", response_class=HTMLResponse)
def molinete_view(request: Request, user: Optional[models.UsuarioSistema] = Depends(auth.get_current_user)):
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="molinete.html", context={"user": user})


@app.post("/api/molinete/validar")
def validar_molinete(token: str = Form(...), db: Session = Depends(get_db)):
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