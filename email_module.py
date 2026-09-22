# ============================================================================
#  QR + HTML EMAIL — Exclusives PH boarding pass & reminder system
#  Theme: "The Upside Down Yacht Party" (Halloween 2026) — matches index.html
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
#      both:  background-color:X; background-image:linear-gradient(X,X);
#      The gradient paints over Gmail's inverted colour, so the surface survives.
#      (The red→cyan accent rule under each heading is a real gradient, so it
#      survives for the same reason.)
#
#   2. BLEND-MODE TEXT FIX (Rémi Parmentier). Locking backgrounds does nothing
#      for text — Gmail still inverts `color`. Gmail replaces the doctype with a
#      <u></u> element, so `u + .body` is a Gmail-only CSS hook. Nesting a
#      mix-blend-mode:difference div inside a mix-blend-mode:screen div (both on
#      black) mathematically cancels Gmail's inversion back out. In every other
#      client the selector never matches, the divs are inert, nothing changes.
#      => Every TEXT block is wrapped with _t() (screen > difference).
#
#   3. IMAGES (QR, LOGO, POSTERS) STAY OUTSIDE THE BLEND WRAPPERS. Gmail does
#      not invert images, so the blend maths would invert the image instead.
#      For the QR this breaks scannability.
#
#  Consequences for anyone editing the template:
#   - Any element with its OWN background: gradient-locked, OUTSIDE _t().
#   - Any element that is only TEXT: INSIDE _t().
#   - Any IMAGE: OUTSIDE _t().
#   - No rgba() anywhere — every translucent site colour is pre-flattened to hex.
#   - Borders can't be gradient-locked, so card/panel borders are faked with a
#     1px-padding wrapper whose background is gradient-locked (see _panel()).
#   - The `u + .body .text-*` colours are slightly darker than the real ones.
#     That's deliberate compensation for the blend maths (same curve as the
#     previous gold theme). Test in Gmail iOS/Android after changing any colour.
#
#  Known limitation: Gmail Android with a non-Google account ("GANGA") strips
#  <style> blocks entirely, so the blend fix can't load there and the email will
#  still invert. No fix exists for that client. Everything else is covered.
# ============================================================================
import os, io, json, base64
from html import escape
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

# ---- Event facts — keep in sync with index.html -----------------------------
EVENT_DATE_LONG = "Friday, October 30, 2026"
EVENT_DATE_SHORT = "Oct 30, 2026 &middot; 8:00 PM"
EVENT_DATE_SHORT_TEXT = "Oct 30, 2026 - 8:00 PM"
EVENT_CHECKIN_TIME = "8:00 PM"
EVENT_DECK_UNTIL = "3:00 AM"
EVENT_VENUE_LINE = "Manila Yacht Club, CCP Complex, Roxas Boulevard, Malate, Manila"
EVENT_TAGLINE = "The Upside Down Yacht Party"
DRESS_CODE_HTML = "Black or your best Stranger Things / 80s / Halloween costume. No shorts of any kind."
DRESS_CODE_TEXT = "Black or your best Stranger Things / 80s / Halloween costume. No shorts of any kind."

# Reply-by deadline for the bottle/food picks (2 days before the event).
RSVP_DEADLINE = "October 28, 2026"

# Path to the logo file used in the email header. Override with LOGO_PATH if
# your deploy layout differs from the frontend's images/ folder.
LOGO_PATH = os.environ.get("LOGO_PATH", "images/logo.png")
BOTTLE_POSTER_PATH = os.environ.get("BOTTLE_POSTER_PATH", "images/bottle-poster.jpg")
FOOD_POSTER_PATH = os.environ.get("FOOD_POSTER_PATH", "images/food-poster.jpg")
PARKING_POSTER_PATH = os.environ.get("PARKING_POSTER_PATH", "images/parking-poster.jpg")
HOUSE_RULES_POSTER_PATH = os.environ.get("HOUSE_RULES_POSTER_PATH", "images/house-rules-poster.jpg")

# If set, the logo is referenced as a normal hosted <img src="..."> instead of
# being embedded as an inline cid: MIME part (avoids Gmail listing it in the
# attachment strip). Falls back to inline cid embedding (LOGO_PATH) if empty.
LOGO_URL = "https://gvtutnofyrkmyyclfshe.supabase.co/storage/v1/object/public/assets/logo.png"


# ---- Palette (flattened from the site's Tailwind brand colours) -------------
VOID = "#030305"        # page background (brand.void)
CARD = "#0B0A1A"        # main card (brand.upside)
PANEL = "#131122"       # inner panels (brand.slate)
RED = "#E50914"         # brand.neonRed — borders, accent rule
RED_TEXT = "#FF2A36"    # slightly brighter red for readable text on dark
BLUE = "#1CE6FF"        # brand.neonBlue
ICE = "#F8F8FF"         # brand.ice
MUTED = "#8B8DA6"       # brand.muted, nudged up for contrast on CARD
EDGE_BLUE = "#17667A"   # neonBlue @ 40% over PANEL
EDGE_RED = "#7C0D1B"    # neonRed @ 50% over PANEL
EDGE_LINE = "#2A2640"   # neutral hairline border
RULE = "#1F1C33"        # row separators
DASH = "#4A0E14"        # dashed ticket tear line
QR_BG = "#FFFFFE"

