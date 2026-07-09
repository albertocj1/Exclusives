# ============================================================================
#  QR + HTML EMAIL  — drop-in replacement for your send_approval_email
#  Requires: qrcode, pillow   (add to requirements.txt)
# ============================================================================
import os, io, json, base64
from email.message import EmailMessage
from email.utils import make_msgid

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.send']


def get_gmail_service():
    creds = None
    token_json_str = os.environ.get("GMAIL_TOKEN_JSON")
    if token_json_str:
        creds = Credentials.from_authorized_user_info(json.loads(token_json_str), SCOPES)
    elif os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        raise Exception("Gmail credentials are not valid. Ensure GMAIL_TOKEN_JSON env var or token.json is present.")

    return build('gmail', 'v1', credentials=creds)


def _make_qr_png(data: str) -> bytes:
    """Generate a branded PNG QR encoding the given string (the ticket_code)."""
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0A1A24", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _build_email_html(guest_name, ticket_code, package_name, guests, table_id, qr_cid):
    table_display = table_id if table_id else "General Admission"
    guest_word = "guest" if str(guests) == "1" else "guests"
    html = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="x-apple-disable-message-reformatting">
</head>
<body style="margin:0; padding:0; background-color:#0A1A24; font-family:Arial, Helvetica, sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#0A1A24; padding:32px 12px;">
<tr><td align="center">
  <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%;">
    <tr><td align="center" style="padding-bottom:28px;">
      <span style="font-family:'Courier New', monospace; font-size:12px; letter-spacing:4px; color:#F5C518; text-transform:uppercase; font-weight:bold;">EXCLUSIVES&nbsp;PH</span>
      <div style="font-family:'Courier New', monospace; font-size:9px; letter-spacing:3px; color:#8AA0AD; text-transform:uppercase; margin-top:6px;">Manila Bay &middot; Yacht Sessions</div>
    </td></tr>
    <tr><td style="background-color:#102A38; border:1px solid rgba(245,197,24,0.35); border-radius:24px; overflow:hidden;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td align="center" style="padding:36px 32px 8px 32px;">
          <div style="width:52px; height:52px; line-height:52px; margin:0 auto 18px auto; border:1px solid #F5C518; border-radius:50%; color:#F5C518; font-size:22px; text-align:center;">&#10003;</div>
          <div style="font-family:Georgia, 'Times New Roman', serif; font-size:28px; color:#F2EADD; letter-spacing:-0.5px;">You're on the list</div>
          <div style="font-size:13px; color:#8AA0AD; line-height:1.6; padding:14px 8px 0 8px;">
            Hi __GUEST_NAME__, your payment is verified and your booking is confirmed. Present the QR code below at the Manila Yacht Club dock to board.
          </div>
        </td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td align="center" style="padding:28px 32px 8px 32px;">
          <table role="presentation" cellpadding="0" cellspacing="0" style="background-color:#FFFFFF; border-radius:18px;">
            <tr><td align="center" style="padding:16px;">
              <img src="cid:__QR_CID__" alt="Boarding QR code" width="180" height="180" style="display:block; width:180px; height:180px;">
            </td></tr>
          </table>
          <div style="font-family:'Courier New', monospace; font-size:11px; letter-spacing:2px; color:#8AA0AD; text-transform:uppercase; padding-top:16px;">Scan at reception</div>
        </td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td align="center" style="padding:8px 32px 24px 32px;">
          <div style="font-family:'Courier New', monospace; font-size:22px; letter-spacing:3px; color:#F5C518; font-weight:bold;">__TICKET_CODE__</div>
        </td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="padding:0 32px;"><div style="border-top:2px dashed rgba(245,197,24,0.3); font-size:0; line-height:0;">&nbsp;</div></td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="padding:24px 32px 8px 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-family:'Courier New', monospace; font-size:12px;">
            <tr><td style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0;">Passenger</td>
                <td align="right" style="color:#F2EADD; font-weight:bold; padding:8px 0;">__GUEST_NAME__</td></tr>
            <tr><td style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid rgba(255,255,255,0.06);">Ticket Type</td>
                <td align="right" style="color:#F2EADD; padding:8px 0; border-top:1px solid rgba(255,255,255,0.06);">__PACKAGE__</td></tr>
            <tr><td style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid rgba(255,255,255,0.06);">Guests</td>
                <td align="right" style="color:#F2EADD; padding:8px 0; border-top:1px solid rgba(255,255,255,0.06);">__GUESTS__ __GUEST_WORD__</td></tr>
            <tr><td style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid rgba(255,255,255,0.06);">Table / Spot</td>
                <td align="right" style="color:#F2EADD; padding:8px 0; border-top:1px solid rgba(255,255,255,0.06);">__TABLE__</td></tr>
            <tr><td style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid rgba(255,255,255,0.06);">Boarding</td>
                <td align="right" style="color:#F2EADD; padding:8px 0; border-top:1px solid rgba(255,255,255,0.06);">Manila Yacht Club</td></tr>
          </table>
        </td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="padding:16px 32px 36px 32px;">
          <div style="background-color:rgba(245,197,24,0.08); border:1px solid rgba(245,197,24,0.2); border-radius:14px; padding:16px; font-size:12px; color:#8AA0AD; line-height:1.6;">
            <span style="color:#F5C518; font-weight:bold;">Check-in 21:00.</span> We sail 21:30 sharp &mdash; once off the dock we can't return for latecomers. Smart resort-luxe, strictly 18+.
          </div>
        </td></tr>
      </table>
    </td></tr>
    <tr><td align="center" style="padding:28px 20px 8px 20px;">
      <div style="font-size:11px; color:#8AA0AD; line-height:1.7;">
        Exclusives PH &middot; Manila Yacht Club, CCP Complex, Roxas Blvd, Malate, Manila<br>
        Questions? Reply to this email or reach us at exclusives.est2023@gmail.com
      </div>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""
    return (html.replace("__GUEST_NAME__", str(guest_name))
                .replace("__TICKET_CODE__", str(ticket_code))
                .replace("__PACKAGE__", str(package_name))
                .replace("__GUESTS__", str(guests))
                .replace("__GUEST_WORD__", guest_word)
                .replace("__TABLE__", str(table_display))
                .replace("__QR_CID__", str(qr_cid)))


