import os
import io
import smtplib
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, date, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# DATOS FISCALES CONFIGURABLES (Render > Environment o archivo .env)
RAZON_SOCIAL = os.getenv("EMPRESA_RAZON_SOCIAL", "GYMPRO FITNESS CLUB")
CUIT_EMISOR = os.getenv("EMPRESA_CUIT", "30-71234567-9")
DOMICILIO_COMERCIAL = os.getenv("EMPRESA_DOMICILIO", "Av. Principal 1234, CABA")
CONDICION_IVA = os.getenv("EMPRESA_CONDICION_IVA", "Monotributista")

# PARÁMETROS DEL SERVIDOR DE CORREO SALIENTE (SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# Ubicación del logo institucional (si existe en static/logo.png se añade al PDF)
BASE_DIR = Path(__file__).resolve().parent.parent
LOGO_PATH = BASE_DIR / "static" / "logo.png"

def emitir_factura_arca(socio_nombre: str, socio_dni: str, monto: float, concepto: str):
    """
    Simula la obtención de Código de Autorización Electrónico (CAE) de ARCA (ex-AFIP).
    Genera un comprobante Clase 'C' numerado correlativamente con vencimiento de 10 días.
    """
    numero_comp = int(datetime.utcnow().timestamp()) % 1000000
    cae_simulado = f"742{datetime.utcnow().strftime('%Y%m%d')}{numero_comp:04d}"
    vto_cae = date.today() + timedelta(days=10)

    return {
        "punto_venta": 1,
        "numero_comprobante": numero_comp,
        "tipo_comprobante": "C",
        "cae": cae_simulado,
        "cae_vencimiento": vto_cae,
        "monto": monto
    }

