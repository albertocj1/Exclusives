import os
import json
import base64
import secrets
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional
from email.message import EmailMessage

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from supabase import Client, create_client

# Branded confirmation email + inline QR & reminders (see email_module.py)
from email_module import send_approval_email, send_pending_reminder_email, send_event_details_email, send_parking_info_email, send_final_reminder_email, PACKAGE_TIER_CONFIG, FOOD_OPTIONS

EVENT_CAPACITY = int(os.getenv("EVENT_CAPACITY", "145"))  # hard cap: no bookings accepted past this
HOLD_MINUTES = int(os.getenv("HOLD_MINUTES", "15"))       # how long an unpaid booking holds its seat/table
EXTRA_HEAD_FEE = 2500

# Receipt upload validation
ALLOWED_RECEIPT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
MAX_RECEIPT_SIZE = 10 * 1024 * 1024  # 10 MB

# per: person | table | bundle
# base_pax: guests included in base price
# extra_head: if True, each guest beyond base_pax costs EXTRA_HEAD_FEE
# max_guests: hard cap for the package
PACKAGES = {
    "Entrance Fee":        {"price": 2500,  "per": "person", "base_pax": 1, "max_guests": 12, "extra_head": False},
    "6-Pax Bottle Bundle": {"price": 15000, "per": "bundle", "base_pax": 6, "max_guests": 6,  "extra_head": False},
    "Standing Table":      {"price": 8000,  "per": "table",  "base_pax": 4, "max_guests": 4,  "extra_head": False},
    "Indoor Couch":        {"price": 15000, "per": "table",  "base_pax": 6, "max_guests": 12, "extra_head": True},
    "Outdoor Couch":       {"price": 15000, "per": "table",  "base_pax": 6, "max_guests": 12, "extra_head": True},
    "SVIP Couch":          {"price": 20000, "per": "table",  "base_pax": 8, "max_guests": 14, "extra_head": True},
}

# Which physical spots belong to which package (DB keeps these IDs).
PACKAGE_SPOTS = {
    "Standing Table": ["DT1", "DT2", "DT3", "DT4"],
    "Indoor Couch":   ["LC4", "LC7"],
    "Outdoor Couch":  ["DC1", "DC2"],
    "SVIP Couch":     ["LC1", "LC2", "LC3", "LC5", "LC6"],
}

# Friendly display names shown in UI (DB stores the technical ID).
SPOT_DISPLAY_NAMES = {
    "LC1": "SVIP 1", "LC2": "SVIP 2", "LC3": "SVIP 3", "LC5": "SVIP 4", "LC6": "SVIP 5",
    "LC4": "VIP 1",  "LC7": "VIP 2",  "DC1": "VIP 3",  "DC2": "VIP 4",
    "DT1": "Table 1", "DT2": "Table 2", "DT3": "Table 3", "DT4": "Table 4",
}

def compute_total(package: str, guests: int) -> int:
    cfg = PACKAGES[package]
    if cfg["per"] == "person":
        return cfg["price"] * guests
    total = cfg["price"]
    if cfg.get("extra_head"):
        extra = max(0, guests - cfg["base_pax"])
        total += extra * EXTRA_HEAD_FEE
    return total

def _is_valid_file_signature(data: bytes, content_type: str) -> bool:
    """Check magic bytes so a renamed malicious file can't slip through
    just because it claims an allowed Content-Type header."""
    signatures = {
        "image/jpeg": [b"\xff\xd8\xff"],
        "image/png": [b"\x89PNG\r\n\x1a\n"],
        "image/webp": [b"RIFF"],  # WEBP files start with RIFF....WEBP
        "application/pdf": [b"%PDF-"],
    }
    expected = signatures.get(content_type, [])
    return any(data.startswith(sig) for sig in expected)

_client: Optional[Client] = None
def db() -> Client:
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return _client

# --- PYDANTIC SCHEMAS ---
class BookingCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)
    instagram: Optional[str] = None
    referrer: Optional[str] = None
    package: str
    table_id: Optional[str] = None
    guests: int = Field(..., ge=1, le=14)
    guest_names: list[str] = Field(default_factory=list)
    accept_terms: bool
    # Optional consent to future-event invites. Defaults to False so a missing
    # field (old cached app.js, API calls) never counts as consent.
    marketing_opt_in: bool = False

    @field_validator("guest_names")
    @classmethod
    def _clean_names(cls, v):
        return [str(n).strip() for n in (v or []) if str(n).strip()]

    @field_validator("accept_terms")
    @classmethod
    def _terms(cls, v):
        if not v:
            raise ValueError("Must confirm terms.")
        return v

    @field_validator("package")
    @classmethod
    def _known_package(cls, v):
        if v not in PACKAGES:
            raise ValueError(f"Unknown package: {v}")
        return v

    @model_validator(mode="after")
    def _guests_within_package(self):
        cfg = PACKAGES[self.package]
        if self.guests > cfg["max_guests"]:
            raise ValueError(f"{self.package} allows at most {cfg['max_guests']} guests.")
        if cfg["per"] == "table" and not self.table_id:
            raise ValueError("This package requires selecting a table.")
        if self.table_id:
            allowed = PACKAGE_SPOTS.get(self.package, [])
            if allowed and self.table_id not in allowed:
                raise ValueError(f"{self.table_id} is not a valid spot for {self.package}.")
        if len(self.guest_names) != self.guests:
            raise ValueError(f"Please provide a name for each guest ({self.guests} required, got {len(self.guest_names)}).")
        return self

class Booking(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    phone: str
    package: str
    table_id: Optional[str] = None
    guests: int
    total_amount: int
    status: str
    receipt_url: Optional[str] = None
    ticket_code: Optional[str] = None
    guest_names: list[str] = Field(default_factory=list)
    created_at: datetime

class BookingUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, min_length=7, max_length=20)
    instagram: Optional[str] = None
    referrer: Optional[str] = None
    package: Optional[str] = None
    table_id: Optional[str] = None
    guests: Optional[int] = Field(None, ge=1, le=14)
    guest_names: Optional[list[str]] = None

    @field_validator("guest_names")
    @classmethod
    def _clean_names(cls, v):
        if v is None:
            return v
        return [str(n).strip() for n in v if str(n).strip()]

    @field_validator("package")
    @classmethod
    def _known_package(cls, v):
        if v is not None and v not in PACKAGES:
            raise ValueError(f"Unknown package: {v}")
        return v

class CheckinBody(BaseModel):
    heads_present: int = Field(..., ge=0, le=50)
    # Positions in the booking's guest_names of the people who are inside (ticked at the door).
    # Optional so older reception pages that only send a head count keep working.
    checked_in_guests: Optional[list[int]] = None

class RemindPayload(BaseModel):
    booking_ids: Optional[list[str]] = None

class NotifyPayload(BaseModel):
    booking_ids: Optional[list[str]] = None

class NotifyParkingPayload(BaseModel):
    booking_ids: Optional[list[str]] = None

class NotifyFinalReminderPayload(BaseModel):
    booking_ids: Optional[list[str]] = None

# ---------------------------------------------------------------------------
#  POS — menu, orders
# ---------------------------------------------------------------------------

