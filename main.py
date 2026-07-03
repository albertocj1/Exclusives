"""
Exclusives PH — Manila Bay Yacht Sessions
Single-file FastAPI backend (Supabase database + PayMongo payments)

FLOWS
-----
  POST   /api/bookings                create a pending booking (RSVP form)
  POST   /api/bookings/{id}/checkout  create a PayMongo checkout session -> checkout_url
  GET    /api/bookings/{id}/verify    ask PayMongo if paid; if so, confirm + issue ticket
  POST   /api/webhooks/paymongo       PayMongo -> us: confirm on 'checkout_session.payment.paid'
  GET    /api/bookings/{id}           fetch a booking / ticket
  GET    /api/availability            capacity / taken / spots_left (drives the bar)

  Admin (all require the  X-Admin-Key  header = ADMIN_API_KEY):
  GET    /api/bookings                list bookings (optional ?status_filter=)
  POST   /api/bookings/{id}/cancel    cancel a booking (frees its spots)

  GET    /health                      liveness probe

SETUP
-----
1) Run SUPABASE_SCHEMA in the Supabase SQL editor (fresh install), OR if you
   already created the table earlier, run SUPABASE_MIGRATION to add the new
   columns. Print either with:
       python main.py --print-schema
       python main.py --print-migration

2) Environment (.env):
     SUPABASE_URL          = https://<project>.supabase.co
     SUPABASE_KEY          = <service_role / secret key>
     ADMIN_API_KEY         = <long random string>       # protects admin routes
     PAYMONGO_SECRET_KEY   = sk_test_xxx                 # from PayMongo dashboard
     PUBLIC_BASE_URL       = http://127.0.0.1:5502       # where index.html is served
     EVENT_CAPACITY        = 300                         # optional
     ALLOWED_ORIGINS       = http://127.0.0.1:5502,...   # optional
     PAYMONGO_WEBHOOK_SECRET = whsk_xxx                  # optional (production webhooks)

3) Install + run:
     pip install "fastapi[standard]" supabase "pydantic[email]" httpx
     uvicorn main:app --reload --port 8000 --env-file .env
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from supabase import Client, create_client

# --------------------------------------------------------------------------- #
# Database schema
# --------------------------------------------------------------------------- #
SUPABASE_SCHEMA = """
create table if not exists public.bookings (
    id                  uuid primary key default gen_random_uuid(),
    full_name           text        not null,
    email               text        not null,
    phone               text        not null,
    instagram           text,
    package             text        not null,
    guests              integer     not null check (guests >= 1),
    unit_price          integer     not null,
    total_amount        integer     not null,
    status              text        not null default 'pending'
                            check (status in ('pending', 'confirmed', 'cancelled')),
    payment_method      text,
    checkout_session_id text,
    ticket_code         text        unique,
    created_at          timestamptz not null default now(),
    confirmed_at        timestamptz
);