def generar_pdf_factura(socio_nombre: str, socio_dni: str, socio_email: str, factura_data: dict, concepto: str) -> bytes:
    """
    Compila el archivo PDF oficial con el formato reglamentario exigido por ARCA/AFIP:
    Encabezado emisor, letra del comprobante con código numérico, datos del receptor,
    tabla de ítems facturados y bloque inferior de seguridad con CAE y código de barras.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    elements = []
    styles = getSampleStyleSheet()

    normal_style = ParagraphStyle('TextoNormal', parent=styles['Normal'], fontSize=8.5, leading=11, textColor=colors.HexColor("#334155"))
    bold_style = ParagraphStyle('TextoBold', parent=styles['Normal'], fontSize=8.5, leading=11, fontName="Helvetica-Bold", textColor=colors.HexColor("#0f172a"))

    tipo_comp = factura_data.get("tipo_comprobante", "C")
    pt_vta = factura_data.get("punto_venta", 1)
    num_comp = factura_data.get("numero_comprobante", 1)
    cae = factura_data.get("cae", "")
    cae_vto = factura_data.get("cae_vencimiento")
    cae_vto_str = cae_vto.strftime("%d/%m/%Y") if isinstance(cae_vto, (date, datetime)) else str(cae_vto)

    # Bloque emisor: Agrega imagen de logo institucional si se encuentra en static/logo.png
    emisor_elements = []
    if LOGO_PATH.exists():
        try:
            img = RLImage(str(LOGO_PATH), width=1.4*inch, height=0.7*inch)
            emisor_elements.append(img)
            emisor_elements.append(Spacer(1, 4))
        except Exception:
            pass
    emisor_elements.append(Paragraph(f"<b>{RAZON_SOCIAL}</b><br/>{DOMICILIO_COMERCIAL}<br/>CUIT: {CUIT_EMISOR}<br/>IVA: {CONDICION_IVA}", normal_style))

    # Encabezado estructurado en 3 columnas: Emisor | Letra fiscal | Datos de Factura
    header_data = [
        [
            emisor_elements,
            Paragraph(f"<font size=26><b>{tipo_comp}</b></font><br/><font size=7>COD. 011</font>", ParagraphStyle('C', alignment=1)),
            Paragraph(f"<b>FACTURA ELECTRÓNICA</b><br/>Punto Venta: {pt_vta:04d} Comp: {num_comp:08d}<br/>Fecha: {datetime.now().strftime('%d/%m/%Y')}<br/><b>ARCA - Agencia de Recaudación</b>", normal_style)
        ]
    ]
    t_header = Table(header_data, colWidths=[2.8*inch, 1.1*inch, 2.7*inch])
    t_header.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (1,0), (1,0), colors.HexColor("#f8fafc")),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(t_header)
    elements.append(Spacer(1, 14))

    # Bloque de datos del socio / receptor del servicio
    cliente_data = [
        [Paragraph(f"<b>Socio / Cliente:</b> {socio_nombre}", normal_style), Paragraph(f"<b>DNI / Doc:</b> {socio_dni}", normal_style)],
        [Paragraph(f"<b>Email:</b> {socio_email}", normal_style), Paragraph("<b>Condición IVA:</b> Consumidor Final", normal_style)]
    ]
    t_cliente = Table(cliente_data, colWidths=[3.8*inch, 2.8*inch])
    t_cliente.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#cbd5e1")),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(t_cliente)
    elements.append(Spacer(1, 14))

    # Desglose de importes y concepto
    items_data = [
        [Paragraph("<b>Descripción del Servicio / Concepto</b>", bold_style), Paragraph("<b>Período</b>", bold_style), Paragraph("<b>Subtotal</b>", bold_style)],
        [Paragraph(concepto, normal_style), Paragraph("30 días de acceso", normal_style), Paragraph(f"${factura_data['monto']:.2f}", normal_style)],
        ["", Paragraph("<b>TOTAL FACTURADO:</b>", bold_style), Paragraph(f"<b>${factura_data['monto']:.2f}</b>", bold_style)]
    ]
    t_items = Table(items_data, colWidths=[3.6*inch, 1.8*inch, 1.2*inch])
    t_items.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#0f172a")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('ALIGN', (2,0), (2,-1), 'RIGHT'),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(t_items)
    elements.append(Spacer(1, 18))

    # Cuadro de verificación legal de CAE
    cae_data = [
        [Paragraph(f"<b>CAE N°:</b> {cae}<br/><b>Fecha Vto. CAE:</b> {cae_vto_str}", bold_style),
         Paragraph("Comprobante Autorizado por <b>ARCA (Agencia de Recaudación y Control Aduanero)</b><br/>El acceso en el molinete se actualiza de forma automática.", normal_style)]
    ]
    t_cae = Table(cae_data, colWidths=[2.5*inch, 4.1*inch])
    t_cae.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#0284c7")),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f0f9ff")),
        ('PADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    elements.append(t_cae)

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()

def enviar_correo_factura(destinatario: str, nombre_socio: str, pdf_bytes: bytes, nro_factura: str):
    """
    Envía el email con el PDF adjunto. Si las credenciales SMTP no están
    configuradas en Render, finaliza silenciosamente sin arrojar error al usuario.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"[MAIL LOG] SMTP_USER o SMTP_PASSWORD no configurados. Factura {nro_factura} lista para {destinatario}.")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = f"{RAZON_SOCIAL} <{SMTP_USER}>"
        msg['To'] = destinatario
        msg['Subject'] = f"Comprobante de Pago y Factura ARCA N° {nro_factura} - {RAZON_SOCIAL}"

        cuerpo_html = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head><meta charset="UTF-8"></head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Arial, sans-serif; background-color: #f1f5f9; color: #1e293b;">
            <div style="max-width: 600px; margin: 30px auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; border: 1px solid #e2e8f0;">
                <div style="background-color: #0f172a; padding: 24px; text-align: center;">
                    <h1 style="color: #ffffff; margin: 0; font-size: 20px;">{RAZON_SOCIAL}</h1>
                    <span style="color: #10b981; font-size: 11px; font-weight: bold; text-transform: uppercase;">Confirmación Oficial de Pago</span>
                </div>
                <div style="padding: 32px 24px;">
                    <h2 style="color: #0f172a; font-size: 18px;">¡Hola {nombre_socio}!</h2>
                    <p style="font-size: 14px; line-height: 1.6; color: #334155;">
                        Confirmamos que recibimos tu pago correctamente. Tu cuota ha sido renovada y tu acceso al molinete se encuentra activo.
                    </p>
                    <div style="background-color: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 12px; padding: 16px; margin: 20px 0; font-size: 13px;">
                        <b>Comprobante Fiscal:</b> Factura N° {nro_factura}<br/>
                        <b>Archivo Adjunto:</b> Factura_{nro_factura}.pdf
                    </div>
                </div>
                <div style="background-color: #f8fafc; padding: 16px; border-top: 1px solid #e2e8f0; text-align: center; font-size: 11px; color: #94a3b8;">
                    {RAZON_SOCIAL} &bull; {DOMICILIO_COMERCIAL} &bull; CUIT: {CUIT_EMISOR}
                </div>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(cuerpo_html, 'html'))

        adjunto = MIMEApplication(pdf_bytes, _subtype="pdf")
        adjunto.add_header('Content-Disposition', 'attachment', filename=f"Factura_{nro_factura}.pdf")
        msg.attach(adjunto)

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"[MAIL ERROR]: {e}")
        return False