POS_MENU_PRICES = {
    "pkg-breeze":       ("Breeze", 3550),
    "pkg-ignition-ab":  ("Ignition (Absolut)", 3850),
    "pkg-ignition-jg":  ("Ignition (Jagermeister)", 4000),
    "btl-absolut":      ("Absolut Vodka", 2850),
    "btl-jager":        ("Jagermeister", 3000),
    "btl-jwblack":      ("Johnnie Walker Black Label", 3195),
    "btl-greygoose":    ("Grey Goose 750ml", 7000),
    "shot-jwblack":     ("Black Label Shot", 250),
    "shot-jager":       ("Jagermeister Shot", 250),
    "shot-absolut":     ("Absolut Vodka Shot", 250),
    "ctl-black-coke":   ("Black x Coke", 450),
    "ctl-absolut-sprite": ("Absolut x Sprite", 450),
    "ctl-jager-redbull": ("Jager x Red Bull", 450),
    "bev-water":        ("Evian", 130),
    "bev-coke":         ("Coca-Cola Regular", 150),
    "bev-sprite":       ("Sprite Lemon-Lime Soda", 150),
    "bev-redbull":      ("Red Bull Energy Drink", 200),
    "bev-lemondou":     ("Lemon-Dou Peach Lemon", 150),
    "food-nachos":      ("Nachos", 600),
    "food-fruit":       ("Fruit Platter", 750),
    "food-board":       ("Charcuterie Board", 850),
}

class OrderItemIn(BaseModel):
    id: str
    qty: int = Field(..., ge=1, le=50)

class OrderCreate(BaseModel):
    table_name: Optional[str] = Field(None, max_length=80)
    items: list[OrderItemIn]
    notes: Optional[str] = Field(None, max_length=500)
    status: Literal["held", "sent"] = "sent"
    payment_method: Optional[Literal["cash", "gcash", "unpaid"]] = "unpaid"

    @field_validator("items")
    @classmethod
    def _must_have_items(cls, v):
        if not v:
            raise ValueError("Order must include at least one item.")
        return v

class OrderStatusUpdate(BaseModel):
    status: Literal["held", "sent", "fulfilled", "cancelled"]
    payment_method: Optional[Literal["cash", "gcash", "unpaid"]] = None

app = FastAPI()

@app.on_event("startup")
def startup_event():
    db()

origins_str = os.environ.get("ALLOWED_ORIGINS", "*")
if origins_str == "*":
    origins_list = ["*"]
else:
    origins_list = [origin.strip() for origin in origins_str.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
#  AUTH DEPENDENCIES
# ---------------------------------------------------------------------------

def require_admin(x_admin_key: str = Header(default="")):
    if not secrets.compare_digest(x_admin_key, os.environ.get("ADMIN_API_KEY", "")):
        raise HTTPException(status_code=401, detail="Invalid admin key.")

_reception_bearer = HTTPBearer(auto_error=False)

def _require_supabase_session(cred: Optional[HTTPAuthorizationCredentials], label: str):
    if cred is None or not cred.credentials:
        raise HTTPException(status_code=401, detail=f"{label} login required.")
    token = cred.credentials
    try:
        user_resp = db().auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    user = getattr(user_resp, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session.")
    return user

def require_reception(cred: Optional[HTTPAuthorizationCredentials] = Depends(_reception_bearer)):
    return _require_supabase_session(cred, "Reception")

def require_staff(cred: Optional[HTTPAuthorizationCredentials] = Depends(_reception_bearer)):
    return _require_supabase_session(cred, "Staff")

# ---------------------------------------------------------------------------
#  IN-MEMORY QUERY OPTIMIZATIONS
# ---------------------------------------------------------------------------

def _get_booking(booking_id: str):
    res = db().table("bookings").select("*").eq("id", booking_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Not found.")
    return res.data[0]

def _confirmed_guest_count() -> int:
    res = db().table("bookings").select("guests").eq("status", "confirmed").execute()
    return sum(row.get("guests", 0) for row in (res.data or []))

def _committed_guest_count() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=HOLD_MINUTES)).isoformat()
    res = db().table("bookings").select("guests, status, created_at").in_("status", ["confirmed", "verifying", "pending"]).execute()
    
    taken = 0
    for r in (res.data or []):
        st = r.get("status")
        g = r.get("guests", 0)
        if st in ["confirmed", "verifying"]:
            taken += g
        elif st == "pending" and r.get("created_at") >= cutoff:
            taken += g
    return taken

def _table_capacity(package_name: Optional[str]) -> Optional[int]:
    cfg = PACKAGES.get(package_name or "")
    return cfg["max_guests"] if cfg else None

# ---------------------------------------------------------------------------
#  PUBLIC ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/api/availability")
def availability():
    taken = _committed_guest_count()
    left = max(0, EVENT_CAPACITY - taken)
    return {
        "capacity": EVENT_CAPACITY,
        "taken": taken,
        "spots_left": left,
        "sold_out": left <= 0,
        "confirmed": _confirmed_guest_count(),
    }

@app.get("/api/tables/availability")
def get_tables():
    all_tables = db().table("tables").select("*").execute().data
    lock_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=HOLD_MINUTES)).isoformat()

    res = db().table("bookings").select("table_id, status, created_at").in_("status", ["confirmed", "verifying", "pending"]).not_.is_("table_id", "null").execute()
    bookings = res.data or []

    taken_ids = set()
    for b in bookings:
        st = b.get("status")
        tid = b.get("table_id")
        if not tid: continue
        if st == "confirmed":
            taken_ids.add(tid)
        elif st in ["pending", "verifying"] and b.get("created_at") >= lock_cutoff:
            taken_ids.add(tid)

    for t in all_tables:
        t["is_available"] = t["id"] not in taken_ids
    return {"tables": all_tables}

@app.post("/api/bookings", response_model=Booking)
def create_booking(payload: BookingCreate):
    taken = _committed_guest_count()
    left = max(0, EVENT_CAPACITY - taken)
    if left <= 0:
        raise HTTPException(status_code=409, detail="The guestlist is full. No spots remain.")
    if payload.guests > left:
        raise HTTPException(
            status_code=409,
            detail=f"Only {left} spot{'s' if left != 1 else ''} left — you requested {payload.guests}.",
        )

    if payload.table_id:
        lock_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=HOLD_MINUTES)).isoformat()
        
        res = db().table("bookings").select("id, status, created_at").eq("table_id", payload.table_id).in_("status", ["confirmed", "verifying", "pending"]).execute()
        for b in (res.data or []):
            st = b.get("status")
            if st == "confirmed":
                raise HTTPException(status_code=409, detail="Table just got reserved by someone else.")
            elif st in ["pending", "verifying"] and b.get("created_at") >= lock_cutoff:
                raise HTTPException(status_code=409, detail="Table just got reserved by someone else.")

    unit = PACKAGES[payload.package]["price"]
    total = compute_total(payload.package, payload.guests)

    try:
        res = db().table("bookings").insert({
            "full_name": payload.full_name, "email": payload.email, "phone": payload.phone,
            "instagram": payload.instagram, "referrer": payload.referrer,
            "package": payload.package, "table_id": payload.table_id, "guests": payload.guests,
            "guest_names": payload.guest_names,
            "unit_price": unit, "total_amount": total, "status": "pending",
            "marketing_opt_in": payload.marketing_opt_in,
            "marketing_opt_in_at": datetime.now(timezone.utc).isoformat() if payload.marketing_opt_in else None,
        }).execute()
    except Exception as e:
        if "23505" in str(e) or "duplicate key" in str(e).lower():
            raise HTTPException(status_code=409, detail="Table just got reserved by someone else.")
        raise

    return Booking(**res.data[0])