MONO = "'VT323','Courier New',Courier,monospace"
SERIF = "'Playfair Display',Georgia,'Times New Roman',serif"
SANS = "Arial,Helvetica,sans-serif"

_HEAD = f"""<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title></title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700&family=VT323&display=swap" rel="stylesheet">
<style>
  :root {{ color-scheme: dark; supported-color-schemes: dark; }}
  u + .body .gmail-blend-screen     {{ background:#000000; mix-blend-mode:screen; }}
  u + .body .gmail-blend-difference {{ background:#000000; mix-blend-mode:difference; }}
  u + .body .text-red   {{ color:#FF1B25 !important; border-color:#FF1B25 !important; }}
  u + .body .text-blue  {{ color:#10E0FF !important; }}
  u + .body .text-ice   {{ color:#F6F6FF !important; }}
  u + .body .text-muted {{ color:#777A95 !important; }}
  @media (prefers-color-scheme: dark) {{
    .body-bg    {{ background-color:{VOID} !important; }}
    .card-edge  {{ background-color:{RED} !important; }}
    .card-bg    {{ background-color:{CARD} !important; }}
    .qr-card-bg {{ background-color:{QR_BG} !important; }}
    .edge-blue  {{ background-color:{EDGE_BLUE} !important; }}
    .edge-red   {{ background-color:{EDGE_RED} !important; }}
    .edge-line  {{ background-color:{EDGE_LINE} !important; }}
    .panel-bg   {{ background-color:{PANEL} !important; }}
    .text-red   {{ color:{RED_TEXT} !important; }}
    .text-blue  {{ color:{BLUE} !important; }}
    .text-ice   {{ color:{ICE} !important; }}
    .text-muted {{ color:{MUTED} !important; }}
    a           {{ color:{BLUE} !important; }}
  }}
  [data-ogsc] .body-bg,    [data-ogsb] .body-bg    {{ background-color:{VOID} !important; }}
  [data-ogsc] .card-edge,  [data-ogsb] .card-edge  {{ background-color:{RED} !important; }}
  [data-ogsc] .card-bg,    [data-ogsb] .card-bg    {{ background-color:{CARD} !important; }}
  [data-ogsc] .qr-card-bg, [data-ogsb] .qr-card-bg {{ background-color:{QR_BG} !important; }}
  [data-ogsc] .edge-blue,  [data-ogsb] .edge-blue  {{ background-color:{EDGE_BLUE} !important; }}
  [data-ogsc] .edge-red,   [data-ogsb] .edge-red   {{ background-color:{EDGE_RED} !important; }}
  [data-ogsc] .edge-line,  [data-ogsb] .edge-line  {{ background-color:{EDGE_LINE} !important; }}
  [data-ogsc] .panel-bg,   [data-ogsb] .panel-bg   {{ background-color:{PANEL} !important; }}
  [data-ogsc] .text-red,   [data-ogsb] .text-red   {{ color:{RED_TEXT} !important; }}
  [data-ogsc] .text-blue,  [data-ogsb] .text-blue  {{ color:{BLUE} !important; }}
  [data-ogsc] .text-ice,   [data-ogsb] .text-ice   {{ color:{ICE} !important; }}
  [data-ogsc] .text-muted, [data-ogsb] .text-muted {{ color:{MUTED} !important; }}
</style>"""


