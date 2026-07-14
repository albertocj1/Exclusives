# ============================================================================
#  QR + HTML EMAIL — Exclusives PH boarding pass
#  Requires: qrcode, pillow   (add to requirements.txt)
#
#  DARK MODE NOTES — read before editing the template:
#
#  Gmail's iOS/Android apps do a blind FULL colour inversion. They ignore
#  `prefers-color-scheme`, ignore the colour-scheme meta tags, and ignore
#  [data-ogsc]. A dark-designed email like this one gets flipped to a light
#  theme and the branding falls apart. There is exactly one working fix, and
#  this file uses all three parts of it:
#
#   1. GRADIENT-LOCKED BACKGROUNDS. Gmail rewrites `background-color` but does
#      NOT touch `background-image`. Every coloured surface therefore declares
#      both:  background-color:#102A38; background-image:linear-gradient(#102A38,#102A38);
#      The gradient paints over Gmail's inverted colour, so the surface survives.
#
#   2. BLEND-MODE TEXT FIX (Rémi Parmentier). Locking backgrounds does nothing
#      for text — Gmail still inverts `color`. Gmail replaces the doctype with a
#      <u></u> element, so `u + .body` is a Gmail-only CSS hook. Nesting a
#      mix-blend-mode:difference div inside a mix-blend-mode:screen div (both on
#      black) mathematically cancels Gmail's inversion back out. In every other
#      client the selector never matches, the divs are inert, nothing changes.
#      => Every TEXT block is wrapped in .gmail-blend-screen > .gmail-blend-difference.
#
#   3. IMAGES (QR + LOGO) STAY OUTSIDE THE BLEND WRAPPERS. This is deliberate —
#      do not "tidy" them inside. Gmail does not invert images, so the blend
#      maths (which assumes everything was inverted) would come along and
#      invert the image instead. For the QR this breaks scannability; for the
#      logo it would flip the brand colours. Both images use gradient-locked
#      backgrounds on their containers instead, and the images themselves are
#      left completely alone.
#
#  Consequences for anyone editing the template:
#   - Any element that has its OWN background must be gradient-locked and must
#     sit OUTSIDE the blend wrappers.
#   - Any element that is only TEXT goes INSIDE the blend wrappers.
#   - Any element that is an IMAGE (logo, QR) goes OUTSIDE the blend wrappers.
#   - No rgba() anywhere. Semi-transparent colours composite unpredictably under
#     blend modes, so every rgba() has been pre-flattened to solid hex.
#   - Borders can't be gradient-locked, so the card and notice borders are faked
#     with a 1px-padding wrapper whose background is gradient-locked.
#
#  Known limitation: Gmail Android with a non-Google account ("GANGA") strips
#  <style> blocks entirely, so the blend fix can't load there and the email will
#  still invert. No fix exists for that client. Everything else is covered.
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

# Guests have no idea what "LC1" or "DT2" means. Show them the friendly name.
# Mirrors SPOT_DISPLAY_NAMES in main.py — keep the two in sync.
SPOT_DISPLAY_NAMES = {
    "LC1": "SVIP 1", "LC2": "SVIP 2", "LC3": "SVIP 3", "LC5": "SVIP 4", "LC6": "SVIP 5",
    "LC4": "VIP 1",  "LC7": "VIP 2",  "DC1": "VIP 3",  "DC2": "VIP 4",
    "DT1": "Table 1", "DT2": "Table 2",
}

EVENT_DATE_LONG = "Friday, August 14, 2026"
EVENT_DATE_SHORT = "Aug 14, 2026 &middot; 8:00 PM"

# Path to the logo file used in the email header. Override with LOGO_PATH if
# your deploy layout differs from the frontend's images/ folder.
LOGO_PATH = os.environ.get("LOGO_PATH", "images/logo.png")


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


def _spot_label(table_id):
    """LC1 -> 'SVIP 1 (LC1)'. Falls back to the raw ID for unknown spots."""
    if not table_id:
        return "General Admission"
    friendly = SPOT_DISPLAY_NAMES.get(table_id)
    return f"{friendly} ({table_id})" if friendly else str(table_id)


def _make_qr_png(data: str) -> bytes:
    """Branded PNG QR encoding the ticket_code.

    border=4 is the spec-mandated minimum quiet zone — the previous border=2 was
    below spec and can fail on stricter scanners. Solid white background (never
    transparent): a transparent PNG would pick up whatever an email client painted
    behind it in dark mode.
    """
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0A1A24", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _load_logo_bytes() -> bytes | None:
    """
    Reads the logo PNG off disk for inline embedding. Returns None (rather than
    raising) if the file isn't found, so a missing logo degrades gracefully to
    the text wordmark instead of breaking email sending entirely.
    """
    try:
        with open(LOGO_PATH, "rb") as f:
            return f.read()
    except FileNotFoundError:
        print(f"WARNING: logo not found at {LOGO_PATH}; falling back to text wordmark.")
        return None