@app.post("/api/bookings/{booking_id}/submit-payment", response_model=Booking)
async def submit_payment(booking_id: str, receipt: UploadFile = File(...)):
    booking = _get_booking(booking_id)
    if booking["status"] != "pending":
        raise HTTPException(status_code=400, detail="Cannot submit payment for this booking.")

    content_type = (receipt.content_type or "").lower()
    if content_type not in ALLOWED_RECEIPT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Please upload a JPG, PNG, WEBP, or PDF.",
        )

    file_bytes = await receipt.read()
    if len(file_bytes) > MAX_RECEIPT_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max size is 10MB.")
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file.")

    if not _is_valid_file_signature(file_bytes, content_type):
        raise HTTPException(status_code=400, detail="File content doesn't match its type.")

    file_ext = ALLOWED_RECEIPT_TYPES[content_type]
    file_name = f"{booking_id}_{secrets.token_hex(4)}{file_ext}"

    try:
        db().storage.from_("receipts").upload(
            file_name, file_bytes, {"content-type": content_type}
        )
        receipt_url = db().storage.from_("receipts").get_public_url(file_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not upload receipt: {str(e)}")

    res = db().table("bookings").update({
        "status": "verifying", "receipt_url": receipt_url
    }).eq("id", booking_id).execute()
    return Booking(**res.data[0])

# ---------------------------------------------------------------------------
#  ADMIN ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/api/bookings", dependencies=[Depends(require_admin)])
def list_bookings():
    return db().table("bookings").select("*").order("created_at", desc=True).limit(1000).execute().data

@app.patch("/api/bookings/{booking_id}", response_model=Booking, dependencies=[Depends(require_admin)])
def update_booking(booking_id: str, payload: BookingUpdate):
    existing = _get_booking(booking_id)
    if existing["status"] == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot edit a cancelled booking.")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    merged = {**existing, **updates}

    package = merged.get("package")
    if package not in PACKAGES:
        raise HTTPException(status_code=400, detail=f"Unknown package: {package}")
    cfg = PACKAGES[package]

    guests = merged.get("guests")
    if not guests or guests < 1:
        raise HTTPException(status_code=400, detail="Guests must be at least 1.")
    if guests > cfg["max_guests"]:
        raise HTTPException(status_code=400, detail=f"{package} allows at most {cfg['max_guests']} guests.")

    table_id = merged.get("table_id")
    if cfg["per"] == "table" and not table_id:
        raise HTTPException(status_code=400, detail="This package requires selecting a table.")
    if table_id:
        allowed = PACKAGE_SPOTS.get(package, [])
        if allowed and table_id not in allowed:
            raise HTTPException(status_code=400, detail=f"{table_id} is not a valid spot for {package}.")

    guest_names = merged.get("guest_names") or []
    if len(guest_names) != guests:
        raise HTTPException(
            status_code=400,
            detail=f"Please provide a name for each guest ({guests} required, got {len(guest_names)}).",
        )

    if guests != existing.get("guests"):
        taken = _committed_guest_count()
        counts_toward_cap = existing["status"] in ("confirmed", "verifying", "pending")
        available = EVENT_CAPACITY - taken + (existing.get("guests", 0) if counts_toward_cap else 0)
        if guests > available:
            raise HTTPException(
                status_code=409,
                detail=f"Only {available} spot{'s' if available != 1 else ''} available for this booking.",
            )

    if table_id and table_id != existing.get("table_id"):
        lock_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=HOLD_MINUTES)).isoformat()
        res = (
            db().table("bookings").select("id, status, created_at")
            .eq("table_id", table_id).neq("id", booking_id)
            .in_("status", ["confirmed", "verifying", "pending"]).execute()
        )
        for b in (res.data or []):
            st = b.get("status")
            if st == "confirmed" or (st in ["pending", "verifying"] and b.get("created_at") >= lock_cutoff):
                raise HTTPException(status_code=409, detail="That table is already reserved by another guest.")

    update_payload = {
        "full_name": merged.get("full_name"),
        "email": merged.get("email"),
        "phone": merged.get("phone"),
        "instagram": merged.get("instagram"),
        "referrer": merged.get("referrer"),
        "package": package,
        "table_id": table_id,
        "guests": guests,
        "guest_names": guest_names,
        "unit_price": cfg["price"],
        "total_amount": compute_total(package, guests),
    }

    try:
        res = db().table("bookings").update(update_payload).eq("id", booking_id).execute()
    except Exception as e:
        if "23505" in str(e) or "duplicate key" in str(e).lower():
            raise HTTPException(status_code=409, detail="That table is already reserved by another guest.")
        raise

    if not res.data:
        raise HTTPException(status_code=502, detail="Failed to update booking.")
    return Booking(**res.data[0])