# ============================================================================
#  Gmail + asset helpers
# ============================================================================
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
    """PNG QR encoding the ticket_code.

    border=4 is the spec-mandated minimum quiet zone. Solid white background
    (never transparent) so dark-mode clients can't paint behind it.
    """
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color=VOID, back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _load_image_bytes(path: str, label: str) -> bytes | None:
    """Returns None on a missing file so the email still sends without that image."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        print(f"WARNING: {label} not found at {path}; sending email without it.")
        return None


def _load_logo_bytes() -> bytes | None:
    return _load_image_bytes(LOGO_PATH, "logo")


def _new_cid():
    msgid = make_msgid(domain="exclusivesph")
    return msgid, msgid[1:-1]


def _prepare_logo():
    """(bytes, msgid, cid) for an inline logo, or (None, None, None) when LOGO_URL is used."""
    if LOGO_URL:
        return None, None, None
    logo_bytes = _load_logo_bytes()
    if not logo_bytes:
        return None, None, None
    msgid, cid = _new_cid()
    return logo_bytes, msgid, cid


def _send(to_email, subject, text_body, html_body, related=()):
    """related: iterable of (bytes, subtype, msgid, filename) inline images."""
    service = get_gmail_service()
    msg = EmailMessage()
    msg['To'] = to_email
    msg['From'] = os.environ.get("SENDER_EMAIL", "your-email@gmail.com")
    msg['Subject'] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype='html')

    related = [r for r in related if r and r[0]]
    if related:
        html_part = msg.get_payload()[1]
        for data, subtype, msgid, filename in related:
            html_part.add_related(data, maintype='image', subtype=subtype, cid=msgid,
                                  filename=filename, disposition='inline')

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={'raw': raw}).execute()


# ============================================================================
#  Template building blocks
#  _t() = text wrapper (blend fix). Anything with a background stays outside it.
# ============================================================================
def _t(inner):
    return f'<div class="gmail-blend-screen"><div class="gmail-blend-difference">{inner}</div></div>'


def _eyebrow(text, tone="blue"):
    cls, col = ("text-red", RED_TEXT) if tone == "red" else ("text-blue", BLUE)
    return (f'<div class="{cls}" style="font-family:{MONO}; font-size:15px; letter-spacing:4px; '
            f'color:{col}; text-transform:uppercase; margin-bottom:10px;">{text}</div>')


def _heading(text):
    return (f'<div class="text-red" style="font-family:{SERIF}; font-size:28px; line-height:1.15; '
            f'font-weight:bold; color:{RED_TEXT}; text-transform:uppercase; letter-spacing:1px;">{text}</div>')


def _body(text, size=13, color_cls="text-muted", color=MUTED, extra=""):
    return (f'<div class="{color_cls}" style="font-family:{SANS}; font-size:{size}px; color:{color}; '
            f'line-height:1.6;{extra}">{text}</div>')


def _label(text, tone="blue", extra=""):
    cls, col = {"blue": ("text-blue", BLUE), "red": ("text-red", RED_TEXT),
                "muted": ("text-muted", MUTED)}[tone]
    return (f'<div class="{cls}" style="font-family:{MONO}; font-size:15px; letter-spacing:2px; '
            f'color:{col}; text-transform:uppercase;{extra}">{text}</div>')


def _accent_rule():
    """The site's short red→cyan underline. Has a background → outside _t()."""
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" align="center" style="margin:0 auto;">'
            f'<tr><td width="64" height="2" style="width:64px; height:2px; font-size:0; line-height:0; '
            f'background-color:{RED}; background-image:linear-gradient(90deg,{RED},{BLUE});">&nbsp;</td></tr></table>')


def _header_rows(eyebrow, heading, intro, eyebrow_tone="blue"):
    top = _t(_eyebrow(eyebrow, eyebrow_tone) + _heading(heading))
    intro_html = _t(_body(intro))
    return f"""
        <tr><td align="center" style="padding:36px 32px 0 32px;">{top}</td></tr>
        <tr><td align="center" style="padding:18px 32px 0 32px;">{_accent_rule()}</td></tr>
        <tr><td align="center" style="padding:18px 32px 8px 32px;">{intro_html}</td></tr>"""


_EDGES = {
    "blue": ("edge-blue", EDGE_BLUE),
    "red": ("edge-red", EDGE_RED),
    "line": ("edge-line", EDGE_LINE),
}


