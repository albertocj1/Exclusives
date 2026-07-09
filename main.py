import os
import json
import base64
import secrets
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional
from email.message import EmailMessage

from fastapi import Depends, FastAPI, Header, HTTPException, status, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from supabase import Client, create_client

# Google API Imports
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

EVENT_CAPACITY = int(os.getenv("EVENT_CAPACITY", "300"))
PACKAGES = {
    "Entrance Fee":           {"price": 2500,  "per": "person", "max_guests": 8},
    "Standing Table (4 pax)": {"price": 8000,  "per": "table",  "max_guests": 4},
    "Couch (6 pax)":          {"price": 15000, "per": "table",  "max_guests": 6},
    "Couch (8 pax)":          {"price": 20000, "per": "table",  "max_guests": 8},
}

_client: Optional[Client] = None
def db() -> Client:
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return _client

# --- GMAIL API SERVICE HELPER ---
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

def get_gmail_service():
    creds = None
    # 1. First attempt to read the token from environment variables (Production style)
    token_json_str = os.environ.get("GMAIL_TOKEN_JSON")
    
    if token_json_str:
        token_dict = json.loads(token_json_str)
        creds = Credentials.from_authorized_user_info(token_dict, SCOPES)
    # 2. Fallback to local token.json if the env var isn't found (Local testing style)
    elif os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # 3. Refresh token if expired
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        
    if not creds or not creds.valid:
        raise Exception("Gmail credentials are not valid. Ensure GMAIL_TOKEN_JSON env variable or local token.json is present.")
            
    return build('gmail', 'v1', credentials=creds)

def send_approval_email(to_email: str, guest_name: str, ticket_code: str, package_name: str):
    """Constructs and sends the confirmation email in the background."""
    try:
        service = get_gmail_service()
        
        message = EmailMessage()
        content = (
            f"Hi {guest_name},\n\n"
            f"Your payment has been successfully verified!\n"
            f"Your booking for '{package_name}' is officially confirmed.\n\n"
            f"🎟️ YOUR EXCLUSIVE TICKET CODE IS:\n"
            f"👉 {ticket_code} 👈\n\n"
            f"Please present this code at the entrance upon arrival. See you at Manila Bay!\n\n"
            f"Best regards,\n"
            f"Exclusives PH Team"
        )
        message.set_content(content)
        message['To'] = to_email
        message['From'] = os.environ.get("SENDER_EMAIL", "your-email@gmail.com") 
        message['Subject'] = "Booking Confirmed — Exclusives PH"

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {'raw': encoded_message}

        service.users().messages().send(userId="me", body=create_message).execute()
        print(f"Successfully sent confirmation email to {to_email}")
    except Exception as e:
        print(f"ERROR sending email to {to_email}: {str(e)}")

# --- PYDANTIC SCHEMAS ---
class BookingCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)
    instagram: Optional[str] = None
    referrer: Optional[str] = None
    package: str
    table_id: Optional[str] = None
    guests: int = Field(..., ge=1, le=8)
    accept_terms: bool

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
        max_guests = PACKAGES[self.package]["max_guests"]
        if self.guests > max_guests:
            raise ValueError(f"{self.package} allows at most {max_guests} guests.")
        if PACKAGES[self.package]["per"] == "table" and not self.table_id:
            raise ValueError("This package requires selecting a table.")
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
    created_at: datetime

app = FastAPI()

# Dynamically handle allowed origins from your .env string
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

def require_admin(x_admin_key: str = Header(default="")):
    if not secrets.compare_digest(x_admin_key, os.environ.get("ADMIN_API_KEY", "")):
        raise HTTPException(status_code=401, detail="Invalid admin key.")