def send_approval_email(to_email, guest_name, ticket_code, package_name, guests=1, table_id=None):
    """Build + send the branded confirmation email with an inline QR code.
    The QR encodes the ticket_code, which your reception scanner looks up."""
    try:
        service = get_gmail_service()

        # Content-ID for the inline QR image (angle-bracketed value)
        qr_msgid = make_msgid(domain="exclusivesph")   # e.g. <abc123@exclusivesph>
        qr_cid = qr_msgid[1:-1]                          # strip <> for the HTML src

        qr_png = _make_qr_png(ticket_code)
        html_body = _build_email_html(guest_name, ticket_code, package_name, guests, table_id, qr_cid)

        # Plain-text fallback for clients that don't render HTML
        text_body = (
            f"Hi {guest_name},\n\n"
            f"Your payment is verified and your booking for '{package_name}' is confirmed.\n\n"
            f"Ticket code: {ticket_code}\n"
            f"Guests: {guests}\n"
            f"Table / Spot: {table_id or 'General Admission'}\n"
            f"Boarding: Manila Yacht Club, check-in 21:00, sail 21:30.\n\n"
            f"Present your QR code (in the HTML version of this email) at reception.\n\n"
            f"Exclusives PH"
        )

        msg = EmailMessage()
        msg['To'] = to_email
        msg['From'] = os.environ.get("SENDER_EMAIL", "your-email@gmail.com")
        msg['Subject'] = "You're on the list — Exclusives PH Boarding Pass"
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype='html')

        # Attach the QR as an inline (related) image on the HTML part
        html_part = msg.get_payload()[1]
        html_part.add_related(qr_png, maintype='image', subtype='png', cid=qr_msgid)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Successfully sent confirmation email to {to_email}")
    except Exception as e:
        print(f"ERROR sending email to {to_email}: {str(e)}")