def _panel(inner, edge="blue", pad="16px 18px"):
    """Bordered panel. Border is faked with a gradient-locked 1px wrapper."""
    cls, col = _EDGES[edge]
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="{cls}" style="background-color:{col}; background-image:linear-gradient({col},{col}); border-radius:16px;">
            <tr><td style="padding:1px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="panel-bg" style="background-color:{PANEL}; background-image:linear-gradient({PANEL},{PANEL}); border-radius:15px;">
                <tr><td style="padding:{pad};">{_t(inner)}</td></tr>
              </table>
            </td></tr>
          </table>"""


def _row(content, pad="16px 32px 8px 32px", align=None):
    a = f' align="{align}"' if align else ""
    return f'\n        <tr><td{a} style="padding:{pad};">{content}</td></tr>'


def _dashed_row():
    return _row(_t(f'<div style="border-top:2px dashed {DASH}; font-size:0; line-height:0;">&nbsp;</div>'),
                pad="8px 32px")


def _poster_row(cid, alt, pad="22px 32px 0 32px"):
    """Poster image: outside _t(), gradient-locked hairline frame."""
    if not cid:
        return ""
    return f"""
        <tr><td align="center" style="padding:{pad};">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="edge-line" style="background-color:{EDGE_LINE}; background-image:linear-gradient({EDGE_LINE},{EDGE_LINE}); border-radius:16px;">
            <tr><td style="padding:1px; line-height:0; font-size:0;">
              <img src="cid:{cid}" alt="{alt}" width="416" style="display:block; width:100%; max-width:416px; height:auto; border:0; border-radius:15px;">
            </td></tr>
          </table>
        </td></tr>"""


def _numbered_rows(items):
    """items: list of (title_html, note_html_or_None). Returns a table (text only → goes inside _t())."""
    rows = ""
    for i, (title, note) in enumerate(items, start=1):
        note_html = _body(note, size=12, extra=" margin-top:2px;") if note else ""
        rows += f"""
              <tr>
                <td valign="top" style="padding:9px 0; width:30px;">
                  <div class="text-red" style="color:{RED_TEXT}; font-family:{MONO}; font-size:18px;">{i:02d}</div>
                </td>
                <td valign="top" style="padding:9px 0;">
                  <div class="text-ice" style="font-family:{SANS}; font-size:14px; color:{ICE}; line-height:1.5; font-weight:bold;">{title}</div>{note_html}
                </td>
              </tr>"""
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}\n            </table>'


def _detail_table(pairs):
    rows = ""
    for i, (k, v) in enumerate(pairs):
        border = "" if i == 0 else f" border-top:1px solid {RULE};"
        rows += (f'<tr><td class="text-muted" style="font-family:{MONO}; font-size:15px; color:{MUTED}; '
                 f'text-transform:uppercase; letter-spacing:1px; padding:9px 0;{border}">{k}</td>'
                 f'<td align="right" class="text-ice" style="font-family:{SANS}; font-size:13px; color:{ICE}; '
                 f'padding:9px 0;{border}">{v}</td></tr>')
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'


def _wordmark_html(logo_cid=None):
    tagline = _t(f'<div class="text-muted" style="font-family:{MONO}; font-size:15px; letter-spacing:4px; '
                 f'color:{MUTED}; text-transform:uppercase; margin-top:12px;">{EVENT_TAGLINE}</div>')
    src = LOGO_URL or (f"cid:{logo_cid}" if logo_cid else None)
    if src:
        return (f'<img src="{src}" alt="Exclusives PH" width="200" style="display:block; width:200px; '
                f'max-width:60%; height:auto; border:0; margin:0 auto;">{tagline}')
    return _t(f'<div class="text-red" style="font-family:{SERIF}; font-size:22px; letter-spacing:4px; '
              f'color:{RED_TEXT}; text-transform:uppercase; font-weight:bold;">Exclusives&nbsp;PH</div>') + tagline


def _page(card_rows, logo_cid=None, footer_extra="Questions? Reply directly to this email."):
    footer = _t(_body(
        f"Exclusives PH &middot; Halloween Party &middot; {EVENT_DATE_SHORT}<br>"
        f"{EVENT_VENUE_LINE}<br>{footer_extra}",
        size=11, extra=" line-height:1.7;"))
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
{_HEAD}
</head>
<body class="body body-bg" style="margin:0; padding:0; background-color:{VOID}; font-family:{SANS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="body-bg" style="background-color:{VOID}; background-image:linear-gradient({VOID},{VOID}); padding:32px 12px;">
<tr><td align="center">
  <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%;">
    <tr><td align="center" style="padding-bottom:28px;">{_wordmark_html(logo_cid)}</td></tr>
    <tr><td class="card-edge" style="background-color:{RED}; background-image:linear-gradient({RED},{RED}); border-radius:24px; padding:1px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="card-bg" style="background-color:{CARD}; background-image:linear-gradient({CARD},{CARD}); border-radius:23px;">
{card_rows}
        <tr><td style="padding:0 0 28px 0; font-size:0; line-height:0;">&nbsp;</td></tr>
      </table>
    </td></tr>
    <tr><td align="center" style="padding:28px 20px 8px 20px;">{footer}</td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


# ============================================================================
#  1. Boarding pass (approval)
# ============================================================================
def _build_email_html(guest_name, ticket_code, package_name, guests, table_id, qr_cid, logo_cid=None):
    name = escape(str(guest_name))
    guest_word = "guest" if str(guests) == "1" else "guests"

    rows = _header_rows(
        "Clearance granted",
        "You're on the list",
        f"Hi {name}, your payment is verified and your booking is confirmed. "
        f"Show the QR code below at the Manila Yacht Club gate to board.",
    )

    # QR: image → outside _t(), white gradient-locked card behind it.
    rows += f"""
        <tr><td align="center" style="padding:24px 32px 8px 32px;">
          <table role="presentation" cellpadding="0" cellspacing="0" class="qr-card-bg" style="background-color:{QR_BG}; background-image:linear-gradient({QR_BG},{QR_BG}); border-radius:18px;">
            <tr><td align="center" style="padding:16px;">
              <img src="cid:{qr_cid}" alt="Boarding QR code" width="180" height="180" style="display:block; width:180px; height:180px; border:0;">
            </td></tr>
          </table>
        </td></tr>"""
    rows += _row(_t(_label("Scan at the gate", "muted", " text-align:center;")), pad="8px 32px 0 32px", align="center")
    rows += _row(_t(f'<div class="text-red" style="font-family:{MONO}; font-size:34px; letter-spacing:4px; '
                    f'color:{RED_TEXT}; text-align:center;">{escape(str(ticket_code))}</div>'),
                 pad="4px 32px 20px 32px", align="center")
    rows += _dashed_row()
    rows += _row(_t(_detail_table([
        ("Passenger", f"<strong>{name}</strong>"),
        ("Ticket type", escape(str(package_name))),
        ("Guests", f"{escape(str(guests))} {guest_word}"),
        ("Table / spot", escape(_spot_label(table_id))),
        ("Date", EVENT_DATE_SHORT),
        ("Boarding", "Happy Life Yacht &middot; Manila Yacht Club"),
    ])), pad="16px 32px 8px 32px")
    rows += _row(_panel(
        _body(f'<span class="text-blue" style="color:{BLUE}; font-weight:bold;">{EVENT_DATE_LONG}.</span> '
              f'Check-in opens {EVENT_CHECKIN_TIME}. {DRESS_CODE_HTML} Strictly 18+, guestlist only &mdash; no walk-ins.',
              size=12),
        edge="blue"), pad="16px 32px 8px 32px")

    return _page(rows, logo_cid, "Questions? Reply to this email or reach us at exclusives.est2023@gmail.com")


def send_approval_email(to_email, guest_name, ticket_code, package_name, guests=1, table_id=None):
    """Build + send the branded confirmation email with an inline QR code."""
    try:
        qr_msgid, qr_cid = _new_cid()
        logo_bytes, logo_msgid, logo_cid = _prepare_logo()

        qr_png = _make_qr_png(ticket_code)
        html_body = _build_email_html(guest_name, ticket_code, package_name, guests, table_id, qr_cid, logo_cid)

        text_body = (
            f"Hi {guest_name},\n\n"
            f"CLEARANCE GRANTED - your payment is verified and your booking for '{package_name}' is confirmed.\n\n"
            f"Ticket code: {ticket_code}\n"
            f"Guests: {guests}\n"
            f"Table / Spot: {_spot_label(table_id)}\n"
            f"Date: {EVENT_DATE_LONG} - {EVENT_CHECKIN_TIME}\n"
            f"Boarding: Happy Life Yacht, Manila Yacht Club. Check-in opens {EVENT_CHECKIN_TIME}.\n\n"
            f"Dress code: {DRESS_CODE_TEXT}\n\n"
            f"Present your QR code (in the HTML version of this email) at the gate.\n\n"
            f"Strictly 18+. Guestlist only - no walk-ins.\n\n"
            f"Exclusives PH"
        )

        _send(to_email,
              "Clearance granted — your Exclusives PH boarding pass",
              text_body, html_body,
              related=[(qr_png, 'png', qr_msgid, 'ticket-qr.png'),
                       (logo_bytes, 'png', logo_msgid, 'logo.png')])
        print(f"Successfully sent confirmation email to {to_email}")
    except Exception as e:
        print(f"ERROR sending email to {to_email}: {str(e)}")


# ============================================================================
#  2. Event details (bottle / food picks)
# ============================================================================
# bottle_count: how many complimentary bottles this tier picks (0 = none).
# food: whether a complimentary food pick is offered.
PACKAGE_TIER_CONFIG = {
    "SVIP Couch":           {"bottle_count": 2, "food": True},
    "Indoor Couch":         {"bottle_count": 1, "food": True},
    "Outdoor Couch":        {"bottle_count": 1, "food": True},
    "6-Pax Bottle Bundle":  {"bottle_count": 1, "food": False},
    "Standing Table":       {"bottle_count": 0, "food": False},
    "Entrance Fee":         {"bottle_count": 0, "food": False},
}

BOTTLE_OPTIONS = [
    ("Absolut Vodka", "Sprite"),
    ("Johnnie Walker Black Label", "Coca-Cola"),
    ("J\u00e4germeister", "Red Bull"),
]
FOOD_OPTIONS = ["Nachos", "Tacos", "Charcuterie Board"]

FOOTWEAR_HTML = (
    f'Wear <strong>shoes</strong> to arrive &mdash; slippers aren\'t allowed at the club gate. '
    f'Once aboard, change into your own <strong>brand-new or like-new slippers</strong> for use inside the yacht.'
)


def send_event_details_email(to_email, guest_name, package_name=None, table_id=None):
    """Pre-event details email. Content depends on package tier (PACKAGE_TIER_CONFIG).
    Unknown package → no bottle/food section (never over-promise a perk)."""
    try:
        cfg = PACKAGE_TIER_CONFIG.get(package_name, {"bottle_count": 0, "food": False})
        bottle_count = cfg["bottle_count"]
        has_bottle = bottle_count > 0
        has_food = cfg["food"]
        name = escape(str(guest_name))

        logo_bytes, logo_msgid, logo_cid = _prepare_logo()

        bottle_bytes = bottle_msgid = bottle_cid = None
        if has_bottle:
            bottle_bytes = _load_image_bytes(BOTTLE_POSTER_PATH, "bottle poster")
            if bottle_bytes:
                bottle_msgid, bottle_cid = _new_cid()

        food_bytes = food_msgid = food_cid = None
        if has_food:
            food_bytes = _load_image_bytes(FOOD_POSTER_PATH, "food poster")
            if food_bytes:
                food_msgid, food_cid = _new_cid()

        bottle_heading = "Your complimentary bottles &mdash; pick 2" if bottle_count == 2 else "Your complimentary bottle"

        if has_bottle and has_food:
            heading = "Choose your bottle &amp; food"
            intro_line = "two quick picks before you step into the Upside Down:"
            reply_what = "your two bottle selections and food selection" if bottle_count == 2 else "your bottle and food selections"
        elif has_bottle:
            heading = "Choose your bottles" if bottle_count == 2 else "Choose your bottle"
            intro_line = ("your two complimentary bottle picks before you board:" if bottle_count == 2
                          else "your complimentary bottle pick before you board:")
            reply_what = "your two bottle selections" if bottle_count == 2 else "your bottle selection"
        elif has_food:
            heading = "Choose your food"
            intro_line = "your complimentary food pick before you board:"
            reply_what = "your food selection"
        else:
            heading = "You're confirmed"
            intro_line = "you're all set to board. A few things to know before the night:"
            reply_what = None

        rows = _header_rows("Before you board", heading, f"Hi {name}, {intro_line}")

        if has_bottle:
            rows += _poster_row(bottle_cid, "Choose your complimentary bottle: Absolut Vodka, Johnnie Walker Black Label, or J\u00e4germeister")
            bottle_items = [(f'{n} <span class="text-muted" style="color:{MUTED}; font-weight:normal;">&mdash; with {m}</span>', None)
                            for n, m in BOTTLE_OPTIONS]
            rows += _row(_t(_label(bottle_heading, "blue", " margin-bottom:4px;") + _numbered_rows(bottle_items)),
                         pad="22px 32px 4px 32px")

        if has_bottle and has_food:
            rows += _dashed_row()

        if has_food:
            rows += _poster_row(food_cid, "Choose one complimentary food item: Nachos, Tacos, or Charcuterie Board", pad="20px 32px 0 32px")
            rows += _row(_t(_label("Your complimentary food item", "blue", " margin-bottom:4px;") +
                            _numbered_rows([(f, None) for f in FOOD_OPTIONS])),
                         pad="20px 32px 4px 32px")

        if reply_what:
            rows += _row(_panel(_body(
                f'<span class="text-blue" style="color:{BLUE}; font-weight:bold;">Reply to this email</span> '
                f'with {reply_what} by <span class="text-blue" style="color:{BLUE}; font-weight:bold;">{RSVP_DEADLINE}</span>. '
                f"If we don't hear from you by then, these will be assigned based on availability.", size=12),
                edge="blue"), pad="20px 32px 8px 32px")

        rows += _row(_panel(
            _label("Footwear", "red", " margin-bottom:6px;") + _body(FOOTWEAR_HTML, size=13, color_cls="text-ice", color=ICE),
            edge="red"), pad="8px 32px 8px 32px")

        rows += _row(_t(_body(f"Check-in opens {EVENT_CHECKIN_TIME} &middot; Manila Yacht Club. See you on the other side.",
                              size=12, extra=f" border-top:1px solid {RULE}; padding-top:16px;")),
                     pad="16px 32px 8px 32px")

        html_body = _page(rows, logo_cid)

        text_sections = []
        if has_bottle:
            bottle_text = "\n".join(f"{i}. {n} - with {m}" for i, (n, m) in enumerate(BOTTLE_OPTIONS, start=1))
            label = "YOUR COMPLIMENTARY BOTTLES (PICK 2)" if bottle_count == 2 else "YOUR COMPLIMENTARY BOTTLE"
            text_sections.append(f"{label}\n{bottle_text}")
        if has_food:
            food_text = "\n".join(f"{i}. {item}" for i, item in enumerate(FOOD_OPTIONS, start=1))
            text_sections.append(f"YOUR COMPLIMENTARY FOOD ITEM\n{food_text}")

        text_body = f"Hi {guest_name},\n\n"
        text_body += ("Two quick picks" if (has_bottle and has_food) else "A quick note") + " before you board Exclusives PH - Halloween Party:\n\n"
        if text_sections:
            text_body += "\n\n".join(text_sections) + "\n\n"
        if reply_what:
            text_body += (f"Reply to this email with {reply_what} by {RSVP_DEADLINE}. "
                          f"If we don't hear from you by then, these will be assigned based on availability.\n\n")
        text_body += (
            "FOOTWEAR: Wear shoes to arrive - slippers aren't allowed at the club gate. "
            "Once aboard, change into your own brand-new or like-new slippers for use inside the yacht.\n\n"
            f"Check-in opens {EVENT_CHECKIN_TIME} - Manila Yacht Club. See you on the other side.\n\n"
            "Exclusives PH"
        )

        deadline_short = RSVP_DEADLINE.split(',')[0]
        if has_bottle and has_food:
            subject = f"Choose your bottle & food — reply by {deadline_short}"
        elif has_bottle:
            subject = f"Choose your bottle — reply by {deadline_short}"
        elif has_food:
            subject = f"Choose your food — reply by {deadline_short}"
        else:
            subject = "You're confirmed — Exclusives PH Halloween Party"

        _send(to_email, subject, text_body, html_body, related=[
            (logo_bytes, 'png', logo_msgid, 'logo.png'),
            (bottle_bytes, 'jpeg', bottle_msgid, 'bottle-poster.jpg'),
            (food_bytes, 'jpeg', food_msgid, 'food-poster.jpg'),
        ])
        print(f"Successfully sent event details email to {to_email} (package={package_name})")
    except Exception as e:
        print(f"ERROR sending event details email to {to_email}: {str(e)}")


# ============================================================================
#  3. Pending payment reminder
# ============================================================================
def send_pending_reminder_email(to_email, guest_name, package_name, guests, total_amount):
    """Friendly reminder to complete payment for a pending reservation."""
    try:
        logo_bytes, logo_msgid, logo_cid = _prepare_logo()
        guest_word = "guest" if str(guests) == "1" else "guests"
        name = escape(str(guest_name))
        pkg = escape(str(package_name))

        rows = _header_rows(
            "Payment reminder",
            "Complete your reservation",
            f"Hi {name}, you requested a spot for <strong>{pkg}</strong> ({escape(str(guests))} {guest_word}) "
            f"but we haven't received your payment receipt yet. The manifest is capped at 120, so unpaid holds are released automatically.",
            eyebrow_tone="red",
        )
        rows += _row(_panel(
            _label("Amount due", "muted", " text-align:center;") +
            f'<div class="text-red" style="font-family:{SERIF}; font-size:32px; color:{RED_TEXT}; font-weight:bold; '
            f'text-align:center; margin-top:4px;">&#8369;{total_amount:,}</div>',
            edge="red", pad="20px"), pad="16px 32px 8px 32px")
        rows += _row(_t(_body(
            "To get your boarding pass, send your payment via GCash or Maribank and upload the screenshot on our website. "
            "Guestlist only &mdash; no walk-ins.", size=12, extra=" text-align:center;")),
            pad="16px 32px 8px 32px", align="center")

        html_body = _page(rows, logo_cid)

        text_body = (
            f"Hi {guest_name},\n\n"
            f"You requested a booking for '{package_name}' ({guests} {guest_word}) but we haven't received your payment receipt yet.\n\n"
            f"Amount Due: ₱{total_amount:,}\n\n"
            f"The manifest is capped at 120, so unpaid holds are released automatically. To secure your spot, "
            f"send your payment via GCash or Maribank and upload the screenshot on our website. Guestlist only - no walk-ins.\n\n"
            f"Exclusives PH"
        )

        _send(to_email, "Action required: complete your Exclusives PH reservation", text_body, html_body,
              related=[(logo_bytes, 'png', logo_msgid, 'logo.png')])
        print(f"Successfully sent pending reminder email to {to_email}")
    except Exception as e:
        print(f"ERROR sending reminder email to {to_email}: {str(e)}")


# ============================================================================
#  4. Parking info
# ============================================================================
# Nearby lots, closest first, with approximate walk time to Manila Yacht Club.
PARKING_OPTIONS = [
    ("Harbour Square", "~8 min walk", "Closest option — fills up fastest, so head here first."),
    ("The Aristocrat Restaurant", "~11-12 min walk", "Reliable overflow option a few minutes further down Roxas Blvd."),
    ("Aquasphere Public Parking Lot", "~13 min walk", "Furthest of the three, but rarely full."),
]


def send_parking_info_email(to_email, guest_name):
    """Parking guidance for a confirmed guest — three nearby lots, closest first."""
    try:
        logo_bytes, logo_msgid, logo_cid = _prepare_logo()
        name = escape(str(guest_name))

        parking_bytes = _load_image_bytes(PARKING_POSTER_PATH, "parking poster")
        parking_msgid = parking_cid = None
        if parking_bytes:
            parking_msgid, parking_cid = _new_cid()

        rows = _header_rows(
            "Where to park",
            "Parking is limited at the club",
            f"Hi {name}, on-site parking at Manila Yacht Club is very limited. We recommend one of these nearby lots instead:",
        )
        rows += _poster_row(parking_cid, "Map showing nearby parking lots and walk times to Manila Yacht Club")
        items = [(f'{n} <span class="text-blue" style="color:{BLUE}; font-weight:normal; font-family:{MONO}; font-size:15px;">&middot; {w}</span>', note)
                 for n, w, note in PARKING_OPTIONS]
        rows += _row(_t(_numbered_rows(items)), pad="16px 32px 4px 32px")
        rows += _row(_panel(_body(
            "Walk times are approximate, measured from each lot to the club entrance along Roxas Blvd.", size=12),
            edge="blue"), pad="16px 32px 8px 32px")
        rows += _row(_t(_body(f"Check-in opens {EVENT_CHECKIN_TIME} &middot; Manila Yacht Club. See you on the other side.",
                              size=12, extra=f" border-top:1px solid {RULE}; padding-top:16px;")),
                     pad="16px 32px 8px 32px")

        html_body = _page(rows, logo_cid)

        parking_text = "\n".join(f"{i}. {n} ({w}) - {note}" for i, (n, w, note) in enumerate(PARKING_OPTIONS, start=1))
        text_body = (
            f"Hi {guest_name},\n\n"
            f"WHERE TO PARK\n"
            f"On-site parking at Manila Yacht Club is very limited. We recommend one of these nearby lots instead:\n\n"
            f"{parking_text}\n\n"
            f"Walk times are approximate, measured from each lot to the club entrance along Roxas Blvd.\n\n"
            f"Check-in opens {EVENT_CHECKIN_TIME} - Manila Yacht Club. See you on the other side.\n\n"
            f"Exclusives PH"
        )

        _send(to_email, "Where to park — Exclusives PH Halloween Party", text_body, html_body, related=[
            (logo_bytes, 'png', logo_msgid, 'logo.png'),
            (parking_bytes, 'jpeg', parking_msgid, 'parking-poster.jpg'),
        ])
        print(f"Successfully sent parking info email to {to_email}")
    except Exception as e:
        print(f"ERROR sending parking info email to {to_email}: {str(e)}")


# ============================================================================
#  5. Final (day-of) reminder
# ============================================================================
def send_final_reminder_email(to_email, guest_name, package_name=None, table_id=None):
    """Day-of reminder: check-in, footwear rule, dress code, what to bring, no-transfer policy."""
    try:
        logo_bytes, logo_msgid, logo_cid = _prepare_logo()
        name = escape(str(guest_name))

        rules_bytes = _load_image_bytes(HOUSE_RULES_POSTER_PATH, "house rules poster")
        rules_msgid = rules_cid = None
        if rules_bytes:
            rules_msgid, rules_cid = _new_cid()

        spot_line = _spot_label(table_id)
        pkg_display = escape(str(package_name)) if package_name else "Confirmed booking"
        spot_suffix = f" &middot; {escape(spot_line)}" if table_id else ""

        CHECKLIST = [
            ("Valid government ID", "18+, no exceptions."),
            ("Your boarding pass", "Printed or on your phone — the QR code from your confirmation email."),
            ("Your costume", DRESS_CODE_HTML),
        ]

        rows = _header_rows(
            "Final reminder",
            "See you tonight",
            f"Hi {name}, the breach opens in a few hours. Here's everything you need before you board.",
            eyebrow_tone="red",
        )
        rows += _row(_panel(
            _label("Check-in", "blue") +
            f'<div class="text-ice" style="font-family:{SERIF}; font-size:20px; color:{ICE}; margin-top:4px; font-weight:bold;">'
            f'{EVENT_CHECKIN_TIME} &middot; gate opens</div>' +
            _body(f"{EVENT_VENUE_LINE}. Arrive 20&ndash;30 minutes early &mdash; the guestlist is checked name by name. "
                  f"The party runs until {EVENT_DECK_UNTIL}.", size=12, extra=" margin-top:6px;") +
            _body(f'Your spot: <span class="text-blue" style="color:{BLUE};">{pkg_display}</span>{spot_suffix}',
                  size=12, extra=f" margin-top:10px; padding-top:10px; border-top:1px solid {RULE};"),
            edge="line", pad="18px 20px"), pad="16px 32px 4px 32px")

        rows += _row(_panel(
            _label("Footwear &mdash; read this", "red", " margin-bottom:6px;") +
            _body("Wear <strong>shoes</strong> to arrive. Manila Yacht Club does not allow slippers at the gate &mdash; "
                  "you won't be let in wearing them.", size=14, color_cls="text-ice", color=ICE) +
            _body("Once aboard, change into your own <strong>slippers</strong> for use inside the yacht. Any style is fine, "
                  "as long as they're brand-new or like-new &mdash; not worn or used.",
                  size=14, color_cls="text-ice", color=ICE, extra=" margin-top:8px;"),
            edge="red", pad="18px 20px"), pad="18px 32px 4px 32px")

        rows += _row(_t(_label("What to bring", "blue", " margin-bottom:4px;") + _numbered_rows(CHECKLIST)),
                     pad="22px 32px 4px 32px")
        rows += _poster_row(rules_cid, "Aboard the yacht — House Rules", pad="12px 32px 0 32px")
        rows += _row(_t(_body(
            "Spots aren't transferable &mdash; only the guests named on your booking can board, "
            "and re-entry goes through the same ID check.", size=12)), pad="18px 32px 8px 32px")
        rows += _row(_t(_body("Docked at Manila Yacht Club aboard Happy Life Yacht. See you on the other side.",
                              size=12, extra=f" border-top:1px solid {RULE}; padding-top:16px;")),
                     pad="12px 32px 8px 32px")

        html_body = _page(rows, logo_cid)

        checklist_text = "\n".join(f"{i}. {n} - {note}" for i, (n, note) in enumerate(
            [(n, DRESS_CODE_TEXT if n == "Your costume" else note) for n, note in CHECKLIST], start=1))
        spot_text_line = f"Your spot: {package_name or 'Confirmed booking'}" + (f" - {spot_line}" if table_id else "")

        text_body = (
            f"Hi {guest_name},\n\n"
            f"FINAL REMINDER - the breach opens in a few hours. Here's everything you need before you board.\n\n"
            f"CHECK-IN: {EVENT_CHECKIN_TIME}, gate opens\n"
            f"{EVENT_VENUE_LINE}\n"
            f"Arrive 20-30 minutes early - the guestlist is checked name by name. The party runs until {EVENT_DECK_UNTIL}.\n"
            f"{spot_text_line}\n\n"
            f"FOOTWEAR - READ THIS:\n"
            f"Wear SHOES to arrive. Manila Yacht Club does not allow slippers at the gate - you won't be let in wearing them. "
            f"Once aboard, change into your own slippers for use inside the yacht. Any style is fine, as long as they're "
            f"brand-new or like-new - not worn or used.\n\n"
            f"WHAT TO BRING:\n{checklist_text}\n\n"
            f"Spots aren't transferable - only the guests named on your booking can board.\n\n"
            f"Docked at Manila Yacht Club aboard Happy Life Yacht. See you on the other side.\n\n"
            f"Exclusives PH"
        )

        _send(to_email, "Tonight: wear shoes to enter, slippers once aboard", text_body, html_body, related=[
            (logo_bytes, 'png', logo_msgid, 'logo.png'),
            (rules_bytes, 'jpeg', rules_msgid, 'house-rules-poster.jpg'),
        ])
        print(f"Successfully sent final reminder email to {to_email}")
    except Exception as e:
        print(f"ERROR sending final reminder email to {to_email}: {str(e)}")