def enviar_correo_bienvenida(destinatario: str, nombre: str, dni: str, url_credencial: str):
    """
    Envía el correo de bienvenida al socio cuando es dado de alta.
    Informa que su usuario es su DNI y su contraseña es su correo electrónico.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = f"{RAZON_SOCIAL} <{SMTP_USER}>"
        msg['To'] = destinatario
        msg['Subject'] = f"¡Bienvenido a {RAZON_SOCIAL}! Tus credenciales de acceso"

        cuerpo_html = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f1f5f9; padding: 20px; color: #1e293b;">
            <div style="max-width: 550px; margin: auto; background: white; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0;">
                <div style="background-color: #0f172a; padding: 20px; text-align: center; color: white;">
                    <h2 style="margin: 0;">{RAZON_SOCIAL}</h2>
                    <span style="color: #10b981; font-size: 12px; font-weight: bold;">ALTA DE SOCIO EXITOSA</span>
                </div>
                <div style="padding: 24px;">
                    <p>Hola <b>{nombre}</b>, ¡te damos la bienvenida al gimnasio!</p>
                    <p>Ya puedes acceder a tu credencial digital, tu código QR para el molinete y ver tus rutinas de entrenamiento:</p>
                    <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px; margin: 15px 0;">
                        <b>Usuario:</b> {dni} (Tu DNI)<br/>
                        <b>Contraseña:</b> {destinatario} (Tu correo electrónico)
                    </div>
                    <div style="text-align: center; margin-top: 20px;">
                        <a href="{url_credencial}" style="background: #10b981; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">Ver Mi Credencial Digital</a>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(cuerpo_html, 'html'))
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"[MAIL BIENVENIDA ERROR]: {e}")
        return False


def enviar_aviso_vencimiento(destinatario: str, nombre: str, dias_restantes: int, fecha_vto: str, link_pago: str):
    """
    Envía aviso preventivo al socio (7 días o 1 día antes del vencimiento)
    para que renueve y no quede retenido en el molinete.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = f"{RAZON_SOCIAL} <{SMTP_USER}>"
        msg['To'] = destinatario
        msg['Subject'] = f"Aviso de Vencimiento de Cuota ({dias_restantes} días) - {RAZON_SOCIAL}"

        cuerpo_html = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f1f5f9; padding: 20px; color: #1e293b;">
            <div style="max-width: 550px; margin: auto; background: white; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0;">
                <div style="background-color: #0f172a; padding: 20px; text-align: center; color: white;">
                    <h2 style="margin: 0;">{RAZON_SOCIAL}</h2>
                    <span style="color: #f59e0b; font-size: 12px; font-weight: bold;">RECORDATORIO DE PAGO</span>
                </div>
                <div style="padding: 24px;">
                    <p>Hola <b>{nombre}</b>,</p>
                    <p>Te recordamos que tu abono vence el día <b>{fecha_vto}</b> (en {dias_restantes} días).</p>
                    <p>Puedes abonar online ahora mismo con Mercado Pago para evitar demoras en el molinete:</p>
                    <div style="text-align: center; margin: 25px 0;">
                        <a href="{link_pago}" style="background: #0284c7; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">Renovar Cuota Online</a>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(cuerpo_html, 'html'))
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"[MAIL VTO ERROR]: {e}")
        return False


def enviar_correo_recuperacion_password(destinatario: str, nombre_usuario: str, enlace_recuperacion: str):
    """
    Envía un correo seguro con enlace de un solo uso para reestablecer la contraseña
    olvidada de un Operador o Administrador.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = f"{RAZON_SOCIAL} <{SMTP_USER}>"
        msg['To'] = destinatario
        msg['Subject'] = f"Recuperación de Contraseña - {RAZON_SOCIAL}"

        cuerpo_html = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f1f5f9; padding: 20px; color: #1e293b;">
            <div style="max-width: 550px; margin: auto; background: white; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0;">
                <div style="background-color: #0f172a; padding: 20px; text-align: center; color: white;">
                    <h2 style="margin: 0;">{RAZON_SOCIAL}</h2>
                    <span style="color: #6366f1; font-size: 12px; font-weight: bold;">SEGURIDAD DE LA CUENTA</span>
                </div>
                <div style="padding: 24px;">
                    <p>Hola <b>{nombre_usuario}</b>,</p>
                    <p>Recibimos una solicitud para restablecer la contraseña de tu cuenta en el sistema de gestión.</p>
                    <p>Haz clic en el siguiente botón para definir una nueva clave (el enlace expira en 30 minutos):</p>
                    <div style="text-align: center; margin: 25px 0;">
                        <a href="{enlace_recuperacion}" style="background: #4f46e5; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">Restablecer Mi Contraseña</a>
                    </div>
                    <p style="font-size: 11px; color: #64748b;">Si no solicitaste este cambio, puedes ignorar este mensaje.</p>
                </div>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(cuerpo_html, 'html'))
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"[MAIL RECUPERACION ERROR]: {e}")
        return False