@app.post("/api/bookings/{booking_id}/approve", dependencies=[Depends(require_admin)])
def approve_booking(booking_id: str, background_tasks: BackgroundTasks):
    b = _get_booking(booking_id)
    if b["status"] != "verifying":
        raise HTTPException(status_code=400, detail="Not awaiting verification.")

    try:
        res = db().table("bookings").update({
            "status": "confirmed",
            "ticket_code": "EXC-" + secrets.token_hex(3).upper(),
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", booking_id).execute()
    except Exception as e:
        if "23505" in str(e) or "duplicate key" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="That table is already confirmed for another guest. Cancel one before approving.",
            )
        raise

    if res.data:
        booking_data = res.data[0]
        background_tasks.add_task(
            send_approval_email,
            to_email=booking_data["email"],
            guest_name=booking_data["full_name"],
            ticket_code=booking_data["ticket_code"],
            package_name=booking_data["package"],
            guests=booking_data["guests"],
            table_id=booking_data.get("table_id"),
        )
        return Booking(**booking_data)
    raise HTTPException(status_code=502, detail="Failed to issue ticket.")

@app.post("/api/bookings/{booking_id}/cancel", dependencies=[Depends(require_admin)])
def cancel_booking(booking_id: str):
    res = db().table("bookings").update({"status": "cancelled"}).eq("id", booking_id).execute()
    return Booking(**res.data[0])

@app.delete("/api/bookings/{booking_id}", dependencies=[Depends(require_admin)])
def delete_booking(booking_id: str):
    _get_booking(booking_id)
    db().table("bookings").delete().eq("id", booking_id).execute()
    return {"status": "deleted", "id": booking_id}

@app.post("/api/bookings/remind-pending", dependencies=[Depends(require_admin)])
def remind_pending_bookings(payload: RemindPayload, background_tasks: BackgroundTasks):
    query = db().table("bookings").select("*").eq("status", "pending")
    if payload.booking_ids:
        query = query.in_("id", payload.booking_ids)
    res = query.execute()
    pending_rows = res.data or []

    unique_pending = {}
    for row in pending_rows:
        email_key = row["email"].lower().strip()
        if email_key not in unique_pending:
            unique_pending[email_key] = row

    reminded_count = 0
    for email, b in unique_pending.items():
        background_tasks.add_task(
            send_pending_reminder_email,
            to_email=email,
            guest_name=b["full_name"],
            package_name=b["package"],
            guests=b["guests"],
            total_amount=b["total_amount"],
        )
        reminded_count += 1

    return {
        "status": "success",
        "reminded_count": reminded_count,
        "total_pending_rows": len(pending_rows),
        "duplicates_skipped": len(pending_rows) - reminded_count,
    }

@app.post("/api/bookings/notify-confirmed", dependencies=[Depends(require_admin)])
def notify_confirmed_bookings(payload: NotifyPayload, background_tasks: BackgroundTasks):
    query = db().table("bookings").select("*").eq("status", "confirmed")
    if payload.booking_ids:
        query = query.in_("id", payload.booking_ids)
    res = query.execute()
    confirmed_rows = res.data or []

    # One email per address. If a guest has several bookings, keep the one with
    # the most perks so an SVIP guest isn't sent the "no picks" version.
    def perk_rank(row):
        cfg = PACKAGE_TIER_CONFIG.get(row.get("package"), {})
        return (cfg.get("bottle_count", 0), cfg.get("food", False), cfg.get("cans", 0))

    unique_confirmed = {}
    for row in confirmed_rows:
        email_key = row["email"].lower().strip()
        if email_key not in unique_confirmed or perk_rank(row) > perk_rank(unique_confirmed[email_key]):
            unique_confirmed[email_key] = row

    notified_count = 0
    for email, b in unique_confirmed.items():
        background_tasks.add_task(
            send_event_details_email,
            to_email=email,
            guest_name=b["full_name"],
            package_name=b.get("package"),
            table_id=b.get("table_id"),
        )
        notified_count += 1

    return {
        "status": "success",
        "notified_count": notified_count,
        "total_confirmed_rows": len(confirmed_rows),
        "duplicates_skipped": len(confirmed_rows) - notified_count,
    }

@app.post("/api/bookings/notify-parking", dependencies=[Depends(require_admin)])
def notify_parking_bookings(payload: NotifyParkingPayload, background_tasks: BackgroundTasks):
    query = db().table("bookings").select("*").eq("status", "confirmed")
    if payload.booking_ids:
        query = query.in_("id", payload.booking_ids)
    res = query.execute()
    confirmed_rows = res.data or []

    unique_confirmed = {}
    for row in confirmed_rows:
        email_key = row["email"].lower().strip()
        if email_key not in unique_confirmed:
            unique_confirmed[email_key] = row

    notified_count = 0
    for email, b in unique_confirmed.items():
        background_tasks.add_task(
            send_parking_info_email,
            to_email=email,
            guest_name=b["full_name"],
        )
        notified_count += 1

    return {
        "status": "success",
        "notified_count": notified_count,
        "total_confirmed_rows": len(confirmed_rows),
        "duplicates_skipped": len(confirmed_rows) - notified_count,
    }

@app.post("/api/bookings/notify-final-reminder", dependencies=[Depends(require_admin)])
def notify_final_reminder_bookings(payload: NotifyFinalReminderPayload, background_tasks: BackgroundTasks):
    query = db().table("bookings").select("*").eq("status", "confirmed")
    if payload.booking_ids:
        query = query.in_("id", payload.booking_ids)
    res = query.execute()
    confirmed_rows = res.data or []

    unique_confirmed = {}
    for row in confirmed_rows:
        email_key = row["email"].lower().strip()
        if email_key not in unique_confirmed:
            unique_confirmed[email_key] = row

    notified_count = 0
    for email, b in unique_confirmed.items():
        background_tasks.add_task(
            send_final_reminder_email,
            to_email=email,
            guest_name=b["full_name"],
            package_name=b["package"],
            table_id=b.get("table_id"),
        )
        notified_count += 1

    return {
        "status": "success",
        "notified_count": notified_count,
        "total_confirmed_rows": len(confirmed_rows),
        "duplicates_skipped": len(confirmed_rows) - notified_count,
    }

# ---------------------------------------------------------------------------
#  RECEPTION ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/api/reception/search", dependencies=[Depends(require_reception)])
def reception_search(q: str):
    query_str = (q or "").strip()
    if not query_str or len(query_str) < 2:
        raise HTTPException(status_code=400, detail="Search query must be at least 2 characters.")

    # Reception only works with confirmed bookings; pending/verifying guests are not on the door list.
    all_rows = db().table("bookings").select("*").eq("status", "confirmed").execute().data or []
    q_lower = query_str.lower()
    matches = []
    for b in all_rows:
        full_name = (b.get("full_name") or "").lower()
        ticket = (b.get("ticket_code") or "").lower()
        gnames = [str(n).lower() for n in (b.get("guest_names") or [])]

        if q_lower in full_name or q_lower in ticket or any(q_lower in n for n in gnames):
            matches.append({
                "id": b["id"],
                "ticket_code": b.get("ticket_code"),
                "full_name": b["full_name"],
                "package": b["package"],
                "table_id": b.get("table_id"),
                "guests": b["guests"],
                "status": b["status"],
                "checked_in": b.get("checked_in", False),
                "checked_in_at": b.get("checked_in_at"),
                "heads_present": b.get("heads_present", 0),
                "guest_names": b.get("guest_names") or [],
                "checked_in_guests": b.get("checked_in_guests") or [],
                "comp_bottles": b.get("comp_bottles") or [],
                "comp_food": b.get("comp_food"),
            })
    matches.sort(key=lambda m: 0 if m["status"] == "confirmed" else 1)
    return {"results": matches}

@app.get("/api/reception/guests", dependencies=[Depends(require_reception)])
def reception_guests():
    # Reception only works with confirmed bookings; pending/verifying guests are not on the door list.
    all_rows = db().table("bookings").select("*").eq("status", "confirmed").execute().data or []

    guests = []
    for b in all_rows:
        guests.append({
            "id": b["id"],
            "ticket_code": b.get("ticket_code"),
            "full_name": b["full_name"],
            "package": b["package"],
            "table_id": b.get("table_id"),
            "guests": b["guests"],
            "status": b["status"],
            "checked_in": b.get("checked_in", False),
            "checked_in_at": b.get("checked_in_at"),
            "heads_present": b.get("heads_present", 0),
            "guest_names": b.get("guest_names") or [],
            "checked_in_guests": b.get("checked_in_guests") or [],
            "comp_bottles": b.get("comp_bottles") or [],
            "comp_food": b.get("comp_food"),
        })

    # Confirmed-and-not-yet-arrived first, then confirmed-checked-in, then anything else (pending/etc).
    def sort_key(g):
        if g["status"] == "confirmed" and not g["checked_in"]:
            rank = 0
        elif g["status"] == "confirmed" and g["checked_in"]:
            rank = 1
        else:
            rank = 2
        return (rank, g["full_name"].lower())

    guests.sort(key=sort_key)

    total = len(guests)
    checked_in = sum(1 for g in guests if g["checked_in"])
    return {
        "guests": guests,
        "total": total,
        "checked_in": checked_in,
        "not_checked_in": total - checked_in,
    }

@app.get("/api/reception/lookup/{ticket_code}", dependencies=[Depends(require_reception)])
def reception_lookup(ticket_code: str):
    code = (ticket_code or "").strip().upper()
    res = db().table("bookings").select("*").eq("ticket_code", code).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    b = res.data[0]
    return {
        "id": b["id"],
        "ticket_code": b.get("ticket_code"),
        "full_name": b["full_name"],
        "package": b["package"],
        "table_id": b.get("table_id"),
        "guests": b["guests"],
        "status": b["status"],
        "checked_in": b.get("checked_in", False),
        "checked_in_at": b.get("checked_in_at"),
        "heads_present": b.get("heads_present", 0),
        "guest_names": b.get("guest_names") or [],
        "checked_in_guests": b.get("checked_in_guests") or [],
        "comp_bottles": b.get("comp_bottles") or [],
        "comp_food": b.get("comp_food"),
    }


def _update_checkin(booking_id: str, update: dict):
    """Write a check-in update. If the checked_in_guests column hasn't been added to the
    database yet, save everything else so the door keeps working on head counts alone."""
    try:
        return db().table("bookings").update(update).eq("id", booking_id).execute()
    except Exception as e:
        if "checked_in_guests" in update and "checked_in_guests" in str(e):
            print(f"WARNING: bookings.checked_in_guests column missing; saving head count only. ({e})")
            rest = {k: v for k, v in update.items() if k != "checked_in_guests"}
            return db().table("bookings").update(rest).eq("id", booking_id).execute()
        raise

@app.post("/api/reception/checkin/{booking_id}", dependencies=[Depends(require_reception)])
def reception_checkin(booking_id: str, body: CheckinBody):
    b = _get_booking(booking_id)
    if b["status"] != "confirmed":
        raise HTTPException(status_code=400, detail="This booking is not confirmed — cannot check in.")

    max_heads = b.get("guests") or 0
    if body.heads_present > max_heads:
        raise HTTPException(
            status_code=400,
            detail=f"This booking only has {max_heads} guest{'s' if max_heads != 1 else ''} — can't check in {body.heads_present}.",
        )

    already = bool(b.get("checked_in"))
    update = {"checked_in": True, "heads_present": body.heads_present}

    if body.checked_in_guests is not None:
        n_names = len(b.get("guest_names") or [])
        ticked = sorted(set(body.checked_in_guests))
        if any(i < 0 or i >= n_names for i in ticked):
            raise HTTPException(status_code=400, detail="Ticked guest is not on this booking. Refresh and try again.")
        if len(ticked) > body.heads_present:
            raise HTTPException(status_code=400, detail="More guests ticked than the head count.")
        update["checked_in_guests"] = ticked

    if not already:
        update["checked_in_at"] = datetime.now(timezone.utc).isoformat()

    res = _update_checkin(booking_id, update)
    row = res.data[0]
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "table_id": row.get("table_id"),
        "guests": row["guests"],
        "heads_present": row.get("heads_present", 0),
        "checked_in": row.get("checked_in", False),
        "checked_in_at": row.get("checked_in_at"),
        "checked_in_guests": row.get("checked_in_guests") or [],
        "already_checked_in": already,
    }

