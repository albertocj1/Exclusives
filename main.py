"""
Exclusives PH — Manila Bay Yacht Sessions
Single-file FastAPI backend (Supabase as database)

WHAT THIS SERVES
----------------
The landing page collects RSVPs, computes a price per package, takes a
"payment", issues a ticket code, and shows a live "spots left" counter.
This backend implements exactly those flows:

  POST   /api/bookings            create a pending booking (the RSVP form)
  POST   /api/bookings/{id}/pay   confirm payment -> issue ticket code
  GET    /api/bookings/{id}       fetch a booking / ticket
  GET    /api/availability        total / taken / spots_left  (drives the bar)
  GET    /api/bookings            list all bookings  (admin, needs X-Admin-Key)
  GET    /health                  liveness probe

SETUP
-----
1) Create the table in Supabase (SQL editor). Run the schema in SUPABASE_SCHEMA
   below (also printed by `python main.py --print-schema`).

2) Environment variables (e.g. a .env, or exported in the shell):
     SUPABASE_URL          = https://<project>.supabase.co
     SUPABASE_KEY          = <service_role / secret key>   # server-side only, keep secret
     ADMIN_API_KEY         = <any long random string>   # protects GET /api/bookings
     EVENT_CAPACITY        = 300        # optional, default 300
     ALLOWED_ORIGINS       = https://yourdomain.com,http://localhost:5500  # optional

3) Install and run:
     pip install "fastapi[standard]" supabase "pydantic[email]"
     uvicorn main:app --reload --port 8000 --env-file .env

   Interactive docs then live at http://localhost:8000/docs
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from supabase import Client, create_client

# --------------------------------------------------------------------------- #
# Supabase table definition (run once in the Supabase SQL editor)
# --------------------------------------------------------------------------- #
SUPABASE_SCHEMA = """
create table if not exists public.bookings (
    id              uuid primary key default gen_random_uuid(),
    full_name       text        not null,
    email           text        not null,
    phone           text        not null,
    instagram       text,
    package         text        not null,
    guests          integer     not null check (guests >= 1),
    unit_price      integer     not null,
    total_amount    integer     not null,
    status          text        not null default 'pending'
                        check (status in ('pending', 'confirmed', 'cancelled')),
    payment_method  text        check (payment_method in ('gcash', 'maya')),
    ticket_code     text        unique,
    created_at      timestamptz not null default now(),
    confirmed_at    timestamptz
);

create index if not exists bookings_status_idx on public.bookings (status);
create index if not exists bookings_email_idx  on public.bookings (email);
"""

# --------------------------------------------------------------------------- #
# Event configuration — the source of truth for pricing and capacity
# --------------------------------------------------------------------------- #
# For "table" packages the price is flat regardless of headcount; for the
# per-person entrance fee the total scales with the number of guests.
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


def price_for(package: str, guests: int) -> tuple[int, int]:
    """Return (unit_price, total_amount) for a package + headcount."""
    cfg = PACKAGES[package]
    unit = cfg["price"]
    total = unit * guests if cfg["per"] == "person" else unit
    return unit, total


# --------------------------------------------------------------------------- #
# Supabase client (lazy so the app can import without a live connection)
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
# Request / response models
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


class PaymentRequest(BaseModel):
    payment_method: Literal["gcash", "maya"]
    payment_phone: str = Field(..., min_length=7, max_length=20)


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
    ticket_code: Optional[str] = None
    created_at: datetime
    confirmed_at: Optional[datetime] = None


class Availability(BaseModel):
    capacity: int
    taken: int
    spots_left: int


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
app = FastAPI(title="Exclusives PH API", version="1.1.0")

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
    """Sum of guests across confirmed bookings (what counts against capacity)."""
    res = db().table("bookings").select("guests").eq("status", "confirmed").execute()
    return sum(row["guests"] for row in (res.data or []))


def _new_ticket_code() -> str:
    return "EXC-" + secrets.token_hex(3).upper()  # e.g. EXC-A1B2C3


# --------------------------------------------------------------------------- #
# Routes
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

    # Soft capacity guard against already-confirmed guests.
    if payload.guests > (EVENT_CAPACITY - _confirmed_guest_count()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Not enough spots left for that number of guests.",
        )

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


@app.get("/api/bookings/{booking_id}", response_model=Booking)
def get_booking(booking_id: str) -> Booking:
    res = db().table("bookings").select("*").eq("id", booking_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")
    return Booking(**res.data[0])


@app.post("/api/bookings/{booking_id}/pay", response_model=Booking)
def pay_booking(booking_id: str, payload: PaymentRequest) -> Booking:
    res = db().table("bookings").select("*").eq("id", booking_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")

    booking = res.data[0]
    if booking["status"] == "confirmed":
        return Booking(**booking)  # idempotent: already paid, return existing ticket
    if booking["status"] == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Booking was cancelled.")

    # Re-check capacity at the moment of payment.
    if booking["guests"] > (EVENT_CAPACITY - _confirmed_guest_count()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Spots sold out before payment completed.")

    # Issue a unique ticket code (retry on the rare collision).
    update = {
        "status": "confirmed",
        "payment_method": payload.payment_method,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    for _ in range(5):
        try:
            update["ticket_code"] = _new_ticket_code()
            done = db().table("bookings").update(update).eq("id", booking_id).execute()
            if done.data:
                return Booking(**done.data[0])
        except Exception:  # unique violation on ticket_code -> try a new code
            continue
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not confirm payment.")


@app.get("/api/bookings", response_model=list[Booking], dependencies=[Depends(require_admin)])
def list_bookings(
    status_filter: Optional[Literal["pending", "confirmed", "cancelled"]] = None,
    limit: int = 100,
) -> list[Booking]:
    query = db().table("bookings").select("*").order("created_at", desc=True).limit(min(limit, 500))
    if status_filter:
        query = query.eq("status", status_filter)
    res = query.execute()
    return [Booking(**row) for row in (res.data or [])]


# --------------------------------------------------------------------------- #
# CLI helper: `python main.py --print-schema`
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if "--print-schema" in sys.argv:
        print(SUPABASE_SCHEMA)
    else:
        import uvicorn

        uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)