def _get_booking(booking_id: str):
    res = db().table("bookings").select("*").eq("id", booking_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Not found.")
    return res.data[0]

def _confirmed_guest_count() -> int:
    res = db().table("bookings").select("guests").eq("status", "confirmed").execute()
    return sum(row["guests"] for row in (res.data or []))

@app.get("/api/availability")
def availability():
    taken = _confirmed_guest_count()
    return {"capacity": EVENT_CAPACITY, "taken": taken, "spots_left": max(0, EVENT_CAPACITY - taken)}

@app.get("/api/tables/availability")
def get_tables():
    all_tables = db().table("tables").select("*").execute().data
    lock_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

    confirmed = db().table("bookings").select("table_id") \
        .eq("status", "confirmed") \
        .not_.is_("table_id", "null") \
        .execute()

    holds = db().table("bookings").select("table_id") \
        .in_("status", ["pending", "verifying"]) \
        .gte("created_at", lock_cutoff) \
        .not_.is_("table_id", "null") \
        .execute()

    taken_ids = {r["table_id"] for r in (confirmed.data or []) if r.get("table_id")}
    taken_ids |= {r["table_id"] for r in (holds.data or []) if r.get("table_id")}

    for t in all_tables:
        t["is_available"] = t["id"] not in taken_ids
    return {"tables": all_tables}

@app.post("/api/bookings", response_model=Booking)
def create_booking(payload: BookingCreate):
    if payload.table_id:
        lock_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

        confirmed = db().table("bookings").select("id") \
            .eq("table_id", payload.table_id) \
            .eq("status", "confirmed") \
            .execute()

        held = db().table("bookings").select("id") \
            .eq("table_id", payload.table_id) \
            .in_("status", ["pending", "verifying"]) \
            .gte("created_at", lock_cutoff) \
            .execute()

        if confirmed.data or held.data:
            raise HTTPException(status_code=409, detail="Table just got reserved by someone else.")

    unit = PACKAGES[payload.package]["price"]
    total = unit * payload.guests if PACKAGES[payload.package]["per"] == "person" else unit

    try:
        res = db().table("bookings").insert({
            "full_name": payload.full_name, "email": payload.email, "phone": payload.phone,
            "instagram": payload.instagram, "referrer": payload.referrer,
            "package": payload.package, "table_id": payload.table_id, "guests": payload.guests,
            "unit_price": unit, "total_amount": total, "status": "pending",
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

    file_bytes = await receipt.read()
    file_ext = receipt.filename.split(".")[-1] if "." in receipt.filename else "jpg"
    file_name = f"{booking_id}_{secrets.token_hex(4)}.{file_ext}"

    try:
        db().storage.from_("receipts").upload(file_name, file_bytes, {"content-type": receipt.content_type})
        receipt_url = db().storage.from_("receipts").get_public_url(file_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not upload receipt: {str(e)}")

    res = db().table("bookings").update({
        "status": "verifying", "receipt_url": receipt_url
    }).eq("id", booking_id).execute()
    return Booking(**res.data[0])

@app.get("/api/bookings", dependencies=[Depends(require_admin)])
def list_bookings():
    return db().table("bookings").select("*").order("created_at", desc=True).limit(1000).execute().data

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
        if "23505" in str(e) or "duplicate key" in str(e).lower(): # Fixed syntax error typo here
            raise HTTPException(
                status_code=409,
                detail="That table is already confirmed for another guest. Cancel one before approving.",
            )
        raise

    if res.data:
        booking_data = res.data[0]
        
        # Dispatch email script into FastAPI background runner
        background_tasks.add_task(
            send_approval_email,
            to_email=booking_data["email"],
            guest_name=booking_data["full_name"],
            ticket_code=booking_data["ticket_code"],
            package_name=booking_data["package"]
        )
        
        return Booking(**booking_data)
    raise HTTPException(status_code=502, detail="Failed to issue ticket.")

@app.post("/api/bookings/{booking_id}/cancel", dependencies=[Depends(require_admin)])
def cancel_booking(booking_id: str):
    res = db().table("bookings").update({"status": "cancelled"}).eq("id", booking_id).execute()
    return Booking(**res.data[0])