@app.post("/api/reception/checkin/{booking_id}/undo", dependencies=[Depends(require_reception)])
def reception_undo_checkin(booking_id: str):
    b = _get_booking(booking_id)
    if not b.get("checked_in"):
        raise HTTPException(status_code=400, detail="This booking isn't checked in.")

    update = {"checked_in": False, "heads_present": 0, "checked_in_at": None, "checked_in_guests": []}
    res = _update_checkin(booking_id, update)
    row = res.data[0]
    return {
        "id": row["id"],
        "ticket_code": row.get("ticket_code"),
        "full_name": row["full_name"],
        "table_id": row.get("table_id"),
        "guests": row["guests"],
        "heads_present": row.get("heads_present", 0),
        "checked_in": row.get("checked_in", False),
        "checked_in_at": row.get("checked_in_at"),
        "checked_in_guests": row.get("checked_in_guests") or [],
    }

@app.get("/api/reception/summary", dependencies=[Depends(require_reception)])
def reception_summary():
    confirmed = db().table("bookings").select("*").eq("status", "confirmed").execute().data or []

    total_bookings = len(confirmed)
    checked_in_bookings = sum(1 for b in confirmed if b.get("checked_in"))

    expected_heads = sum((b.get("guests") or 0) for b in confirmed)
    expected_entrance = sum((b.get("guests") or 0) for b in confirmed if b.get("package") in ["Entrance Fee", "6-Pax Bottle Bundle"])
    expected_couch = expected_heads - expected_entrance
    present_heads = sum((b.get("heads_present") or 0) for b in confirmed if b.get("checked_in"))

    present_entrance = sum(
        (b.get("heads_present") or 0)
        for b in confirmed
        if b.get("checked_in") and b.get("package") in ["Entrance Fee", "6-Pax Bottle Bundle"]
    )
    present_couch = present_heads - present_entrance

    def _remaining(b):
        present = (b.get("heads_present") or 0) if b.get("checked_in") else 0
        return max(0, (b.get("guests") or 0) - present)

    coming_entrance = sum(_remaining(b) for b in confirmed if b.get("package") in ["Entrance Fee", "6-Pax Bottle Bundle"])
    coming_couch = sum(_remaining(b) for b in confirmed if b.get("package") not in ["Entrance Fee", "6-Pax Bottle Bundle"])
    coming_heads = coming_entrance + coming_couch

    return {
        "total_bookings": total_bookings,
        "checked_in_bookings": checked_in_bookings,
        "pending_bookings": total_bookings - checked_in_bookings,
        "expected_heads": expected_heads,
        "expected_entrance": expected_entrance,
        "expected_couch": expected_couch,
        "present_heads": present_heads,
        "present_entrance": present_entrance,
        "present_couch": present_couch,
        "coming_heads": coming_heads,
        "coming_entrance": coming_entrance,
        "coming_couch": coming_couch,
    }

@app.get("/api/reception/tables", dependencies=[Depends(require_reception)])
def reception_tables():
    tables = db().table("tables").select("*").execute().data or []
    confirmed = db().table("bookings").select("*") \
        .eq("status", "confirmed") \
        .not_.is_("table_id", "null") \
        .execute().data or []

    by_table = {}
    for bk in confirmed:
        by_table.setdefault(bk["table_id"], []).append(bk)

    out = []
    for t in tables:
        bks = by_table.get(t["id"], [])
        seated = sum((bk.get("heads_present") or 0) for bk in bks if bk.get("checked_in"))
        booked = sum((bk.get("guests") or 0) for bk in bks)
        out.append({
            "id": t["id"],
            "package": t.get("package"),
            "capacity": _table_capacity(t.get("package")),
            "reserved_by": [bk["full_name"] for bk in bks],
            "booked_pax": booked,
            "seated": seated,
            "any_checked_in": any(bk.get("checked_in") for bk in bks),
        })
    out.sort(key=lambda x: str(x["id"]))
    return {"tables": out}

# ---------------------------------------------------------------------------
#  COMPLIMENTARY BOTTLES / FOOD / CANS
#  Allowances come from PACKAGE_TIER_CONFIG (same rules as the guest emails).
#  Picks are entered in admin; items are handed out from the bar display.
# ---------------------------------------------------------------------------

COMP_BOTTLES = ["Absolut Vodka", "Johnnie Walker Black Label", "Jagermeister"]
COMP_FOOD = list(FOOD_OPTIONS)
COMP_CANS = ["Sprite", "Coca-Cola", "Red Bull"]
COMP_DRINKS = ["Cocktail Drinks"]
COMP_TYPES = ("bottle", "food", "can", "drink")
# Complimentary cocktails per person who has checked in, by package. Tracked per guest when
# reception ticked names at check-in; otherwise as a group total (2 x heads present).
COMP_DRINKS_PER_PERSON: dict[str, int] = {"Entrance Fee": 2, "6-Pax Bottle Bundle": 2}

class CompPicks(BaseModel):
    bottles: list[str] = Field(default_factory=list)
    food: Optional[str] = None