def _build_email_html(guest_name, ticket_code, package_name, guests, table_id, qr_cid, logo_cid=None):
    table_display = _spot_label(table_id)
    guest_word = "guest" if str(guests) == "1" else "guests"

    # Logo image if we have one embedded, otherwise fall back to the old
    # text wordmark (wrapped in the Gmail blend fix like all other text).
    if logo_cid:
        wordmark_html = f"""
      <img src="cid:{logo_cid}" alt="Exclusives PH" width="200" style="display:block; width:200px; max-width:60%; height:auto; border:0; margin:0 auto;">
      <div class="gmail-blend-screen"><div class="gmail-blend-difference">
        <div class="text-muted" style="font-family:'Courier New', monospace; font-size:9px; letter-spacing:3px; color:#8AA0AD; text-transform:uppercase; margin-top:10px;">Manila Bay &middot; Yacht Sessions</div>
      </div></div>"""
    else:
        wordmark_html = """
      <div class="gmail-blend-screen"><div class="gmail-blend-difference">
        <span class="text-gold" style="font-family:'Courier New', monospace; font-size:12px; letter-spacing:4px; color:#F5C518; text-transform:uppercase; font-weight:bold;">EXCLUSIVES&nbsp;PH</span>
        <div class="text-muted" style="font-family:'Courier New', monospace; font-size:9px; letter-spacing:3px; color:#8AA0AD; text-transform:uppercase; margin-top:6px;">Manila Bay &middot; Yacht Sessions</div>
      </div></div>"""

    html = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title></title>
