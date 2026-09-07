import os
import io
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, date, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Datos comerciales y fiscales configurables por variables de entorno
RAZON_SOCIAL = os.getenv("EMPRESA_RAZON_SOCIAL", "GIMNASIO FITNESS CLUB")
CUIT_EMISOR = os.getenv("EMPRESA_CUIT", "30-71234567-9")
DOMICILIO_COMERCIAL = os.getenv("EMPRESA_DOMICILIO", "Av. Principal 1234, CABA")
CONDICION_IVA = os.getenv("EMPRESA_CONDICION_IVA", "Monotributista")

# Parámetros SMTP para el envío de mails
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

def emitir_factura_arca(socio_nombre: str, socio_dni: str, monto: float, concepto: str):
    """
    Simula la llamada al Web Service de Factura Electrónica ARCA (ex AFIP)
    WSFE v1 y devuelve los datos fiscales autorizados.
    En producción real se conecta con tu certificado o API puente.
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
    Compila en memoria el PDF de la factura electrónica con formato oficial ARCA.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    elements = []
    styles = getSampleStyleSheet()

    # Estilos
    titulo_style = ParagraphStyle(
        'TituloFactura',
        parent=styles['Heading1'],
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#0f172a"),
        alignment=1
    )
    normal_style = ParagraphStyle(
        'TextoNormal',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155")
    )
    bold_style = ParagraphStyle(
        'TextoBold',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#0f172a")
    )

    # 1. Encabezado Oficial ARCA
    tipo_comp = factura_data.get("tipo_comprobante", "C")
    pt_vta = factura_data.get("punto_venta", 1)
    num_comp = factura_data.get("numero_comprobante", 1)
    cae = factura_data.get("cae", "")
    cae_vto = factura_data.get("cae_vencimiento")
    cae_vto_str = cae_vto.strftime("%d/%m/%Y") if isinstance(cae_vto, (date, datetime)) else str(cae_vto)

    header_data = [
        [
            Paragraph(f"<b>{RAZON_SOCIAL}</b><br/>{DOMICILIO_COMERCIAL}<br/>CUIT: {CUIT_EMISOR}<br/>IVA: {CONDICION_IVA}", normal_style),
            Paragraph(f"<font size=28><b>{tipo_comp}</b></font><br/><font size=7>COD. 011</font>", ParagraphStyle('C', alignment=1)),
            Paragraph(f"<b>FACTURA ELECTRÓNICA</b><br/>Punto Venta: {pt_vta:04d} Comp: {num_comp:08d}<br/>Fecha: {datetime.now().strftime('%d/%m/%Y')}<br/><b>ARCA - Agencia de Recaudación</b>", normal_style)
        ]
    ]
    t_header = Table(header_data, colWidths=[2.7*inch, 1.2*inch, 2.7*inch])
    t_header.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (1,0), (1,0), colors.HexColor("#f8fafc")),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(t_header)
    elements.append(Spacer(1, 15))

    # 2. Datos del Socio / Cliente
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
    elements.append(Spacer(1, 15))

    # 3. Detalle del Cobro
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
    elements.append(Spacer(1, 20))

    # 4. Pie con CAE oficial de ARCA
    cae_data = [
        [Paragraph(f"<b>CAE N°:</b> {cae}<br/><b>Fecha Vto. CAE:</b> {cae_vto_str}", bold_style),
         Paragraph("Comprobante Autorizado por <b>ARCA (Agencia de Recaudación y Control Aduanero)</b><br/>El pase al gimnasio se renueva automáticamente en el molinete.", normal_style)]
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
    Despacha el correo electrónico con el PDF adjunto. Si las credenciales SMTP no están
    configuradas en variables de entorno, deja el log de prueba sin interrumpir la aplicación.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"[MAIL MOCK] SMTP no configurado. Factura {nro_factura} lista para enviarse a {destinatario}.")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = f"{RAZON_SOCIAL} <{SMTP_USER}>"
        msg['To'] = destinatario
        msg['Subject'] = f"Comprobante de Pago y Factura Electrónica N° {nro_factura}"

        cuerpo_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #1e293b; line-height: 1.5;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px;">
                <h2 style="color: #059669; margin-top: 0;">¡Pago Recibido y Acceso Habilitado!</h2>
                <p>Hola <b>{nombre_socio}</b>,</p>
                <p>Te confirmamos que registramos correctamente tu pago. Tu acceso al gimnasio por el molinete ha sido renovado por 30 días.</p>
                <p>Adjuntamos a este correo tu <b>Factura Electrónica oficial de ARCA</b> correspondiente al período abonado.</p>
                <br/>
                <hr style="border: 0; border-top: 1px solid #e2e8f0;" />
                <p style="font-size: 12px; color: #64748b;">
                    {RAZON_SOCIAL} - {DOMICILIO_COMERCIAL}<br/>
                    Sistema de Gestión y Control de Accesos
                </p>
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
        print(f"[MAIL OK] Factura {nro_factura} enviada a {destinatario}.")
        return True
    except Exception as e:
        print(f"[MAIL ERROR] Error al enviar correo: {e}")
        return False