class CompClaimIn(BaseModel):
    item_type: Literal["bottle", "food", "can", "drink"]
    item_name: Optional[str] = Field(None, max_length=60)
    qty: int = Field(1, ge=1, le=12)
    # For cocktails tracked per guest: the guest's position in guest_names.
    guest_index: Optional[int] = Field(None, ge=0, le=100)

def _inside_guests(b: dict) -> list:
    """Positions in guest_names that reception ticked as inside."""
    n = len(b.get("guest_names") or [])
    return sorted({i for i in (b.get("checked_in_guests") or []) if isinstance(i, int) and 0 <= i < n})

def _drinks_per_guest_mode(b: dict) -> bool:
    """Cocktails are tracked per guest once the booking is checked in with ticked names."""
    return bool(COMP_DRINKS_PER_PERSON.get(b.get("package") or "")) and bool(b.get("checked_in")) and bool(_inside_guests(b))

def _comp_allowance(package: Optional[str], booking: Optional[dict] = None) -> dict:
    cfg = PACKAGE_TIER_CONFIG.get(package or "", {})
    per_person = COMP_DRINKS_PER_PERSON.get(package or "", 0)
    people = 0
    if per_person and booking is not None:
        if not booking.get("checked_in"):
            people = booking.get("guests") or 0              # shown only; claiming needs check-in
        elif _inside_guests(booking):
            people = len(_inside_guests(booking))            # per guest
        else:
            people = booking.get("heads_present") or 0       # checked in by head count only
    return {"bottle": cfg.get("bottle_count", 0), "food": 1 if cfg.get("food") else 0,
            "can": cfg.get("cans", 0), "drink": per_person * people}

def _active_claims(booking_ids: Optional[list] = None) -> list:
    q = db().table("comp_claims").select("*").is_("voided_at", "null")
    if booking_ids is not None:
        if not booking_ids:
            return []
        q = q.in_("booking_id", [str(i) for i in booking_ids])
    return q.order("created_at").execute().data or []

def _comp_view(b: dict, claims: list) -> dict:
    allowance = _comp_allowance(b.get("package"), b)
    mine = [c for c in claims if str(c["booking_id"]) == str(b["id"])]
    given = {t: sum(1 for c in mine if c["item_type"] == t) for t in COMP_TYPES}
    names = [str(n) for n in (b.get("guest_names") or [])]
    groups = {}
    for c in mine:
        gi = c.get("guest_index")
        g = groups.setdefault(c["claim_id"], {"claim_id": c["claim_id"], "item_type": c["item_type"],
                                              "item_name": c.get("item_name"), "qty": 0,
                                              "guest_index": gi,
                                              "guest_name": names[gi] if isinstance(gi, int) and 0 <= gi < len(names) else None,
                                              "created_at": c["created_at"], "given_by": c.get("given_by")})
        g["qty"] += 1

    remaining = {t: max(0, allowance[t] - given[t]) for t in COMP_TYPES}
    per_person = COMP_DRINKS_PER_PERSON.get(b.get("package") or "", 0)
    people = []
    if per_person and names:
        inside = set(_inside_guests(b)) if b.get("checked_in") else set()
        for i, name in enumerate(names):
            got = [c for c in mine if c["item_type"] == "drink" and c.get("guest_index") == i]
            people.append({
                "index": i, "name": name, "inside": i in inside,
                "given": len(got),
                "left": max(0, per_person - len(got)) if i in inside else 0,
                "last_at": max((c["created_at"] for c in got), default=None),
            })
        if _drinks_per_guest_mode(b):
            remaining["drink"] = sum(p["left"] for p in people)
    return {
        "id": b["id"],
        "full_name": b["full_name"],
        "package": b["package"],
        "table_id": b.get("table_id"),
        "ticket_code": b.get("ticket_code"),
        "checked_in": bool(b.get("checked_in")),
        "guests": b.get("guests") or 0,
        "heads_present": b.get("heads_present") or 0,
        "drinks_per_person": per_person,
        "drinks_per_guest": _drinks_per_guest_mode(b),
        "people": people,
        "allowance": allowance,
        "given": given,
        "remaining": remaining,
        "picks": {"bottles": b.get("comp_bottles") or [], "food": b.get("comp_food")},
        "claims": sorted(groups.values(), key=lambda g: g["created_at"]),
    }

@app.put("/api/bookings/{booking_id}/comp-picks", dependencies=[Depends(require_admin)])
def set_comp_picks(booking_id: str, payload: CompPicks):
    b = _get_booking(booking_id)
    allowance = _comp_allowance(b.get("package"))
    bottles = [x for x in (payload.bottles or []) if x]
    if len(bottles) > allowance["bottle"]:
        raise HTTPException(status_code=400, detail=f"{b['package']} gets {allowance['bottle']} bottle(s), not {len(bottles)}.")
    bad = [x for x in bottles if x not in COMP_BOTTLES]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown bottle: {bad[0]}")
    food = payload.food or None
    if food and not allowance["food"]:
        raise HTTPException(status_code=400, detail=f"{b['package']} does not include food.")
    if food and food not in COMP_FOOD:
        raise HTTPException(status_code=400, detail=f"Unknown food: {food}")
    res = db().table("bookings").update({"comp_bottles": bottles, "comp_food": food}).eq("id", booking_id).execute()
    row = res.data[0]
    return {"id": row["id"], "comp_bottles": row.get("comp_bottles") or [], "comp_food": row.get("comp_food")}

@app.get("/api/comps", dependencies=[Depends(require_staff)])
def list_comps():
    """Every confirmed booking with complimentary items, what it picked and what's left."""
    rows = db().table("bookings").select("*").eq("status", "confirmed").execute().data or []
    rows = [b for b in rows if any(_comp_allowance(b.get("package"), b).values())]
    claims = _active_claims([b["id"] for b in rows])
    out = [_comp_view(b, claims) for b in rows]
    out.sort(key=lambda x: (str(x.get("table_id") or "~"), x["full_name"].lower()))
    return {
        "bookings": out,
        "options": {"bottle": COMP_BOTTLES, "food": COMP_FOOD, "can": COMP_CANS, "drink": COMP_DRINKS},
    }