<style>
  /* This email is dark by design. Tell clients so they don't "helpfully" invert it. */
  :root { color-scheme: dark; supported-color-schemes: dark; }

  /* --------------------------------------------------------------------
     GMAIL APP ONLY. Gmail swaps the doctype for a <u></u> element, so this
     selector matches in Gmail and nowhere else. The difference blend undoes
     Gmail's inversion; the screen blend composites the result back onto the
     real (gradient-locked) background. In every other client these two divs
     have no styles at all and are completely inert.
     -------------------------------------------------------------------- */
  u + .body .gmail-blend-screen     { background:#000000; mix-blend-mode:screen; }
  u + .body .gmail-blend-difference { background:#000000; mix-blend-mode:difference; }

  /* The screen blend composites onto the card, which lifts every channel slightly.
     These are the authored colours solved backwards through that lift, so they land
     ON the real brand colours after blending. Gmail-only — every other client keeps
     the true hex from the inline styles. Do not "correct" these to look right in a
     browser; they are supposed to look off outside Gmail.
       gold  #F5C518 -> #F4BA00      cream #F2EADD -> #F1E6D3      muted #8AA0AD -> #828D96 */
  u + .body .text-gold  { color:#F4BA00 !important; border-color:#F4BA00 !important; }
  u + .body .text-cream { color:#F1E6D3 !important; }
  u + .body .text-muted { color:#828D96 !important; }

  /* Apple Mail / Outlook partial-inverters: re-assert the palette. */
  @media (prefers-color-scheme: dark) {
    .body-bg    { background-color:#0A1A24 !important; }
    .card-edge  { background-color:#60602D !important; }
    .card-bg    { background-color:#102A38 !important; }
    .qr-card-bg { background-color:#FFFFFE !important; }
    .notice-edge{ background-color:#4C532F !important; }
    .notice-bg  { background-color:#223635 !important; }
    .text-cream { color:#F2EADD !important; }
    .text-muted { color:#8AA0AD !important; }
    .text-gold  { color:#F5C518 !important; }
    a           { color:#F5C518 !important; }
  }

  /* Outlook.com / Outlook Android use these attributes instead of the media query. */
  [data-ogsc] .body-bg,    [data-ogsb] .body-bg    { background-color:#0A1A24 !important; }
  [data-ogsc] .card-edge,  [data-ogsb] .card-edge  { background-color:#60602D !important; }
  [data-ogsc] .card-bg,    [data-ogsb] .card-bg    { background-color:#102A38 !important; }
  [data-ogsc] .qr-card-bg, [data-ogsb] .qr-card-bg { background-color:#FFFFFE !important; }
  [data-ogsc] .notice-edge,[data-ogsb] .notice-edge{ background-color:#4C532F !important; }
  [data-ogsc] .notice-bg,  [data-ogsb] .notice-bg  { background-color:#223635 !important; }
  [data-ogsc] .text-cream, [data-ogsb] .text-cream { color:#F2EADD !important; }
  [data-ogsc] .text-muted, [data-ogsb] .text-muted { color:#8AA0AD !important; }
  [data-ogsc] .text-gold,  [data-ogsb] .text-gold  { color:#F5C518 !important; }
</style>
</head>

<!-- class="body" is what the Gmail `u + .body` hook above latches onto. Don't rename it. -->
<body class="body body-bg" style="margin:0; padding:0; background-color:#0A1A24; font-family:Arial, Helvetica, sans-serif;">

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="body-bg" style="background-color:#0A1A24; background-image:linear-gradient(#0A1A24,#0A1A24); padding:32px 12px;">
<tr><td align="center">
  <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%;">

    <!-- Wordmark: logo image if available (outside blend wrappers, Gmail doesn't
         invert images), else the old blended text as a fallback. -->
    <tr><td align="center" style="padding-bottom:28px;">
      __WORDMARK__
    </td></tr>

    <!-- Card. The 1px gold edge is a gradient-locked background, not a border,
         because Gmail inverts border-color and you can't gradient-lock a border. -->
    <tr><td class="card-edge" style="background-color:#60602D; background-image:linear-gradient(#60602D,#60602D); border-radius:24px; padding:1px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="card-bg" style="background-color:#102A38; background-image:linear-gradient(#102A38,#102A38); border-radius:23px;">

        <!-- Header (text -> blended) -->
        <tr><td align="center" style="padding:36px 32px 8px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-gold" style="width:52px; height:52px; line-height:52px; margin:0 auto 18px auto; border:1px solid #F5C518; border-radius:50%; color:#F5C518; font-size:22px; text-align:center;">&#10003;</div>
            <div class="text-cream" style="font-family:Georgia, 'Times New Roman', serif; font-size:28px; color:#F2EADD; letter-spacing:-0.5px;">You're on the list</div>
            <div class="text-muted" style="font-size:13px; color:#8AA0AD; line-height:1.6; padding:14px 8px 0 8px;">
              Hi __GUEST_NAME__, your payment is verified and your booking is confirmed. Present the QR code below at the Manila Yacht Club dock to board.
            </div>
          </div></div>
        </td></tr>

        <!-- QR. Deliberately OUTSIDE the blend wrappers — see the notes at the top
             of this file. Gmail leaves images alone; the blend maths would not. -->
        <tr><td align="center" style="padding:28px 32px 8px 32px;">
          <table role="presentation" cellpadding="0" cellspacing="0" class="qr-card-bg" style="background-color:#FFFFFE; background-image:linear-gradient(#FFFFFE,#FFFFFE); border-radius:18px;">
            <tr><td align="center" style="padding:16px;">
              <img src="cid:__QR_CID__" alt="Boarding QR code" width="180" height="180" style="display:block; width:180px; height:180px; border:0;">
            </td></tr>
          </table>
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-muted" style="font-family:'Courier New', monospace; font-size:11px; letter-spacing:2px; color:#8AA0AD; text-transform:uppercase; padding-top:16px;">Scan at reception</div>
          </div></div>
        </td></tr>

        <!-- Ticket code (text -> blended) -->
        <tr><td align="center" style="padding:8px 32px 24px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-gold" style="font-family:'Courier New', monospace; font-size:22px; letter-spacing:3px; color:#F5C518; font-weight:bold;">__TICKET_CODE__</div>
          </div></div>
        </td></tr>

        <!-- Perforation (border -> blended, so it restores with the text) -->
        <tr><td style="padding:0 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div style="border-top:2px dashed #55582E; font-size:0; line-height:0;">&nbsp;</div>
          </div></div>
        </td></tr>

        <!-- Details (text -> blended) -->
        <tr><td style="padding:24px 32px 8px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-family:'Courier New', monospace; font-size:12px;">
              <tr><td class="text-muted" style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0;">Passenger</td>
                  <td align="right" class="text-cream" style="color:#F2EADD; font-weight:bold; padding:8px 0;">__GUEST_NAME__</td></tr>
              <tr><td class="text-muted" style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid #1E3744;">Ticket Type</td>
                  <td align="right" class="text-cream" style="color:#F2EADD; padding:8px 0; border-top:1px solid #1E3744;">__PACKAGE__</td></tr>
              <tr><td class="text-muted" style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid #1E3744;">Guests</td>
                  <td align="right" class="text-cream" style="color:#F2EADD; padding:8px 0; border-top:1px solid #1E3744;">__GUESTS__ __GUEST_WORD__</td></tr>
              <tr><td class="text-muted" style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid #1E3744;">Table / Spot</td>
                  <td align="right" class="text-cream" style="color:#F2EADD; padding:8px 0; border-top:1px solid #1E3744;">__TABLE__</td></tr>
              <tr><td class="text-muted" style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid #1E3744;">Date</td>
                  <td align="right" class="text-cream" style="color:#F2EADD; padding:8px 0; border-top:1px solid #1E3744;">__DATE_SHORT__</td></tr>
              <tr><td class="text-muted" style="color:#8AA0AD; text-transform:uppercase; letter-spacing:1px; padding:8px 0; border-top:1px solid #1E3744;">Boarding</td>
                  <td align="right" class="text-cream" style="color:#F2EADD; padding:8px 0; border-top:1px solid #1E3744;">Manila Yacht Club</td></tr>
            </table>
          </div></div>
        </td></tr>

        <!-- Notice. Same 1px-edge trick as the card. -->
        <tr><td style="padding:16px 32px 36px 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notice-edge" style="background-color:#4C532F; background-image:linear-gradient(#4C532F,#4C532F); border-radius:14px;">
            <tr><td style="padding:1px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notice-bg" style="background-color:#223635; background-image:linear-gradient(#223635,#223635); border-radius:13px;">
                <tr><td style="padding:16px;">
                  <div class="gmail-blend-screen"><div class="gmail-blend-difference">
                    <div class="text-muted" style="font-size:12px; color:#8AA0AD; line-height:1.6;">
                      <span class="text-gold" style="color:#F5C518; font-weight:bold;">__DATE_LONG__.</span> Check-in 8:00pm, Smart resort-luxe, strictly 18+.
                    </div>
                  </div></div>
                </td></tr>
              </table>
            </td></tr>
          </table>
        </td></tr>

      </table>
    </td></tr>

    <!-- Footer (text -> blended) -->
    <tr><td align="center" style="padding:28px 20px 8px 20px;">
      <div class="gmail-blend-screen"><div class="gmail-blend-difference">
        <div class="text-muted" style="font-size:11px; color:#8AA0AD; line-height:1.7;">
          Exclusives PH &middot; Manila Yacht Club, CCP Complex, Roxas Blvd, Malate, Manila<br>
          Questions? Reply to this email or reach us at exclusives.est2023@gmail.com
        </div>
      </div></div>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>"""

    return (html.replace("__WORDMARK__", wordmark_html)
                .replace("__GUEST_NAME__", str(guest_name))
                .replace("__TICKET_CODE__", str(ticket_code))
                .replace("__PACKAGE__", str(package_name))
                .replace("__GUESTS__", str(guests))
                .replace("__GUEST_WORD__", guest_word)
                .replace("__TABLE__", str(table_display))
                .replace("__DATE_LONG__", EVENT_DATE_LONG)
                .replace("__DATE_SHORT__", EVENT_DATE_SHORT)
                .replace("__QR_CID__", str(qr_cid)))


def send_approval_email(to_email, guest_name, ticket_code, package_name, guests=1, table_id=None):
    """Build + send the branded confirmation email with an inline QR code and
    inline logo image. The QR encodes the ticket_code, which your reception
    scanner looks up."""
    try:
        service = get_gmail_service()

        # Content-ID for the inline QR image (angle-bracketed value)
        qr_msgid = make_msgid(domain="exclusivesph")   # e.g. <abc123@exclusivesph>
        qr_cid = qr_msgid[1:-1]                        # strip <> for the HTML src

        # Content-ID for the inline logo image, if the file is available.
        logo_bytes = _load_logo_bytes()
        logo_msgid = None
        logo_cid = None
        if logo_bytes:
            logo_msgid = make_msgid(domain="exclusivesph")
            logo_cid = logo_msgid[1:-1]

        qr_png = _make_qr_png(ticket_code)
        html_body = _build_email_html(
            guest_name, ticket_code, package_name, guests, table_id, qr_cid, logo_cid
        )

        # Plain-text fallback for clients that don't render HTML
        text_body = (
            f"Hi {guest_name},\n\n"
            f"Your payment is verified and your booking for '{package_name}' is confirmed.\n\n"
            f"Ticket code: {ticket_code}\n"
            f"Guests: {guests}\n"
            f"Table / Spot: {_spot_label(table_id)}\n"
            f"Date: {EVENT_DATE_LONG} - 8:00 PM\n"
            f"Boarding: Manila Yacht Club, check-in 8:00pm.\n\n"
            f"Present your QR code (in the HTML version of this email) at reception.\n\n"
            f"Exclusives PH"
        )

        msg = EmailMessage()
        msg['To'] = to_email
        msg['From'] = os.environ.get("SENDER_EMAIL", "your-email@gmail.com")
        msg['Subject'] = "You're on the list — Exclusives PH Boarding Pass"
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype='html')

        # Attach the QR (and logo, if we have one) as inline (related) images
        # on the HTML part.
        html_part = msg.get_payload()[1]
        html_part.add_related(qr_png, maintype='image', subtype='png', cid=qr_msgid)
        if logo_bytes:
            html_part.add_related(logo_bytes, maintype='image', subtype='png', cid=logo_msgid)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Successfully sent confirmation email to {to_email}")
    except Exception as e:
        print(f"ERROR sending email to {to_email}: {str(e)}")