create index if not exists bookings_status_idx on public.bookings (status);
create index if not exists bookings_email_idx  on public.bookings (email);
create index if not exists bookings_cs_idx     on public.bookings (checkout_session_id);
"""

# For projects that already ran the older schema: add the new column and relax
# the payment_method check so PayMongo values (paymaya, card, ...) are allowed.
SUPABASE_MIGRATION = """
alter table public.bookings add column if not exists checkout_session_id text;
alter table public.bookings drop constraint if exists bookings_payment_method_check;
create index if not exists bookings_cs_idx on public.bookings (checkout_session_id);
"""

# --------------------------------------------------------------------------- #
# Event configuration — source of truth for pricing and capacity
# --------------------------------------------------------------------------- #
PACKAGES: dict[str, dict] = {
    "Entrance Fee":           {"price": 2500,  "per": "person", "max_guests": 8},
    "Standing Table (4 pax)": {"price": 8000,  "per": "table",  "max_guests": 4},
    "Couch (6 pax)":          {"price": 15000, "per": "table",  "max_guests": 6},
    "Couch (8 pax)":          {"price": 20000, "per": "table",  "max_guests": 8},
}
PackageName = Literal[
    "Entrance Fee",
    "Standing Table (4 pax)",
    "Couch (6 pax)",
    "Couch (8 pax)",
]

EVENT_CAPACITY = int(os.getenv("EVENT_CAPACITY", "300"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:5502").rstrip("/")
PAYMONGO_BASE = "https://api.paymongo.com/v1"


def price_for(package: str, guests: int) -> tuple[int, int]:
    """Return (unit_price, total_amount) in pesos."""
    cfg = PACKAGES[package]
    unit = cfg["price"]
    total = unit * guests if cfg["per"] == "person" else unit
    return unit, total


# --------------------------------------------------------------------------- #
# Supabase client (lazy)
# --------------------------------------------------------------------------- #
_client: Optional[Client] = None


def db() -> Client:
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase is not configured (set SUPABASE_URL and SUPABASE_KEY).",
            )
        _client = create_client(url, key)
    return _client


# --------------------------------------------------------------------------- #
# PayMongo helpers
# --------------------------------------------------------------------------- #
def _paymongo(method: str, path: str, body: Optional[dict] = None) -> dict:
    key = os.environ.get("PAYMONGO_SECRET_KEY")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PayMongo is not configured (set PAYMONGO_SECRET_KEY).",
        )
    token = base64.b64encode(f"{key}:".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    try:
        r = httpx.request(method, f"{PAYMONGO_BASE}{path}", headers=headers, json=body, timeout=30)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not reach PayMongo: {exc}")
    if r.status_code >= 400:
        try:
            errs = r.json().get("errors", [])
            msg = "; ".join(e.get("detail", "") for e in errs) or r.text
        except Exception:
            msg = r.text
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"PayMongo error: {msg}")
    return r.json()["data"]


def _create_checkout_session(booking: dict) -> tuple[str, str]:
    """Create a PayMongo checkout session; return (session_id, checkout_url)."""
    body = {
        "data": {
            "attributes": {
                "line_items": [
                    {
                        "name": booking["package"],
                        "amount": int(booking["total_amount"]) * 100,  # centavos
                        "currency": "PHP",
                        "quantity": 1,
                    }
                ],
                "payment_method_types": ["gcash", "paymaya", "card"],
                "success_url": f"{PUBLIC_BASE_URL}/index.html?booking_id={booking['id']}&paid=1",
                "cancel_url": f"{PUBLIC_BASE_URL}/index.html?booking_id={booking['id']}&cancelled=1",
                "description": f"Exclusives PH — {booking['package']} ({booking['guests']} pax)",
                "reference_number": booking["id"],
            }
        }
    }
    data = _paymongo("POST", "/checkout_sessions", body)
    return data["id"], data["attributes"]["checkout_url"]


def _paid_payment(session: dict) -> Optional[dict]:
    """Return the first paid payment on a checkout session, else None."""
    for p in session.get("attributes", {}).get("payments", []):
        if p.get("attributes", {}).get("status") == "paid":
            return p
    return None


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class BookingCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)
    instagram: Optional[str] = Field(None, max_length=60)
    package: PackageName
    guests: int = Field(..., ge=1, le=8)
    accept_terms: bool = Field(..., description="Guest confirms 18+ and safety directives.")

    @field_validator("accept_terms")
    @classmethod
    def _terms_required(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("You must confirm the 18+ and safety terms to book.")
        return v


class Booking(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    phone: str
    instagram: Optional[str] = None
    package: str
    guests: int
    unit_price: int
    total_amount: int
    status: str
    payment_method: Optional[str] = None
    checkout_session_id: Optional[str] = None
    ticket_code: Optional[str] = None
    created_at: datetime
    confirmed_at: Optional[datetime] = None


class Availability(BaseModel):
    capacity: int
    taken: int
    spots_left: int


class CheckoutResponse(BaseModel):
    checkout_url: str


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
app = FastAPI(title="Exclusives PH API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_admin(x_admin_key: str = Header(default="")) -> None:
    expected = os.environ.get("ADMIN_API_KEY")
    if not expected or not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key.")


def _confirmed_guest_count() -> int:
    res = db().table("bookings").select("guests").eq("status", "confirmed").execute()
    return sum(row["guests"] for row in (res.data or []))


def _get_booking(booking_id: str) -> dict:
    res = db().table("bookings").select("*").eq("id", booking_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")
    return res.data[0]


def _new_ticket_code() -> str:
    return "EXC-" + secrets.token_hex(3).upper()


def _confirm(booking: dict, payment_method: Optional[str]) -> dict:
    """Mark a booking confirmed and issue a unique ticket code (idempotent)."""
    if booking["status"] == "confirmed":
        return booking
    if booking["guests"] > (EVENT_CAPACITY - _confirmed_guest_count()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Spots sold out before payment completed.")
    update = {
        "status": "confirmed",
        "payment_method": payment_method,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    for _ in range(5):
        try:
            update["ticket_code"] = _new_ticket_code()
            done = db().table("bookings").update(update).eq("id", booking["id"]).execute()
            if done.data:
                return done.data[0]
        except Exception:  # unique ticket_code collision -> retry
            continue
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not confirm booking.")


# --------------------------------------------------------------------------- #
# Public routes
# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/availability", response_model=Availability)
def availability() -> Availability:
    taken = _confirmed_guest_count()
    return Availability(capacity=EVENT_CAPACITY, taken=taken, spots_left=max(0, EVENT_CAPACITY - taken))


@app.post("/api/bookings", response_model=Booking, status_code=status.HTTP_201_CREATED)
def create_booking(payload: BookingCreate) -> Booking:
    cfg = PACKAGES[payload.package]
    if payload.guests > cfg["max_guests"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{payload.package} allows at most {cfg['max_guests']} guests.",
        )
    if payload.guests > (EVENT_CAPACITY - _confirmed_guest_count()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough spots left for that number of guests.")

    unit, total = price_for(payload.package, payload.guests)
    record = {
        "full_name": payload.full_name.strip(),
        "email": payload.email,
        "phone": payload.phone.strip(),
        "instagram": (payload.instagram or "").strip() or None,
        "package": payload.package,
        "guests": payload.guests,
        "unit_price": unit,
        "total_amount": total,
        "status": "pending",
    }
    res = db().table("bookings").insert(record).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not create booking.")
    return Booking(**res.data[0])


@app.post("/api/bookings/{booking_id}/checkout", response_model=CheckoutResponse)
def start_checkout(booking_id: str) -> CheckoutResponse:
    booking = _get_booking(booking_id)
    if booking["status"] == "confirmed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Booking is already paid.")
    if booking["status"] == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Booking was cancelled.")

    session_id, checkout_url = _create_checkout_session(booking)
    db().table("bookings").update({"checkout_session_id": session_id}).eq("id", booking_id).execute()
    return CheckoutResponse(checkout_url=checkout_url)


@app.get("/api/bookings/{booking_id}/verify", response_model=Booking)
def verify_payment(booking_id: str) -> Booking:
    booking = _get_booking(booking_id)
    if booking["status"] == "confirmed":
        return Booking(**booking)
    if not booking.get("checkout_session_id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No checkout started for this booking.")

    session = _paymongo("GET", f"/checkout_sessions/{booking['checkout_session_id']}")
    payment = _paid_payment(session)
    if not payment:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Payment not completed yet.")

    method = payment.get("attributes", {}).get("source", {}).get("type") or "paymongo"
    return Booking(**_confirm(booking, method))


@app.get("/api/bookings/{booking_id}", response_model=Booking)
def get_booking(booking_id: str) -> Booking:
    return Booking(**_get_booking(booking_id))


# --------------------------------------------------------------------------- #
# PayMongo webhook (production; optional for local dev)
# --------------------------------------------------------------------------- #
@app.post("/api/webhooks/paymongo")
async def paymongo_webhook(request: Request) -> dict:
    raw = await request.body()

    secret = os.environ.get("PAYMONGO_WEBHOOK_SECRET")
    if secret:
        sig = request.headers.get("Paymongo-Signature", "")
        parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
        t = parts.get("t", "")
        provided = parts.get("te") or parts.get("li") or ""
        expected = hmac.new(secret.encode(), f"{t}.{raw.decode()}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad webhook signature.")

    try:
        event = json.loads(raw)
        attrs = event["data"]["attributes"]
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed webhook payload.")

    if attrs.get("type") == "checkout_session.payment.paid":
        session = attrs.get("data", {})
        session_id = session.get("id")
        payment = _paid_payment(session)
        method = (payment or {}).get("attributes", {}).get("source", {}).get("type") or "paymongo"
        if session_id:
            res = db().table("bookings").select("*").eq("checkout_session_id", session_id).limit(1).execute()
            if res.data and res.data[0]["status"] != "confirmed":
                _confirm(res.data[0], method)

    return {"received": True}


# --------------------------------------------------------------------------- #
# Admin routes  (require X-Admin-Key header)
# --------------------------------------------------------------------------- #
@app.get("/api/bookings", response_model=list[Booking], dependencies=[Depends(require_admin)])
def list_bookings(
    status_filter: Optional[Literal["pending", "confirmed", "cancelled"]] = None,
    limit: int = 200,
) -> list[Booking]:
    query = db().table("bookings").select("*").order("created_at", desc=True).limit(min(limit, 1000))
    if status_filter:
        query = query.eq("status", status_filter)
    res = query.execute()
    return [Booking(**row) for row in (res.data or [])]


@app.post("/api/bookings/{booking_id}/cancel", response_model=Booking, dependencies=[Depends(require_admin)])
def cancel_booking(booking_id: str) -> Booking:
    _get_booking(booking_id)  # 404 if missing
    done = db().table("bookings").update({"status": "cancelled"}).eq("id", booking_id).execute()
    if not done.data:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not cancel booking.")
    return Booking(**done.data[0])


# --------------------------------------------------------------------------- #
# CLI helpers
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if "--print-schema" in sys.argv:
        print(SUPABASE_SCHEMA)
    elif "--print-migration" in sys.argv:
        print(SUPABASE_MIGRATION)
    else:
        import uvicorn

        uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)