@app.post("/api/comps/{booking_id}/claim")
def claim_comp(booking_id: str, payload: CompClaimIn, user=Depends(require_staff)):
    b = _get_booking(booking_id)
    if b["status"] != "confirmed":
        raise HTTPException(status_code=400, detail="Booking is not confirmed.")
    if not b.get("checked_in"):
        raise HTTPException(status_code=400, detail="Guest is not checked in yet \u2014 send them to reception first.")

    t = payload.item_type
    options = {"bottle": COMP_BOTTLES, "food": COMP_FOOD, "can": COMP_CANS, "drink": COMP_DRINKS}[t]
    name = payload.item_name or (COMP_DRINKS[0] if t == "drink" else None)
    if t in ("bottle", "food") and name not in options:
        raise HTTPException(status_code=400, detail=f"Choose which {t}: " + ", ".join(options) + ".")
    if t in ("can", "drink") and name and name not in options:
        raise HTTPException(status_code=400, detail=f"Choose which {t}: " + ", ".join(options) + ".")
    if t == "can" and not name:
        raise HTTPException(status_code=400, detail="Choose which can: " + ", ".join(options) + ".")
    qty = payload.qty if t in ("can", "drink") else 1

    allowance = _comp_allowance(b.get("package"), b)[t]
    if not allowance:
        if t == "drink" and COMP_DRINKS_PER_PERSON.get(b.get("package") or ""):
            raise HTTPException(status_code=400, detail="Nobody from this booking is checked in yet.")
        raise HTTPException(status_code=400, detail=f"{b['package']} does not include complimentary {t}s.")

    guest_index = None
    active = [c for c in _active_claims([booking_id]) if c["item_type"] == t]
    if t == "drink" and _drinks_per_guest_mode(b):
        # Each checked-in guest has their own cocktails.
        guest_index = payload.guest_index
        names = b.get("guest_names") or []
        if guest_index is None:
            raise HTTPException(status_code=400, detail="Choose which guest is claiming.")
        if guest_index not in _inside_guests(b):
            who = names[guest_index] if 0 <= guest_index < len(names) else "That guest"
            raise HTTPException(status_code=400, detail=f"{who} is not checked in \u2014 send them to reception first.")
        allowance = COMP_DRINKS_PER_PERSON[b["package"]]
        active = [c for c in active if c.get("guest_index") == guest_index]
        free_slots = [n for n in range(1, allowance + 1) if n not in {c["slot"] for c in active}]
        if qty > len(free_slots):
            left = len(free_slots)
            raise HTTPException(status_code=409, detail=f"{names[guest_index]} has {left} cocktail{'s' if left != 1 else ''} left ({len(active)} of {allowance} already given).")
    else:
        free_slots = [n for n in range(1, allowance + 1) if n not in {c["slot"] for c in active}]
        if qty > len(free_slots):
            left = len(free_slots)
            raise HTTPException(status_code=409, detail=f"Only {left} {t}{'s' if left != 1 else ''} left for this booking ({len(active)} of {allowance} already given).")

    claim_id = str(uuid.uuid4())
    rows = [{
        "claim_id": claim_id, "booking_id": str(booking_id), "table_id": b.get("table_id"),
        "item_type": t, "item_name": name, "slot": slot, "guest_index": guest_index,
        "given_by": getattr(user, "email", None),
    } for slot in free_slots[:qty]]
    try:
        db().table("comp_claims").insert(rows).execute()
    except Exception as e:
        if "23505" in str(e) or "duplicate key" in str(e).lower():
            raise HTTPException(status_code=409, detail="Another bar device just gave this item. Refresh and check what's left.")
        raise
    return _comp_view(b, _active_claims([booking_id]))

