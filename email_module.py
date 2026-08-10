# ============================================================================
#  QR + HTML EMAIL — Exclusives PH boarding pass & reminder system
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
BOTTLE_POSTER_PATH = os.environ.get("BOTTLE_POSTER_PATH", "images/bottle-poster.jpg")
FOOD_POSTER_PATH = os.environ.get("FOOD_POSTER_PATH", "images/food-poster.jpg")

# If set, the logo is referenced as a normal hosted <img src="..."> instead of
# being embedded as an inline cid: MIME part. This avoids Gmail's behavior of
# listing any cid-embedded image in its attachment strip (see email_module
# dark-mode notes above for background) — a hosted URL has no MIME part at
# all, so there's nothing for Gmail to list. Falls back to inline cid
# embedding (LOGO_PATH) if this is empty.
LOGO_URL = "https://gvtutnofyrkmyyclfshe.supabase.co/storage/v1/object/public/assets/logo.png"


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
    """LC1 -> 'SVIP 1 (LC1)'. Falls back to 'General Admission' for unknown/null spots."""
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


def _load_image_bytes(path: str, label: str) -> bytes | None:
    """Generic version of _load_logo_bytes for the bottle/food poster images —
    returns None on a missing file so the email still sends (just without that
    image) instead of failing outright."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        print(f"WARNING: {label} not found at {path}; sending email without it.")
        return None


def _build_email_html(guest_name, ticket_code, package_name, guests, table_id, qr_cid, logo_cid=None):
    table_display = _spot_label(table_id)
    guest_word = "guest" if str(guests) == "1" else "guests"

    if LOGO_URL:
        wordmark_html = f"""
      <img src="{LOGO_URL}" alt="Exclusives PH" width="200" style="display:block; width:200px; max-width:60%; height:auto; border:0; margin:0 auto;">
      <div class="gmail-blend-screen"><div class="gmail-blend-difference">
        <div class="text-muted" style="font-family:'Courier New', monospace; font-size:9px; letter-spacing:3px; color:#8AA0AD; text-transform:uppercase; margin-top:10px;">Manila Bay &middot; Yacht Sessions</div>
      </div></div>"""
    elif logo_cid:
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
  :root { color-scheme: dark; supported-color-schemes: dark; }
  u + .body .gmail-blend-screen     { background:#000000; mix-blend-mode:screen; }
  u + .body .gmail-blend-difference { background:#000000; mix-blend-mode:difference; }
  u + .body .text-gold  { color:#F4BA00 !important; border-color:#F4BA00 !important; }
  u + .body .text-cream { color:#F1E6D3 !important; }
  u + .body .text-muted { color:#828D96 !important; }
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
<body class="body body-bg" style="margin:0; padding:0; background-color:#0A1A24; font-family:Arial, Helvetica, sans-serif;">

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="body-bg" style="background-color:#0A1A24; background-image:linear-gradient(#0A1A24,#0A1A24); padding:32px 12px;">
<tr><td align="center">
  <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%;">
    <tr><td align="center" style="padding-bottom:28px;">__WORDMARK__</td></tr>
    <tr><td class="card-edge" style="background-color:#60602D; background-image:linear-gradient(#60602D,#60602D); border-radius:24px; padding:1px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="card-bg" style="background-color:#102A38; background-image:linear-gradient(#102A38,#102A38); border-radius:23px;">
        <tr><td align="center" style="padding:36px 32px 8px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-gold" style="width:52px; height:52px; line-height:52px; margin:0 auto 18px auto; border:1px solid #F5C518; border-radius:50%; color:#F5C518; font-size:22px; text-align:center;">&#10003;</div>
            <div class="text-cream" style="font-family:Georgia, 'Times New Roman', serif; font-size:28px; color:#F2EADD; letter-spacing:-0.5px;">You're on the list</div>
            <div class="text-muted" style="font-size:13px; color:#8AA0AD; line-height:1.6; padding:14px 8px 0 8px;">
              Hi __GUEST_NAME__, your payment is verified and your booking is confirmed. Present the QR code below at the Manila Yacht Club dock to board.
            </div>
          </div></div>
        </td></tr>
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
        <tr><td align="center" style="padding:8px 32px 24px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-gold" style="font-family:'Courier New', monospace; font-size:22px; letter-spacing:3px; color:#F5C518; font-weight:bold;">__TICKET_CODE__</div>
          </div></div>
        </td></tr>
        <tr><td style="padding:0 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div style="border-top:2px dashed #55582E; font-size:0; line-height:0;">&nbsp;</div>
          </div></div>
        </td></tr>
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
        <tr><td style="padding:16px 32px 36px 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notice-edge" style="background-color:#4C532F; background-image:linear-gradient(#4C532F,#4C532F); border-radius:14px;">
            <tr><td style="padding:1px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notice-bg" style="background-color:#223635; background-image:linear-gradient(#223635,#223635); border-radius:13px;">
                <tr><td style="padding:16px;">
                  <div class="gmail-blend-screen"><div class="gmail-blend-difference">
                    <div class="text-muted" style="font-size:12px; color:#8AA0AD; line-height:1.6;">
                      <span class="text-gold" style="color:#F5C518; font-weight:bold;">__DATE_LONG__.</span> Check-in 8:00pm, Smart resort-luxe, strictly 18+. Guestlist only &mdash; no walk-ins.
                    </div>
                  </div></div>
                </td></tr>
              </table>
            </td></tr>
          </table>
        </td></tr>
      </table>
    </td></tr>
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
    """Build + send the branded confirmation email with an inline QR code and inline logo image."""
    try:
        service = get_gmail_service()
        qr_msgid = make_msgid(domain="exclusivesph")
        qr_cid = qr_msgid[1:-1]

        logo_bytes = None if LOGO_URL else _load_logo_bytes()
        logo_msgid = None
        logo_cid = None
        if logo_bytes:
            logo_msgid = make_msgid(domain="exclusivesph")
            logo_cid = logo_msgid[1:-1]

        qr_png = _make_qr_png(ticket_code)
        html_body = _build_email_html(guest_name, ticket_code, package_name, guests, table_id, qr_cid, logo_cid)

        text_body = (
            f"Hi {guest_name},\n\n"
            f"Your payment is verified and your booking for '{package_name}' is confirmed.\n\n"
            f"Ticket code: {ticket_code}\n"
            f"Guests: {guests}\n"
            f"Table / Spot: {_spot_label(table_id)}\n"
            f"Date: {EVENT_DATE_LONG} - 8:00 PM\n"
            f"Boarding: Manila Yacht Club, check-in 8:00pm.\n\n"
            f"Present your QR code (in the HTML version of this email) at reception.\n\n"
            f"Guestlist only - no walk-ins.\n\n"
            f"Exclusives PH"
        )

        msg = EmailMessage()
        msg['To'] = to_email
        msg['From'] = os.environ.get("SENDER_EMAIL", "your-email@gmail.com")
        msg['Subject'] = "You're on the list — Exclusives PH Boarding Pass"
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype='html')

        html_part = msg.get_payload()[1]
        html_part.add_related(qr_png, maintype='image', subtype='png', cid=qr_msgid, filename='ticket-qr.png', disposition='inline')
        if logo_bytes:
            html_part.add_related(logo_bytes, maintype='image', subtype='png', cid=logo_msgid, filename='logo.png', disposition='inline')

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Successfully sent confirmation email to {to_email}")
    except Exception as e:
        print(f"ERROR sending email to {to_email}: {str(e)}")


# Per-package complimentary bottle/food config for the pre-event choices email.
# bottle_count: how many complimentary bottles this tier gets to pick (0 = no
# bottle section at all). food: whether a complimentary food pick is offered.
PACKAGE_TIER_CONFIG = {
    "SVIP Couch":           {"bottle_count": 2, "food": True},
    "Indoor Couch":         {"bottle_count": 1, "food": True},
    "Outdoor Couch":        {"bottle_count": 1, "food": True},
    "6-Pax Bottle Bundle":  {"bottle_count": 1, "food": False},
    "Standing Table":       {"bottle_count": 0, "food": False},
    "Entrance Fee":         {"bottle_count": 0, "food": False},
}


def send_event_details_email(to_email, guest_name, package_name=None, table_id=None):
    """Sends the pre-event details email to an already-confirmed guest.

    What's included depends on the package tier (see PACKAGE_TIER_CONFIG):
      - SVIP Couch gets 2 complimentary bottle picks + a food pick.
      - Indoor/Outdoor Couch (VIP) gets 1 complimentary bottle pick + a food pick.
      - 6-Pax Bottle Bundle gets 1 complimentary bottle pick, no food.
      - Standing Table / Entrance Fee get neither — just a plain confirmation
        with check-in info.
    Unknown/missing package_name falls back to no bottle/food section (safest
    default — never over-promises a perk that wasn't paid for).
    """
    try:
        cfg = PACKAGE_TIER_CONFIG.get(package_name, {"bottle_count": 0, "food": False})
        bottle_count = cfg["bottle_count"]
        has_bottle = bottle_count > 0
        has_food = cfg["food"]
        has_choices = has_bottle or has_food

        service = get_gmail_service()
        logo_bytes = None if LOGO_URL else _load_logo_bytes()
        logo_msgid = None
        logo_cid = None
        if logo_bytes:
            logo_msgid = make_msgid(domain="exclusivesph")
            logo_cid = logo_msgid[1:-1]

        bottle_bytes = None
        bottle_msgid = None
        bottle_cid = None
        if has_bottle:
            bottle_bytes = _load_image_bytes(BOTTLE_POSTER_PATH, "bottle poster")
            if bottle_bytes:
                bottle_msgid = make_msgid(domain="exclusivesph")
                bottle_cid = bottle_msgid[1:-1]

        food_bytes = None
        food_msgid = None
        food_cid = None
        if has_food:
            food_bytes = _load_image_bytes(FOOD_POSTER_PATH, "food poster")
            if food_bytes:
                food_msgid = make_msgid(domain="exclusivesph")
                food_cid = food_msgid[1:-1]

        if LOGO_URL:
            wordmark_html = f"""
          <img src="{LOGO_URL}" alt="Exclusives PH" width="200" style="display:block; width:200px; max-width:60%; height:auto; border:0; margin:0 auto;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-muted" style="font-family:'Courier New', monospace; font-size:9px; letter-spacing:3px; color:#8AA0AD; text-transform:uppercase; margin-top:10px;">Manila Bay &middot; Yacht Sessions</div>
          </div></div>"""
        elif logo_cid:
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

        # Reply-by deadline for the bottle/food picks. Keep in sync with the
        # BOTTLE.png / FOOD.png selection posters this copy is based on.
        RSVP_DEADLINE = "August 12, 2026"
        BOTTLE_OPTIONS = [
            ("Absolut Vodka", "Sprite"),
            ("Johnnie Walker Black Label", "Coca-Cola"),
            ("J\u00e4germeister", "Red Bull"),
        ]
        FOOD_OPTIONS = ["Nachos", "Tacos", "Charcuterie Board"]

        def choice_rows_html(lines):
            rows = ""
            for i, line in enumerate(lines, start=1):
                rows += f"""
              <tr>
                <td valign="top" style="padding:8px 0; width:26px;">
                  <div class="text-gold" style="color:#F5C518; font-family:'Courier New', monospace; font-size:13px; font-weight:bold;">{i}.</div>
                </td>
                <td valign="top" style="padding:8px 0;">
                  <div class="text-cream" style="font-size:14px; color:#F2EADD; line-height:1.5;">{line}</div>
                </td>
              </tr>"""
            return rows

        bottle_lines = [
            f'{name} <span class="text-muted" style="color:#8AA0AD;">&mdash; with {mixer}</span>'
            for name, mixer in BOTTLE_OPTIONS
        ]
        bottle_rows_html = choice_rows_html(bottle_lines)
        food_rows_html = choice_rows_html(FOOD_OPTIONS)

        bottle_heading = "Your Complimentary Bottles &mdash; Pick 2" if bottle_count == 2 else "Your Complimentary Bottle"

        # Poster images sit OUTSIDE the blend wrapper with a gradient-locked
        # container, same rule as the QR code and logo — see the dark-mode
        # notes at the top of this file. Falls back to empty string (just the
        # text list below it) if the file isn't found on disk.
        bottle_poster_html = ""
        if bottle_cid:
            bottle_poster_html = f"""
        <tr><td align="center" style="padding:22px 32px 0 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="card-edge" style="background-color:#60602D; background-image:linear-gradient(#60602D,#60602D); border-radius:16px; padding:1px;">
            <tr><td style="line-height:0; font-size:0;">
              <img src="cid:{bottle_cid}" alt="Choose your complimentary bottle: Absolut Vodka, Johnnie Walker Black Label, or J\u00e4germeister" width="416" style="display:block; width:100%; max-width:416px; height:auto; border:0; border-radius:15px;">
            </td></tr>
          </table>
        </td></tr>"""

        food_poster_html = ""
        if food_cid:
            food_poster_html = f"""
        <tr><td align="center" style="padding:20px 32px 0 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="card-edge" style="background-color:#60602D; background-image:linear-gradient(#60602D,#60602D); border-radius:16px; padding:1px;">
            <tr><td style="line-height:0; font-size:0;">
              <img src="cid:{food_cid}" alt="Choose one complimentary food item: Nachos, Tacos, or Charcuterie Board" width="416" style="display:block; width:100%; max-width:416px; height:auto; border:0; border-radius:15px;">
            </td></tr>
          </table>
        </td></tr>"""

        # --- Conditionally-rendered sections -------------------------------
        bottle_section_html = ""
        if has_bottle:
            bottle_section_html = f"""
{bottle_poster_html}
        <tr><td style="padding:22px 32px 4px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-gold" style="font-family:'Courier New', monospace; font-size:10px; letter-spacing:2px; color:#F5C518; text-transform:uppercase; font-weight:bold;">{bottle_heading}</div>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{bottle_rows_html}
            </table>
          </div></div>
        </td></tr>"""

        divider_html = ""
        if has_bottle and has_food:
            divider_html = """
        <tr><td style="padding:0 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div style="border-top:2px dashed #55582E; font-size:0; line-height:0;">&nbsp;</div>
          </div></div>
        </td></tr>"""

        food_section_html = ""
        if has_food:
            food_section_html = f"""
{food_poster_html}
        <tr><td style="padding:20px 32px 4px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-gold" style="font-family:'Courier New', monospace; font-size:10px; letter-spacing:2px; color:#F5C518; text-transform:uppercase; font-weight:bold;">Your Complimentary Food Item</div>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{food_rows_html}
            </table>
          </div></div>
        </td></tr>"""

        # Intro copy + reply notice adapt to what's actually being offered.
        if has_bottle and has_food:
            intro_line = "two quick picks before you board Exclusives PH &mdash; Manila Bay:"
            reply_what = "your two bottle selections and food selection" if bottle_count == 2 else "your bottle and food selections"
        elif has_bottle:
            intro_line = ("your two complimentary bottle picks before you board Exclusives PH &mdash; Manila Bay:"
                           if bottle_count == 2 else
                           "your complimentary bottle pick before you board Exclusives PH &mdash; Manila Bay:")
            reply_what = "your two bottle selections" if bottle_count == 2 else "your bottle selection"
        elif has_food:
            intro_line = "your complimentary food pick before you board Exclusives PH &mdash; Manila Bay:"
            reply_what = "your food selection"
        else:
            intro_line = "you're all set to board Exclusives PH &mdash; Manila Bay:"
            reply_what = None

        reply_notice_html = ""
        if reply_what:
            reply_notice_html = f"""
        <tr><td style="padding:20px 32px 8px 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notice-edge" style="background-color:#4C532F; background-image:linear-gradient(#4C532F,#4C532F); border-radius:14px;">
            <tr><td style="padding:1px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notice-bg" style="background-color:#223635; background-image:linear-gradient(#223635,#223635); border-radius:13px;">
                <tr><td style="padding:16px;">
                  <div class="gmail-blend-screen"><div class="gmail-blend-difference">
                    <div class="text-muted" style="font-size:12px; color:#8AA0AD; line-height:1.6;">
                      <span class="text-gold" style="color:#F5C518; font-weight:bold;">Reply to this email</span> with {reply_what} by <span class="text-gold" style="color:#F5C518; font-weight:bold;">{RSVP_DEADLINE}</span>. If we don't hear from you by then, these will be assigned based on availability.
                    </div>
                  </div></div>
                </td></tr>
              </table>
            </td></tr>
          </table>
        </td></tr>"""

        heading_text = "Choose Your Bottle &amp; Food" if (has_bottle and has_food) else (
            bottle_heading if has_bottle else ("Choose Your Food" if has_food else "You're Confirmed")
        )

        html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<style>
  :root {{ color-scheme: dark; supported-color-schemes: dark; }}
  u + .body .gmail-blend-screen     {{ background:#000000; mix-blend-mode:screen; }}
  u + .body .gmail-blend-difference {{ background:#000000; mix-blend-mode:difference; }}
  u + .body .text-gold  {{ color:#F4BA00 !important; border-color:#F4BA00 !important; }}
  u + .body .text-cream {{ color:#F1E6D3 !important; }}
  u + .body .text-muted {{ color:#828D96 !important; }}
  @media (prefers-color-scheme: dark) {{
    .body-bg {{ background-color:#0A1A24 !important; }}
    .card-edge {{ background-color:#60602D !important; }}
    .card-bg {{ background-color:#102A38 !important; }}
    .notice-edge{{ background-color:#4C532F !important; }}
    .notice-bg  {{ background-color:#223635 !important; }}
    .text-cream {{ color:#F2EADD !important; }}
    .text-muted {{ color:#8AA0AD !important; }}
    .text-gold  {{ color:#F5C518 !important; }}
  }}
</style>
</head>
<body class="body body-bg" style="margin:0; padding:0; background-color:#0A1A24; font-family:Arial, Helvetica, sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="body-bg" style="background-color:#0A1A24; background-image:linear-gradient(#0A1A24,#0A1A24); padding:32px 12px;">
<tr><td align="center">
  <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%;">
    <tr><td align="center" style="padding-bottom:28px;">{wordmark_html}</td></tr>
    <tr><td class="card-edge" style="background-color:#60602D; background-image:linear-gradient(#60602D,#60602D); border-radius:24px; padding:1px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="card-bg" style="background-color:#102A38; background-image:linear-gradient(#102A38,#102A38); border-radius:23px;">
        <tr><td align="center" style="padding:36px 32px 8px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-cream" style="font-family:Georgia, 'Times New Roman', serif; font-size:26px; color:#F2EADD; letter-spacing:-0.5px;">{heading_text}</div>
            <div class="text-muted" style="font-size:13px; color:#8AA0AD; line-height:1.6; padding:14px 8px 0 8px;">
              Hi {guest_name}, {intro_line}
            </div>
          </div></div>
        </td></tr>
{bottle_section_html}
{divider_html}
{food_section_html}
{reply_notice_html}

        <tr><td style="padding:8px 32px 8px 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notice-edge" style="background-color:#4C532F; background-image:linear-gradient(#4C532F,#4C532F); border-radius:14px;">
            <tr><td style="padding:1px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notice-bg" style="background-color:#223635; background-image:linear-gradient(#223635,#223635); border-radius:13px;">
                <tr><td style="padding:16px;">
                  <div class="gmail-blend-screen"><div class="gmail-blend-difference">
                    <div class="text-muted" style="font-size:12px; color:#8AA0AD; line-height:1.6;">
                      <span class="text-gold" style="color:#F5C518; font-weight:bold;">Bring your own clean slippers</span> &mdash; advisable for onboard comfort.
                    </div>
                  </div></div>
                </td></tr>
              </table>
            </td></tr>
          </table>
        </td></tr>

        <tr><td style="padding:16px 32px 36px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-muted" style="font-size:12px; color:#8AA0AD; line-height:1.6; border-top:1px solid #1E3744; padding-top:16px;">
              Check-in 8:00pm &middot; Manila Yacht Club. See you on board!
            </div>
          </div></div>
        </td></tr>
      </table>
    </td></tr>
    <tr><td align="center" style="padding:28px 20px 8px 20px;">
      <div class="gmail-blend-screen"><div class="gmail-blend-difference">
        <div class="text-muted" style="font-size:11px; color:#8AA0AD; line-height:1.7;">
          Exclusives PH &middot; Manila Yacht Club, CCP Complex, Roxas Blvd, Malate, Manila<br>
          Questions? Reply directly to this email.
        </div>
      </div></div>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""

        text_sections = []
        if has_bottle:
            bottle_text = "\n".join(f"{i}. {name} - with {mixer}" for i, (name, mixer) in enumerate(BOTTLE_OPTIONS, start=1))
            label = "YOUR COMPLIMENTARY BOTTLES (PICK 2)" if bottle_count == 2 else "YOUR COMPLIMENTARY BOTTLE"
            text_sections.append(f"{label}\n{bottle_text}")
        if has_food:
            food_text = "\n".join(f"{i}. {item}" for i, item in enumerate(FOOD_OPTIONS, start=1))
            text_sections.append(f"YOUR COMPLIMENTARY FOOD ITEM\n{food_text}")

        text_body = f"Hi {guest_name},\n\n"
        text_body += ("Two quick picks" if (has_bottle and has_food) else "A quick note") + f" before you board Exclusives PH - Manila Bay:\n\n"
        if text_sections:
            text_body += "\n\n".join(text_sections) + "\n\n"
        if reply_what:
            text_body += (
                f"Reply to this email with {reply_what} by {RSVP_DEADLINE}. "
                f"If we don't hear from you by then, these will be assigned based on availability.\n\n"
            )
        text_body += (
            f"** BRING YOUR OWN CLEAN SLIPPERS ** -- advisable for onboard comfort.\n\n"
            f"Check-in 8:00pm - Manila Yacht Club. See you on board!\n\n"
            f"Exclusives PH"
        )

        if has_bottle and has_food:
            subject = f"Choose your bottle & food — reply by {RSVP_DEADLINE.split(',')[0]}"
        elif has_bottle:
            subject = f"Choose your bottle — reply by {RSVP_DEADLINE.split(',')[0]}"
        elif has_food:
            subject = f"Choose your food — reply by {RSVP_DEADLINE.split(',')[0]}"
        else:
            subject = "You're confirmed — Exclusives PH"

        msg = EmailMessage()
        msg['To'] = to_email
        msg['From'] = os.environ.get("SENDER_EMAIL", "your-email@gmail.com")
        msg['Subject'] = subject
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype='html')

        html_part = msg.get_payload()[1]
        if logo_bytes:
            html_part.add_related(logo_bytes, maintype='image', subtype='png', cid=logo_msgid, filename='logo.png', disposition='inline')
        if bottle_bytes:
            html_part.add_related(bottle_bytes, maintype='image', subtype='jpeg', cid=bottle_msgid, filename='bottle-poster.jpg', disposition='inline')
        if food_bytes:
            html_part.add_related(food_bytes, maintype='image', subtype='jpeg', cid=food_msgid, filename='food-poster.jpg', disposition='inline')

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Successfully sent event details email to {to_email} (package={package_name})")
    except Exception as e:
        print(f"ERROR sending event details email to {to_email}: {str(e)}")


def send_pending_reminder_email(to_email, guest_name, package_name, guests, total_amount):
    """Sends a friendly reminder to complete payment for a pending reservation."""
    try:
        service = get_gmail_service()
        logo_bytes = None if LOGO_URL else _load_logo_bytes()
        logo_msgid = None
        logo_cid = None
        if logo_bytes:
            logo_msgid = make_msgid(domain="exclusivesph")
            logo_cid = logo_msgid[1:-1]

        guest_word = "guest" if str(guests) == "1" else "guests"

        if LOGO_URL:
            wordmark_html = f"""
          <img src="{LOGO_URL}" alt="Exclusives PH" width="200" style="display:block; width:200px; max-width:60%; height:auto; border:0; margin:0 auto;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-muted" style="font-family:'Courier New', monospace; font-size:9px; letter-spacing:3px; color:#8AA0AD; text-transform:uppercase; margin-top:10px;">Manila Bay &middot; Yacht Sessions</div>
          </div></div>"""
        elif logo_cid:
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

        html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<style>
  :root {{ color-scheme: dark; supported-color-schemes: dark; }}
  u + .body .gmail-blend-screen     {{ background:#000000; mix-blend-mode:screen; }}
  u + .body .gmail-blend-difference {{ background:#000000; mix-blend-mode:difference; }}
  u + .body .text-gold  {{ color:#F4BA00 !important; border-color:#F4BA00 !important; }}
  u + .body .text-cream {{ color:#F1E6D3 !important; }}
  u + .body .text-muted {{ color:#828D96 !important; }}
  u + .body .text-amount {{ color:#FFE000 !important; }}
  @media (prefers-color-scheme: dark) {{
    .body-bg {{ background-color:#0A1A24 !important; }}
    .card-edge {{ background-color:#60602D !important; }}
    .card-bg {{ background-color:#102A38 !important; }}
    .amount-box-bg {{ background-color:#0A1A24 !important; }}
    .text-cream {{ color:#F2EADD !important; }}
    .text-muted {{ color:#8AA0AD !important; }}
    .text-gold  {{ color:#F5C518 !important; }}
    .text-amount {{ color:#FFE000 !important; }}
  }}
  [data-ogsc] .amount-box-bg, [data-ogsb] .amount-box-bg {{ background-color:#0A1A24 !important; }}
  [data-ogsc] .text-amount, [data-ogsb] .text-amount {{ color:#FFE000 !important; }}
</style>
</head>
<body class="body body-bg" style="margin:0; padding:0; background-color:#0A1A24; font-family:Arial, Helvetica, sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="body-bg" style="background-color:#0A1A24; background-image:linear-gradient(#0A1A24,#0A1A24); padding:32px 12px;">
<tr><td align="center">
  <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%;">
    <tr><td align="center" style="padding-bottom:28px;">{wordmark_html}</td></tr>
    <tr><td class="card-edge" style="background-color:#60602D; background-image:linear-gradient(#60602D,#60602D); border-radius:24px; padding:1px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="card-bg" style="background-color:#102A38; background-image:linear-gradient(#102A38,#102A38); border-radius:23px;">
        <tr><td align="center" style="padding:36px 32px 24px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-gold" style="font-family:'Courier New', monospace; font-size:11px; letter-spacing:3px; color:#F5C518; text-transform:uppercase; margin-bottom:12px;">Payment Reminder</div>
            <div class="text-cream" style="font-family:Georgia, 'Times New Roman', serif; font-size:26px; color:#F2EADD; letter-spacing:-0.5px;">Complete Your Reservation</div>
            <div class="text-muted" style="font-size:13px; color:#8AA0AD; line-height:1.6; padding:16px 8px 0 8px;">
              Hi {guest_name}, we noticed you requested a spot for <strong>{package_name}</strong> ({guests} {guest_word}) but haven't uploaded your payment receipt yet. Since our guestlist is strictly capped, unpaid holds are released automatically.
            </div>
          </div></div>
        </td></tr>
        <tr><td style="padding:0 32px 28px 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="amount-box-bg" style="background-color:#0A1A24; background-image:linear-gradient(#0A1A24,#0A1A24); border:1px solid #1E3744; border-radius:16px;">
            <tr><td align="center" style="padding:20px;">
              <div class="gmail-blend-screen"><div class="gmail-blend-difference">
                <div class="text-muted" style="font-family:'Courier New', monospace; font-size:10px; letter-spacing:2px; color:#8AA0AD; text-transform:uppercase;">Amount Due</div>
                <div class="text-amount" style="font-family:Georgia, 'Times New Roman', serif; font-size:28px; color:#FFE000; font-weight:bold; margin-top:4px;">₱{total_amount:,}</div>
              </div></div>
            </td></tr>
          </table>
        </td></tr>
        <tr><td align="center" style="padding:0 32px 36px 32px;">
          <div class="gmail-blend-screen"><div class="gmail-blend-difference">
            <div class="text-muted" style="font-size:12px; color:#8AA0AD; line-height:1.6;">
              To finalize your boarding pass, please complete your transfer via GCash or Bank Transfer and upload your screenshot on our website. Guestlist only &mdash; no walk-ins.
            </div>
          </div></div>
        </td></tr>
      </table>
    </td></tr>
    <tr><td align="center" style="padding:28px 20px 8px 20px;">
      <div class="gmail-blend-screen"><div class="gmail-blend-difference">
        <div class="text-muted" style="font-size:11px; color:#8AA0AD; line-height:1.7;">
          Exclusives PH &middot; Manila Yacht Club, CCP Complex, Roxas Blvd, Malate, Manila<br>
          Questions? Reply directly to this email.
        </div>
      </div></div>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""

        text_body = (
            f"Hi {guest_name},\n\n"
            f"We noticed you requested a booking for '{package_name}' ({guests} {guest_word}) but haven't uploaded your payment receipt yet.\n\n"
            f"Amount Due: ₱{total_amount:,}\n\n"
            f"Since spots are strictly capped, unpaid reservations are released automatically. To secure your spot, please transfer your payment and upload your receipt on our website. Guestlist only - no walk-ins.\n\n"
            f"Exclusives PH"
        )

        msg = EmailMessage()
        msg['To'] = to_email
        msg['From'] = os.environ.get("SENDER_EMAIL", "your-email@gmail.com")
        msg['Subject'] = "Action Required: Complete your Exclusives PH reservation"
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype='html')

        if logo_bytes:
            html_part = msg.get_payload()[1]
            html_part.add_related(logo_bytes, maintype='image', subtype='png', cid=logo_msgid, filename='logo.png', disposition='inline')

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={'raw': raw}).execute()
        print(f"Successfully sent pending reminder email to {to_email}")
    except Exception as e:
        print(f"ERROR sending reminder email to {to_email}: {str(e)}")