@app.post("/api/comps/claims/{claim_id}/undo", dependencies=[Depends(require_staff)])
def undo_comp_claim(claim_id: str):
    rows = db().table("comp_claims").select("*").eq("claim_id", claim_id).is_("voided_at", "null").execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Nothing to undo (already undone?).")
    db().table("comp_claims").update({"voided_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("claim_id", claim_id).is_("voided_at", "null").execute()
    b = _get_booking(rows[0]["booking_id"])
    return _comp_view(b, _active_claims([rows[0]["booking_id"]]))

@app.get("/api/comps/claims", dependencies=[Depends(require_staff)])
def list_comp_claims():
    """Every active claim, for the bar dashboard and Excel export."""
    claims = _active_claims()
    ids = list({str(c["booking_id"]) for c in claims})
    names = {}
    if ids:
        for b in db().table("bookings").select("id, full_name, package, guest_names").in_("id", ids).execute().data or []:
            names[str(b["id"])] = b
    for c in claims:
        b = names.get(str(c["booking_id"]), {})
        c["full_name"] = b.get("full_name")
        c["package"] = b.get("package")
        gi, gn = c.get("guest_index"), b.get("guest_names") or []
        c["guest_name"] = gn[gi] if isinstance(gi, int) and 0 <= gi < len(gn) else None
    return {"claims": claims}

# ---------------------------------------------------------------------------
#  INVENTORY — bottles and mixers on hand
# ---------------------------------------------------------------------------
# On hand = the latest physical count of an item, plus restocks and adjustments since,
# minus bottles opened for pouring, POS orders (not cancelled) and complimentary items
# given (not undone) since that count. Nothing is stored per sale, so a cancelled order
# or an undone claim puts the stock back on its own. Stock never blocks a sale: on hand
# can go below zero until the bar restocks or counts.

INVENTORY_ITEMS = {
    # key: (name, unit, warn at or below)
    "jager":     ("Jagermeister", "bottle", 3),
    "absolut":   ("Absolut Vodka", "bottle", 3),
    "jwblack":   ("Johnnie Walker Black Label", "bottle", 3),
    "greygoose": ("Grey Goose 750ml", "bottle", 1),
    "evian":     ("Evian", "bottle", 12),
    "redbull":   ("Red Bull", "can", 24),
    "coke":      ("Coca-Cola", "can", 12),
    "sprite":    ("Sprite", "can", 12),
}
# Liquor that shots are poured from: the bar opens a bottle in Inventory instead of per shot.
POUR_ITEMS = ("jager", "absolut", "jwblack")
# Stock used by one of each POS item.
MENU_STOCK = {
    "pkg-breeze":         {"absolut": 1, "sprite": 6},
    "pkg-ignition-ab":    {"absolut": 1, "redbull": 6},
    "pkg-ignition-jg":    {"jager": 1, "redbull": 6},
    "btl-absolut":        {"absolut": 1},
    "btl-jager":          {"jager": 1},
    "btl-jwblack":        {"jwblack": 1},
    "btl-greygoose":      {"greygoose": 1},
    "bev-water":          {"evian": 1},
    "bev-coke":           {"coke": 1},
    "bev-sprite":         {"sprite": 1},
    "bev-redbull":        {"redbull": 1},
    "ctl-black-coke":     {"coke": 1},
    "ctl-absolut-sprite": {"sprite": 1},
    "ctl-jager-redbull":  {"redbull": 1},
}
# Complimentary bottle / can names to stock.
COMP_STOCK = {
    "Absolut Vodka": "absolut", "Johnnie Walker Black Label": "jwblack", "Jagermeister": "jager",
    "Sprite": "sprite", "Coca-Cola": "coke", "Red Bull": "redbull",
}

class InventoryMoveIn(BaseModel):
    item: str
    # count: qty is the number on hand now. restock: qty added. open: bottles opened for
    # pouring. adjust: qty added (+) or removed (-), e.g. broken.
    kind: Literal["count", "restock", "open", "adjust"]
    qty: int = Field(..., ge=-500, le=5000)
    note: Optional[str] = Field(None, max_length=120)

def _ts(value) -> datetime:
    """Supabase timestamps (any number of fraction digits, Z or offset) to an aware datetime."""
    s = str(value or "").strip().replace("Z", "+00:00").replace(" ", "T", 1)
    m = re.match(r"^(.*?T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", s)
    if m:
        frac = (m.group(2) or "")[:7].ljust(7, "0") if m.group(2) else ""
        s = m.group(1) + frac + (m.group(3) or "")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def _all_rows(query_fn, page: int = 1000) -> list:
    """Every row of a select, past Supabase's 1000-row cap."""
    out, start = [], 0
    while True:
        rows = query_fn().range(start, start + page - 1).execute().data or []
        out += rows
        if len(rows) < page:
            return out
        start += page

def _inventory_state() -> dict:
    moves = sorted(db().table("inventory_moves").select("*").execute().data or [], key=lambda m: _ts(m["created_at"]))
    st = {k: {"base": 0, "base_at": None, "restocked": 0, "adjusted": 0, "opened": 0, "sold": 0, "comp": 0}
          for k in INVENTORY_ITEMS}
    for m in moves:
        it = st.get(m.get("item"))
        if not it:
            continue
        q = int(m.get("qty") or 0)
        if m["kind"] == "count":
            it.update(base=q, base_at=_ts(m["created_at"]), restocked=0, adjusted=0, opened=0)
        elif m["kind"] == "restock":
            it["restocked"] += q
        elif m["kind"] == "open":
            it["opened"] += q
        else:
            it["adjusted"] += q

    counted = [v["base_at"] for v in st.values() if v["base_at"]]
    if counted:
        since = min(counted).isoformat()
        orders = _all_rows(lambda: db().table("orders").select("id, items, status, created_at")
                           .gte("created_at", since).neq("status", "cancelled").order("created_at"))
        claims = _all_rows(lambda: db().table("comp_claims").select("item_name, created_at")
                           .gte("created_at", since).is_("voided_at", "null").order("created_at"))
        for o in orders:
            t = _ts(o["created_at"])
            for li in o.get("items") or []:
                for key, per in MENU_STOCK.get(li.get("id"), {}).items():
                    if st[key]["base_at"] and t > st[key]["base_at"]:
                        st[key]["sold"] += per * int(li.get("qty") or 0)
        for c in claims:
            key = COMP_STOCK.get(c.get("item_name") or "")
            if key and st[key]["base_at"] and _ts(c["created_at"]) > st[key]["base_at"]:
                st[key]["comp"] += 1

    items = []
    for key, (name, unit, low) in INVENTORY_ITEMS.items():
        v = st[key]
        on_hand = v["base"] + v["restocked"] + v["adjusted"] - v["opened"] - v["sold"] - v["comp"]
        items.append({
            "key": key, "name": name, "unit": unit, "low_at": low, "pour": key in POUR_ITEMS,
            "counted": v["base_at"] is not None, "count": v["base"],
            "counted_at": v["base_at"].isoformat() if v["base_at"] else None,
            "restocked": v["restocked"], "adjusted": v["adjusted"], "opened": v["opened"],
            "sold": v["sold"], "comp": v["comp"], "on_hand": on_hand,
        })
    on_hand = {i["key"]: i["on_hand"] for i in items if i["counted"]}
    # How many of each POS item can still be sold, for the POS menu.
    menu = {}
    for mid, uses in MENU_STOCK.items():
        caps = [max(0, on_hand[k]) // per for k, per in uses.items() if k in on_hand]
        if caps:
            menu[mid] = min(caps)
    recent = moves[-60:][::-1]
    for m in recent:
        m["name"] = INVENTORY_ITEMS.get(m.get("item"), (m.get("item"),))[0]
    return {"items": items, "menu_available": menu, "moves": recent}

@app.get("/api/inventory", dependencies=[Depends(require_staff)])
def get_inventory():
    return _inventory_state()

@app.post("/api/inventory/moves")
def add_inventory_move(payload: InventoryMoveIn, user=Depends(require_staff)):
    if payload.item not in INVENTORY_ITEMS:
        raise HTTPException(status_code=400, detail="Unknown inventory item.")
    k, q = payload.kind, payload.qty
    if k == "count" and q < 0:
        raise HTTPException(status_code=400, detail="A count can't be below 0.")
    if k in ("restock", "open") and q < 1:
        raise HTTPException(status_code=400, detail="Enter at least 1.")
    if k == "open" and payload.item not in POUR_ITEMS:
        raise HTTPException(status_code=400, detail="Only liquor for shots is opened for pouring.")
    if k == "adjust" and q == 0:
        raise HTTPException(status_code=400, detail="Enter how many to add or remove.")
    db().table("inventory_moves").insert({
        "item": payload.item, "kind": k, "qty": q,
        "note": (payload.note or "").strip() or None, "by_email": getattr(user, "email", None),
    }).execute()
    return _inventory_state()

# ---------------------------------------------------------------------------
#  POS — orders
# ---------------------------------------------------------------------------

@app.post("/api/orders", dependencies=[Depends(require_staff)])
def create_order(payload: OrderCreate):
    line_items = []
    total = 0
    for it in payload.items:
        found = POS_MENU_PRICES.get(it.id)
        if not found:
            raise HTTPException(status_code=400, detail=f"Unknown menu item: {it.id}")
        name, price = found
        line_items.append({"id": it.id, "name": name, "price": price, "qty": it.qty})
        total += price * it.qty

    row = {
        "table_name": payload.table_name,
        "items": line_items,
        "notes": payload.notes,
        "status": payload.status,
        "payment_method": payload.payment_method,
        "total_amount": total,
    }
    res = db().table("orders").insert(row).execute()
    if not res.data:
        raise HTTPException(status_code=502, detail="Failed to create order.")
    return res.data[0]

@app.get("/api/orders", dependencies=[Depends(require_staff)])
def list_orders(status_filter: Optional[str] = None, limit: int = Query(500, ge=1, le=5000)):
    # The bar polls with the default 500; the end-of-night export asks for more.
    query = db().table("orders").select("*").order("created_at", desc=True).limit(limit)
    if status_filter:
        query = query.eq("status", status_filter)
    return query.execute().data

@app.patch("/api/orders/{order_id}/status", dependencies=[Depends(require_staff)])
def update_order_status(order_id: str, payload: OrderStatusUpdate):
    update_data = {"status": payload.status}
    if payload.payment_method:
        update_data["payment_method"] = payload.payment_method
        
    res = db().table("orders").update(update_data).eq("id", order_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Order not found.")
    return res.data[0]

@app.post("/api/orders/{order_id}/receipt", dependencies=[Depends(require_staff)])
async def upload_order_receipt(order_id: str, receipt: UploadFile = File(...)):
    res = db().table("orders").select("id").eq("id", order_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Order not found.")
        
    content_type = (receipt.content_type or "").lower()
    if content_type not in ALLOWED_RECEIPT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type {content_type}. Please upload a JPG, PNG, WEBP, or PDF.",
        )

    file_bytes = await receipt.read()
    if len(file_bytes) > MAX_RECEIPT_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max size is 10MB.")
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file.")

    if not _is_valid_file_signature(file_bytes, content_type):
        raise HTTPException(status_code=400, detail="File content doesn't match its type.")

    file_ext = ALLOWED_RECEIPT_TYPES[content_type]
    file_name = f"order_{order_id}_{secrets.token_hex(4)}{file_ext}"

    try:
        db().storage.from_("order receipts").upload(
            file_name, file_bytes, {"content-type": content_type}
        )
        receipt_url = db().storage.from_("order receipts").get_public_url(file_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not upload receipt: {str(e)}")

    res = db().table("orders").update({"receipt_url": receipt_url}).eq("id", order_id).execute()
    return res.data[0]


# ---------------------------------------------------------------------------
#  FUTURE-EVENT INVITES
#  Guests unsubscribe by replying to an invite / emailing
#  exclusives.est2023@gmail.com. To remove someone, run in Supabase:
#    update bookings set marketing_opt_in = false where lower(email) = lower('their@email.com');
# ---------------------------------------------------------------------------

@app.get("/api/marketing/opt-ins", dependencies=[Depends(require_admin)])
def list_marketing_opt_ins():
    """Admin: everyone currently opted in (one row per email, latest booking wins)."""
    rows = db().table("bookings").select("full_name, email, marketing_opt_in, created_at") \
        .eq("marketing_opt_in", True).order("created_at", desc=True).execute().data or []
    seen, out = set(), []
    for r in rows:
        key = (r.get("email") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"full_name": r.get("full_name"), "email": key})
    return {"count": len(out), "guests": out}