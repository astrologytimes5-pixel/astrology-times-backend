from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, UploadFile, File, Query, Header
from fastapi.responses import JSONResponse, FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import os
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
import razorpay
import requests as http_requests
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import inch
import uuid

from models import (
    User, UserSession, AstrologyBooking, VastuBooking, SiteVisitEnquiry,
    Invoice, SessionReport, BlogPost, WalletTransaction, Referral
)
from auth import (
    hash_password, verify_password, create_jwt_token, get_current_user,
    exchange_session_id
)

# Object Storage
STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
APP_NAME = "astrology-times"
storage_key = None

def init_storage():
    global storage_key
    if storage_key:
        return storage_key
    emergent_key = os.environ.get("EMERGENT_LLM_KEY")
    resp = http_requests.post(f"{STORAGE_URL}/init", json={"emergent_key": emergent_key}, timeout=30)
    resp.raise_for_status()
    storage_key = resp.json()["storage_key"]
    return storage_key

def put_object(path: str, data: bytes, content_type: str) -> dict:
    key = init_storage()
    resp = http_requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data, timeout=120
    )
    resp.raise_for_status()
    return resp.json()

def get_object(path: str):
    key = init_storage()
    resp = http_requests.get(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key}, timeout=60
    )
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@astrologytimes.co.in")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin1234")

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

razorpay_client = razorpay.Client(auth=(
    os.environ.get("RAZORPAY_KEY_ID"),
    os.environ.get("RAZORPAY_KEY_SECRET")
))

ASTROLOGY_PACKAGES = {
    "basic": {"name": "Basic Reading", "duration": 30, "price": 1500},
    "standard": {"name": "Standard Reading", "duration": 60, "price": 2500},
    "marriage": {"name": "Marriage Consultation", "duration": 45, "price": 2000},
    "career": {"name": "Career & Business", "duration": 45, "price": 2000},
    "dasha": {"name": "Dasha & Remedies", "duration": 60, "price": 3000},
}

VASTU_PACKAGES = {
    "basic_report": {"name": "Basic Vastu Report", "price": 7000},
    "home_video": {"name": "Home Vastu Video Call", "price": 9000},
    "office_video": {"name": "Office/Shop Vastu Video Call", "price": 11000},
    "factory_video": {"name": "Factory/Industrial Vastu Video Call", "price": 15000},
}

class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class SessionExchangeRequest(BaseModel):
    session_id: str

class BookingRequest(BaseModel):
    package_type: str
    booking_date: str
    booking_time: str
    info_box: Optional[str] = None

class VastuBookingRequest(BaseModel):
    service_type: str
    booking_date: str
    booking_time: str
    info_box: Optional[str] = None

class SiteVisitRequest(BaseModel):
    name: str
    phone: str
    city: str
    state: str
    property_type: str
    property_size: str
    preferred_visit_date: str
    issues: Optional[str] = None

class WalletRechargeRequest(BaseModel):
    amount: float

class ReferralRedeemRequest(BaseModel):
    referral_code: str

class SessionTimerRequest(BaseModel):
    booking_id: str
    action: str
    notes: Optional[str] = None

class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    dob: Optional[str] = None
    time_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    gender: Optional[str] = None
    language: Optional[str] = None

class AdminLoginRequest(BaseModel):
    email: str
    password: str

class BlogCreateRequest(BaseModel):
    title_en: str
    title_hi: str
    content_en: str
    content_hi: str
    category: str
    image_url: Optional[str] = None

class AdminBookingActionRequest(BaseModel):
    booking_id: str
    booking_type: str
    action: str
    meet_link: Optional[str] = None

class AdminCustomInvoiceRequest(BaseModel):
    enquiry_id: str
    travel_cost: float
    stay_cost: float
    consultation_fee: float
    description: Optional[str] = None

class AdminAvailabilityRequest(BaseModel):
    date: str
    available: bool
    custom_slots: Optional[List[str]] = None

class KundliRequest(BaseModel):
    name: str
    dob: str
    time_of_birth: str
    place_of_birth: str
    gender: str

# Time slot definitions
WEEKDAY_SLOTS = ["11:00 AM", "12:00 PM", "3:00 PM", "4:00 PM", "5:00 PM", "6:00 PM"]
SUNDAY_SLOTS = ["11:00 AM", "12:00 PM", "1:00 PM", "2:00 PM"]
MAX_BOOKINGS_PER_DAY = 3

async def seed_admin():
    existing = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
    if not existing:
        admin = User(
            email=ADMIN_EMAIL,
            name="Admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            language="en"
        )
        admin_dict = admin.model_dump()
        admin_dict["created_at"] = admin_dict["created_at"].isoformat()
        admin_dict["is_admin"] = True
        await db.users.insert_one(admin_dict)
        logger.info("Admin user seeded")
    elif not existing.get("is_admin"):
        await db.users.update_one({"email": ADMIN_EMAIL}, {"$set": {"is_admin": True}})

async def check_admin(request: Request):
    user = await get_current_user(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

@app.on_event("startup")
async def startup():
    try:
        init_storage()
        logger.info("Object storage initialized")
    except Exception as e:
        logger.warning(f"Storage init failed: {e}")
    await seed_admin()
    await seed_blog_posts()

@api_router.get("/")
async def root():
    return {"message": "Astrology Times API"}

@api_router.post("/auth/signup")
async def signup(data: SignupRequest):
    existing_user = await db.users.find_one({"email": data.email}, {"_id": 0})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    user = User(
        email=data.email,
        name=data.name,
        password_hash=hash_password(data.password)
    )
    
    user_dict = user.model_dump()
    user_dict["created_at"] = user_dict["created_at"].isoformat()
    await db.users.insert_one(user_dict)
    
    token = create_jwt_token(user.user_id, user.email)
    
    session = UserSession(
        user_id=user.user_id,
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7)
    )
    session_dict = session.model_dump()
    session_dict["created_at"] = session_dict["created_at"].isoformat()
    session_dict["expires_at"] = session_dict["expires_at"].isoformat()
    await db.user_sessions.insert_one(session_dict)
    
    user_data = await db.users.find_one({"user_id": user.user_id}, {"_id": 0, "password_hash": 0})
    
    return {"user": user_data, "token": token}

@api_router.post("/auth/login")
async def login(data: LoginRequest):
    user_doc = await db.users.find_one({"email": data.email}, {"_id": 0})
    if not user_doc or not user_doc.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not verify_password(data.password, user_doc["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_jwt_token(user_doc["user_id"], user_doc["email"])
    
    session = UserSession(
        user_id=user_doc["user_id"],
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7)
    )
    session_dict = session.model_dump()
    session_dict["created_at"] = session_dict["created_at"].isoformat()
    session_dict["expires_at"] = session_dict["expires_at"].isoformat()
    await db.user_sessions.insert_one(session_dict)
    
    user_data = await db.users.find_one({"user_id": user_doc["user_id"]}, {"_id": 0, "password_hash": 0})
    
    return {"user": user_data, "token": token}

@api_router.post("/auth/google/callback")
async def google_auth_callback(data: SessionExchangeRequest, response: Response):
    result = await exchange_session_id(data.session_id, db)
    
    response.set_cookie(
        key="session_token",
        value=result["session_token"],
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
        max_age=7*24*60*60
    )
    
    return result

@api_router.get("/auth/me")
async def get_me(request: Request):
    user = await get_current_user(request, db)
    return user

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    session_token = request.cookies.get("session_token")
    if session_token:
        await db.user_sessions.delete_one({"session_token": session_token})
    
    response.delete_cookie("session_token")
    return {"message": "Logged out"}

@api_router.get("/packages/astrology")
async def get_astrology_packages():
    return {
        "packages": ASTROLOGY_PACKAGES,
        "per_minute_rate": 60,
        "first_time_free_minutes": 10
    }

@api_router.get("/packages/vastu")
async def get_vastu_packages():
    return {"packages": VASTU_PACKAGES}

@api_router.post("/bookings/astrology")
async def create_astrology_booking(data: BookingRequest, request: Request):
    user = await get_current_user(request, db)
    
    if data.package_type == "per_minute":
        raise HTTPException(status_code=400, detail="Per-minute bookings use wallet system")
    
    if data.package_type not in ASTROLOGY_PACKAGES:
        raise HTTPException(status_code=400, detail="Invalid package type")
    
    package = ASTROLOGY_PACKAGES[data.package_type]
    amount = package["price"]
    
    if user.get("first_time_free_used") == False:
        amount = max(0, amount - (10 * 60))
    
    booking = AstrologyBooking(
        user_id=user["user_id"],
        package_type=data.package_type,
        duration_minutes=package["duration"],
        amount=amount,
        booking_date=data.booking_date,
        booking_time=data.booking_time,
        info_box=data.info_box
    )
    
    razorpay_order = razorpay_client.order.create({
        "amount": int(amount * 100),
        "currency": "INR",
        "receipt": booking.booking_id
    })
    
    booking.razorpay_order_id = razorpay_order["id"]
    
    booking_dict = booking.model_dump()
    booking_dict["created_at"] = booking_dict["created_at"].isoformat()
    await db.astrology_bookings.insert_one(booking_dict)
    
    clean_booking = await db.astrology_bookings.find_one({"booking_id": booking.booking_id}, {"_id": 0})
    
    return {
        "booking": clean_booking,
        "razorpay_order_id": razorpay_order["id"],
        "razorpay_key_id": os.environ.get("RAZORPAY_KEY_ID")
    }

@api_router.post("/bookings/astrology/verify")
async def verify_astrology_payment(
    booking_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    request: Request
):
    user = await get_current_user(request, db)
    
    booking_doc = await db.astrology_bookings.find_one(
        {"booking_id": booking_id, "user_id": user["user_id"]},
        {"_id": 0}
    )
    
    if not booking_doc:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    try:
        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": booking_doc["razorpay_order_id"],
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })
    except:
        raise HTTPException(status_code=400, detail="Payment verification failed")
    
    await db.astrology_bookings.update_one(
        {"booking_id": booking_id},
        {"$set": {
            "payment_status": "completed",
            "razorpay_payment_id": razorpay_payment_id,
            "status": "confirmed"
        }}
    )
    
    if user.get("first_time_free_used") == False:
        await db.users.update_one(
            {"user_id": user["user_id"]},
            {"$set": {"first_time_free_used": True}}
        )
    
    loyalty_points = int(booking_doc["amount"] / 100)
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$inc": {"loyalty_points": loyalty_points}}
    )
    
    if user.get("referred_by"):
        referral_doc = await db.referrals.find_one(
            {"referral_code": user["referred_by"], "referred_user_id": user["user_id"]},
            {"_id": 0}
        )
        if referral_doc and referral_doc["status"] == "pending":
            await db.referrals.update_one(
                {"referral_code": user["referred_by"], "referred_user_id": user["user_id"]},
                {"$set": {"status": "completed"}}
            )
            await db.users.update_one(
                {"user_id": referral_doc["referrer_user_id"]},
                {"$inc": {"wallet_balance": 300}}
            )
    
    return {"message": "Payment verified", "booking_id": booking_id}

@api_router.post("/bookings/vastu")
async def create_vastu_booking(data: VastuBookingRequest, request: Request):
    user = await get_current_user(request, db)
    
    if data.service_type not in VASTU_PACKAGES:
        raise HTTPException(status_code=400, detail="Invalid service type")
    
    package = VASTU_PACKAGES[data.service_type]
    amount = package["price"]
    
    booking = VastuBooking(
        user_id=user["user_id"],
        service_type=data.service_type,
        amount=amount,
        booking_date=data.booking_date,
        booking_time=data.booking_time,
        info_box=data.info_box
    )
    
    razorpay_order = razorpay_client.order.create({
        "amount": int(amount * 100),
        "currency": "INR",
        "receipt": booking.booking_id
    })
    
    booking.razorpay_order_id = razorpay_order["id"]
    
    booking_dict = booking.model_dump()
    booking_dict["created_at"] = booking_dict["created_at"].isoformat()
    await db.vastu_bookings.insert_one(booking_dict)
    
    clean_booking = await db.vastu_bookings.find_one({"booking_id": booking.booking_id}, {"_id": 0})
    
    return {
        "booking": clean_booking,
        "razorpay_order_id": razorpay_order["id"],
        "razorpay_key_id": os.environ.get("RAZORPAY_KEY_ID")
    }

@api_router.post("/bookings/vastu/verify")
async def verify_vastu_payment(
    booking_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    request: Request
):
    user = await get_current_user(request, db)
    
    booking_doc = await db.vastu_bookings.find_one(
        {"booking_id": booking_id, "user_id": user["user_id"]},
        {"_id": 0}
    )
    
    if not booking_doc:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    try:
        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": booking_doc["razorpay_order_id"],
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })
    except:
        raise HTTPException(status_code=400, detail="Payment verification failed")
    
    await db.vastu_bookings.update_one(
        {"booking_id": booking_id},
        {"$set": {
            "payment_status": "completed",
            "razorpay_payment_id": razorpay_payment_id,
            "status": "confirmed"
        }}
    )
    
    loyalty_points = int(booking_doc["amount"] / 100)
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$inc": {"loyalty_points": loyalty_points}}
    )
    
    return {"message": "Payment verified", "booking_id": booking_id}

@api_router.post("/enquiries/site-visit")
async def create_site_visit_enquiry(data: SiteVisitRequest, request: Request):
    user = await get_current_user(request, db)
    
    enquiry = SiteVisitEnquiry(
        user_id=user["user_id"],
        name=data.name,
        phone=data.phone,
        city=data.city,
        state=data.state,
        property_type=data.property_type,
        property_size=data.property_size,
        preferred_visit_date=data.preferred_visit_date,
        issues=data.issues
    )
    
    enquiry_dict = enquiry.model_dump()
    enquiry_dict["created_at"] = enquiry_dict["created_at"].isoformat()
    await db.site_visit_enquiries.insert_one(enquiry_dict)
    
    return {"message": "Enquiry submitted", "enquiry_id": enquiry.enquiry_id}

@api_router.get("/user/bookings")
async def get_user_bookings(request: Request):
    user = await get_current_user(request, db)
    
    astrology_bookings = await db.astrology_bookings.find(
        {"user_id": user["user_id"]},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    vastu_bookings = await db.vastu_bookings.find(
        {"user_id": user["user_id"]},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    return {
        "astrology_bookings": astrology_bookings,
        "vastu_bookings": vastu_bookings
    }

@api_router.get("/user/reports")
async def get_user_reports(request: Request):
    user = await get_current_user(request, db)
    
    reports = await db.session_reports.find(
        {"user_id": user["user_id"]},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    return reports

@api_router.get("/user/invoices")
async def get_user_invoices(request: Request):
    user = await get_current_user(request, db)
    
    invoices = await db.invoices.find(
        {"user_id": user["user_id"]},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    return invoices

@api_router.post("/wallet/recharge")
async def recharge_wallet(data: WalletRechargeRequest, request: Request):
    user = await get_current_user(request, db)
    
    razorpay_order = razorpay_client.order.create({
        "amount": int(data.amount * 100),
        "currency": "INR",
        "receipt": f"WALLET_{user['user_id']}"
    })
    
    return {
        "razorpay_order_id": razorpay_order["id"],
        "razorpay_key_id": os.environ.get("RAZORPAY_KEY_ID"),
        "amount": data.amount
    }

@api_router.post("/wallet/recharge/verify")
async def verify_wallet_recharge(
    razorpay_payment_id: str,
    razorpay_signature: str,
    razorpay_order_id: str,
    amount: float,
    request: Request
):
    user = await get_current_user(request, db)
    
    try:
        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })
    except:
        raise HTTPException(status_code=400, detail="Payment verification failed")
    
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$inc": {"wallet_balance": amount}}
    )
    
    transaction = WalletTransaction(
        user_id=user["user_id"],
        amount=amount,
        type="credit",
        description="Wallet recharge"
    )
    transaction_dict = transaction.model_dump()
    transaction_dict["created_at"] = transaction_dict["created_at"].isoformat()
    await db.wallet_transactions.insert_one(transaction_dict)
    
    return {"message": "Wallet recharged successfully", "new_balance": user["wallet_balance"] + amount}

@api_router.post("/referral/redeem")
async def redeem_referral(data: ReferralRedeemRequest, request: Request):
    user = await get_current_user(request, db)
    
    if user.get("referred_by"):
        raise HTTPException(status_code=400, detail="Referral code already redeemed")
    
    referrer = await db.users.find_one({"referral_code": data.referral_code}, {"_id": 0})
    if not referrer:
        raise HTTPException(status_code=404, detail="Invalid referral code")
    
    if referrer["user_id"] == user["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot use your own referral code")
    
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"referred_by": data.referral_code}}
    )
    
    referral = Referral(
        referrer_user_id=referrer["user_id"],
        referred_user_id=user["user_id"],
        referral_code=data.referral_code
    )
    referral_dict = referral.model_dump()
    referral_dict["created_at"] = referral_dict["created_at"].isoformat()
    await db.referrals.insert_one(referral_dict)
    
    return {"message": "Referral code applied. You will get ₹200 off on your first booking!"}

@api_router.get("/blog")
async def get_blog_posts():
    posts = await db.blog_posts.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return posts

@api_router.get("/blog/{post_id}")
async def get_blog_post(post_id: str):
    post = await db.blog_posts.find_one({"post_id": post_id}, {"_id": 0})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post

@api_router.get("/horoscope/daily")
async def get_daily_horoscope(sign: Optional[str] = None):
    horoscopes = {
        "aries": {"en": "Great day for new beginnings", "hi": "नई शुरुआत के लिए शानदार दिन"},
        "taurus": {"en": "Financial gains expected", "hi": "वित्तीय लाभ की उम्मीद"},
        "gemini": {"en": "Communication brings success", "hi": "संचार सफलता लाता है"},
        "cancer": {"en": "Family matters need attention", "hi": "पारिवारिक मामलों पर ध्यान देने की जरूरत"},
        "leo": {"en": "Leadership opportunities arise", "hi": "नेतृत्व के अवसर उत्पन्न होते हैं"},
        "virgo": {"en": "Perfect day for planning", "hi": "योजना बनाने के लिए सही दिन"},
        "libra": {"en": "Balance and harmony prevail", "hi": "संतुलन और सद्भाव प्रबल"},
        "scorpio": {"en": "Transformation is coming", "hi": "परिवर्तन आ रहा है"},
        "sagittarius": {"en": "Adventure awaits", "hi": "रोमांच का इंतजार है"},
        "capricorn": {"en": "Hard work pays off", "hi": "कड़ी मेहनत रंग लाती है"},
        "aquarius": {"en": "Innovation leads the way", "hi": "नवाचार मार्ग प्रशस्त करता है"},
        "pisces": {"en": "Intuition guides you", "hi": "अंतर्ज्ञान आपका मार्गदर्शन करता है"}
    }
    
    if sign:
        return {sign: horoscopes.get(sign.lower(), horoscopes["aries"])}
    return horoscopes

@api_router.get("/panchang/today")
async def get_today_panchang():
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "tithi": "Shukla Paksha Chaturthi",
        "nakshatra": "Rohini",
        "muhurat": "06:00 AM - 07:30 AM",
        "rahukaal": "03:00 PM - 04:30 PM"
    }

@api_router.post("/admin/session/timer")
async def session_timer(data: SessionTimerRequest, request: Request):
    user = await get_current_user(request, db)
    
    booking = await db.astrology_bookings.find_one(
        {"booking_id": data.booking_id},
        {"_id": 0}
    )
    
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    if data.action == "start":
        await db.astrology_bookings.update_one(
            {"booking_id": data.booking_id},
            {"$set": {"session_start_time": datetime.now(timezone.utc).isoformat()}}
        )
        # Return client info so admin UI can display name/phone/package while timer runs
        client = await db.users.find_one({"user_id": booking["user_id"]}, {"_id": 0, "name": 1, "email": 1, "phone": 1, "first_time_free_used": 1})
        return {
            "message": "Timer started",
            "client_name": (client or {}).get("name"),
            "client_phone": booking.get("phone") or (client or {}).get("phone"),
            "client_email": (client or {}).get("email"),
            "package_name": booking.get("package_name") or booking.get("package_type"),
            "first_time_free_used": (client or {}).get("first_time_free_used", False),
            "rate_per_minute": 60,
            "free_minutes": 0 if (client or {}).get("first_time_free_used", False) else 10
        }
    
    elif data.action == "stop":
        if not booking.get("session_start_time"):
            raise HTTPException(status_code=400, detail="Timer not started")
        
        start_time = datetime.fromisoformat(booking["session_start_time"])
        end_time = datetime.now(timezone.utc)
        total_seconds = (end_time - start_time).total_seconds()
        # Round UP to the nearest minute (a 5s overrun counts as 1 extra minute)
        duration_minutes = max(1, int((total_seconds + 59) // 60))
        
        # Apply first-10-minutes-free for new clients (one-time only)
        client = await db.users.find_one({"user_id": booking["user_id"]}, {"_id": 0})
        rate_per_minute = 60
        free_minutes = 0
        discount_amount = 0
        if client and not client.get("first_time_free_used", False):
            free_minutes = min(10, duration_minutes)
            discount_amount = free_minutes * rate_per_minute
            # Mark the free minutes as used (one-time benefit)
            await db.users.update_one({"user_id": booking["user_id"]}, {"$set": {"first_time_free_used": True}})
        
        billable_minutes = duration_minutes - free_minutes
        gross_amount = duration_minutes * rate_per_minute
        final_amount = billable_minutes * rate_per_minute
        
        update_fields = {
            "session_end_time": end_time.isoformat(),
            "actual_duration_minutes": duration_minutes,
            "billable_minutes": billable_minutes,
            "free_minutes_applied": free_minutes,
            "final_amount": final_amount
        }
        if data.notes:
            update_fields["session_notes"] = data.notes
        
        await db.astrology_bookings.update_one(
            {"booking_id": data.booking_id},
            {"$set": update_fields}
        )
        
        invoice_id = f"INV{uuid.uuid4().hex[:8].upper()}"
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp_path = tmp.name
        
        doc = SimpleDocTemplate(tmp_path, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        
        story.append(Paragraph("ASTROLOGY TIMES", styles['Title']))
        story.append(Spacer(1, 0.2*inch))
        story.append(Paragraph(f"Invoice ID: {invoice_id}", styles['Normal']))
        story.append(Paragraph(f"Booking ID: {data.booking_id}", styles['Normal']))
        story.append(Paragraph(f"Client: {(client or {}).get('name','')} ({booking.get('phone') or (client or {}).get('phone','')})", styles['Normal']))
        story.append(Paragraph(f"Duration: {duration_minutes} minutes", styles['Normal']))
        if free_minutes:
            story.append(Paragraph(f"First-time Free: {free_minutes} minutes (-₹{discount_amount})", styles['Normal']))
        story.append(Paragraph(f"Billable Minutes: {billable_minutes} × ₹{rate_per_minute}/min", styles['Normal']))
        story.append(Paragraph(f"<b>Total Payable: ₹{final_amount}</b>", styles['Heading2']))
        if data.notes:
            story.append(Spacer(1, 0.2*inch))
            story.append(Paragraph("<b>Session Notes:</b>", styles['Heading3']))
            story.append(Paragraph(data.notes.replace("\n", "<br/>"), styles['Normal']))
        
        doc.build(story)
        
        # Upload PDF to object storage instead of local filesystem
        invoice_storage_path = f"{APP_NAME}/invoices/{invoice_id}.pdf"
        try:
            with open(tmp_path, 'rb') as f:
                put_object(invoice_storage_path, f.read(), "application/pdf")
            os.unlink(tmp_path)
        except Exception as e:
            logger.warning(f"Failed to upload invoice to storage: {e}")
        
        invoice = Invoice(
            user_id=booking["user_id"],
            booking_id=data.booking_id,
            booking_type="astrology",
            amount=final_amount,
            duration_minutes=duration_minutes,
            invoice_url=invoice_storage_path
        )
        invoice_dict = invoice.model_dump()
        invoice_dict["created_at"] = invoice_dict["created_at"].isoformat()
        await db.invoices.insert_one(invoice_dict)
        
        return {
            "message": "Session ended",
            "duration_minutes": duration_minutes,
            "billable_minutes": billable_minutes,
            "free_minutes_applied": free_minutes,
            "discount_amount": discount_amount,
            "gross_amount": gross_amount,
            "final_amount": final_amount,
            "rate_per_minute": rate_per_minute,
            "invoice_id": invoice_id,
            "notes_saved": bool(data.notes)
        }

@api_router.post("/admin/generate-report")
async def generate_ai_report(booking_id: str, booking_type: str, request: Request):
    user = await get_current_user(request, db)
    
    if booking_type == "astrology":
        booking = await db.astrology_bookings.find_one({"booking_id": booking_id}, {"_id": 0})
    else:
        booking = await db.vastu_bookings.find_one({"booking_id": booking_id}, {"_id": 0})
    
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    user_data = await db.users.find_one({"user_id": booking["user_id"]}, {"_id": 0})
    
    chat = LlmChat(
        api_key=os.environ.get("EMERGENT_LLM_KEY"),
        session_id=f"report_{booking_id}",
        system_message="You are an astrology and vastu expert creating session summary reports."
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    
    prompt = f"""Generate a professional session summary report for:
Client Name: {user_data.get('name')}
Session Date: {booking.get('booking_date')}
Service Type: {booking_type}
Duration: {booking.get('duration_minutes', 'N/A')} minutes

Include:
1. Brief overview of consultation
2. Key topics discussed
3. Remedies suggested
4. Follow-up recommendations

Keep it professional and concise."""
    
    message = UserMessage(text=prompt)
    report_content = await chat.send_message(message)
    
    report_id = f"RPT{uuid.uuid4().hex[:8].upper()}"
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp_path = tmp.name
    
    doc = SimpleDocTemplate(tmp_path, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    story.append(Paragraph("SESSION SUMMARY REPORT", styles['Title']))
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(f"Report ID: {report_id}", styles['Normal']))
    story.append(Paragraph(f"Client: {user_data.get('name')}", styles['Normal']))
    story.append(Paragraph(f"Date: {booking.get('booking_date')}", styles['Normal']))
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(report_content, styles['Normal']))
    
    doc.build(story)
    
    # Upload PDF to object storage
    report_storage_path = f"{APP_NAME}/reports/{report_id}.pdf"
    try:
        with open(tmp_path, 'rb') as f:
            put_object(report_storage_path, f.read(), "application/pdf")
        os.unlink(tmp_path)
    except Exception as e:
        logger.warning(f"Failed to upload report to storage: {e}")
    
    report = SessionReport(
        user_id=booking["user_id"],
        booking_id=booking_id,
        booking_type=booking_type,
        report_content=report_content,
        report_url=report_storage_path
    )
    report_dict = report.model_dump()
    report_dict["created_at"] = report_dict["created_at"].isoformat()
    await db.session_reports.insert_one(report_dict)
    
    return {"message": "Report generated", "report_id": report_id}

# ==================== BOOKING SLOTS ====================

@api_router.get("/slots/available")
async def get_available_slots(date: str):
    from datetime import datetime as dt
    try:
        d = dt.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    day_of_week = d.strftime("%A")
    if day_of_week == "Sunday":
        slots = list(SUNDAY_SLOTS)
    else:
        slots = list(WEEKDAY_SLOTS)
    
    custom_avail = await db.availability.find_one({"date": date}, {"_id": 0})
    if custom_avail:
        if not custom_avail.get("available", True):
            return {"date": date, "day": day_of_week, "slots": [], "fully_booked": True}
        if custom_avail.get("custom_slots"):
            slots = custom_avail["custom_slots"]
    
    astro_count = await db.astrology_bookings.count_documents({"booking_date": date, "status": {"$ne": "cancelled"}})
    vastu_count = await db.vastu_bookings.count_documents({"booking_date": date, "status": {"$ne": "cancelled"}})
    total_bookings = astro_count + vastu_count
    
    booked_times = []
    astro_booked = await db.astrology_bookings.find(
        {"booking_date": date, "status": {"$ne": "cancelled"}}, {"_id": 0, "booking_time": 1}
    ).to_list(100)
    vastu_booked = await db.vastu_bookings.find(
        {"booking_date": date, "status": {"$ne": "cancelled"}}, {"_id": 0, "booking_time": 1}
    ).to_list(100)
    for b in astro_booked + vastu_booked:
        booked_times.append(b["booking_time"])
    
    available_slots = [s for s in slots if s not in booked_times]
    fully_booked = total_bookings >= MAX_BOOKINGS_PER_DAY or len(available_slots) == 0
    
    return {
        "date": date,
        "day": day_of_week,
        "slots": available_slots if not fully_booked else [],
        "fully_booked": fully_booked,
        "total_bookings": total_bookings
    }

# ==================== USER PROFILE ====================

@api_router.put("/user/profile")
async def update_profile(data: ProfileUpdateRequest, request: Request):
    user = await get_current_user(request, db)
    update_data = {}
    for field, value in data.model_dump(exclude_none=True).items():
        update_data[field] = value
    if update_data:
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": update_data})
    updated = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0, "password_hash": 0})
    return updated

# ==================== FILE UPLOAD ====================

@api_router.post("/upload/floor-plan")
async def upload_floor_plan(file: UploadFile = File(...), description: str = "", request: Request = None):
    user = await get_current_user(request, db)
    ext = file.filename.split(".")[-1] if "." in file.filename else "bin"
    allowed = ["pdf", "jpg", "jpeg", "png", "webp"]
    if ext.lower() not in allowed:
        raise HTTPException(status_code=400, detail="Only PDF and image files allowed")
    
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    path = f"{APP_NAME}/floor-plans/{user['user_id']}/{uuid.uuid4()}.{ext}"
    result = put_object(path, data, file.content_type or "application/octet-stream")
    
    file_record = {
        "file_id": f"FILE{uuid.uuid4().hex[:8].upper()}",
        "user_id": user["user_id"],
        "storage_path": result["path"],
        "original_filename": file.filename,
        "content_type": file.content_type,
        "size": result.get("size", len(data)),
        "description": description,
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.uploaded_files.insert_one(file_record)
    
    return {"file_id": file_record["file_id"], "filename": file.filename, "storage_path": result["path"]}

@api_router.get("/files/{path:path}")
async def download_file(path: str, request: Request, auth: str = Query(None)):
    record = await db.uploaded_files.find_one({"storage_path": path, "is_deleted": False}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    data, content_type = get_object(path)
    return Response(content=data, media_type=record.get("content_type", content_type))

# ==================== KUNDLI GENERATION ====================

@api_router.post("/kundli/generate")
async def generate_kundli(data: KundliRequest):
    # Use Claude Sonnet 4.5 — significantly faster than gpt-5.2 for long-form output.
    # Cloudflare edge proxy times out at ~60s, so we must stay under that window.
    chat = LlmChat(
        api_key=os.environ.get("EMERGENT_LLM_KEY"),
        session_id=f"kundli_{uuid.uuid4().hex[:8]}",
        system_message="You are an expert Vedic astrologer. Generate concise but insightful Kundli readings in both Hindi and English. Be accurate and professional."
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    
    prompt = f"""Generate a Vedic Kundli analysis for:
Name: {data.name} | DOB: {data.dob} | Time: {data.time_of_birth} | Place: {data.place_of_birth} | Gender: {data.gender}

Provide these sections concisely (3-4 lines each, both English and Hindi):
1. Lagna (Ascendant) / लग्न
2. Moon Sign (Rashi) / राशि
3. Nakshatra / नक्षत्र
4. Key Planetary Positions / ग्रह स्थिति (brief table)
5. Personality / व्यक्तित्व
6. Career / करियर
7. Marriage / विवाह
8. Health / स्वास्थ्य
9. Lucky Elements / शुभ तत्व (color, number, day, gemstone)
10. Current Dasha / वर्तमान दशा

Use ## for section headings. Keep total under 600 words. Be specific."""
    
    message = UserMessage(text=prompt)
    kundli_content = await chat.send_message(message)
    
    return {
        "name": data.name,
        "dob": data.dob,
        "time_of_birth": data.time_of_birth,
        "place_of_birth": data.place_of_birth,
        "kundli": kundli_content
    }

# ==================== ENHANCED HOROSCOPE ====================

@api_router.get("/horoscope/detailed/{sign}")
async def get_detailed_horoscope(sign: str):
    signs_data = {
        "aries": {"en_name": "Aries", "hi_name": "मेष", "element": "Fire", "ruler": "Mars", "symbol": "Ram", "dates": "Mar 21 - Apr 19"},
        "taurus": {"en_name": "Taurus", "hi_name": "वृषभ", "element": "Earth", "ruler": "Venus", "symbol": "Bull", "dates": "Apr 20 - May 20"},
        "gemini": {"en_name": "Gemini", "hi_name": "मिथुन", "element": "Air", "ruler": "Mercury", "symbol": "Twins", "dates": "May 21 - Jun 20"},
        "cancer": {"en_name": "Cancer", "hi_name": "कर्क", "element": "Water", "ruler": "Moon", "symbol": "Crab", "dates": "Jun 21 - Jul 22"},
        "leo": {"en_name": "Leo", "hi_name": "सिंह", "element": "Fire", "ruler": "Sun", "symbol": "Lion", "dates": "Jul 23 - Aug 22"},
        "virgo": {"en_name": "Virgo", "hi_name": "कन्या", "element": "Earth", "ruler": "Mercury", "symbol": "Virgin", "dates": "Aug 23 - Sep 22"},
        "libra": {"en_name": "Libra", "hi_name": "तुला", "element": "Air", "ruler": "Venus", "symbol": "Scales", "dates": "Sep 23 - Oct 22"},
        "scorpio": {"en_name": "Scorpio", "hi_name": "वृश्चिक", "element": "Water", "ruler": "Mars", "symbol": "Scorpion", "dates": "Oct 23 - Nov 21"},
        "sagittarius": {"en_name": "Sagittarius", "hi_name": "धनु", "element": "Fire", "ruler": "Jupiter", "symbol": "Archer", "dates": "Nov 22 - Dec 21"},
        "capricorn": {"en_name": "Capricorn", "hi_name": "मकर", "element": "Earth", "ruler": "Saturn", "symbol": "Goat", "dates": "Dec 22 - Jan 19"},
        "aquarius": {"en_name": "Aquarius", "hi_name": "कुंभ", "element": "Air", "ruler": "Saturn", "symbol": "Water Bearer", "dates": "Jan 20 - Feb 18"},
        "pisces": {"en_name": "Pisces", "hi_name": "मीन", "element": "Water", "ruler": "Jupiter", "symbol": "Fish", "dates": "Feb 19 - Mar 20"}
    }
    
    sign_lower = sign.lower()
    if sign_lower not in signs_data:
        raise HTTPException(status_code=404, detail="Invalid zodiac sign")
    
    sign_info = signs_data[sign_lower]
    
    horoscope_db = await db.daily_horoscopes.find_one(
        {"sign": sign_lower, "date": datetime.now().strftime("%Y-%m-%d")},
        {"_id": 0}
    )
    
    if horoscope_db:
        return {**sign_info, **horoscope_db}
    
    daily_predictions = {
        "aries": {"love_en": "Romantic energy is strong today", "love_hi": "आज प्रेम की ऊर्जा प्रबल है", "career_en": "New project brings recognition", "career_hi": "नई परियोजना पहचान दिलाती है", "health_en": "Take care of your headaches", "health_hi": "सिरदर्द का ध्यान रखें", "lucky_number": 9, "lucky_color": "Red"},
        "taurus": {"love_en": "Stability in relationships", "love_hi": "रिश्तों में स्थिरता", "career_en": "Financial gains expected", "career_hi": "वित्तीय लाभ की उम्मीद", "health_en": "Focus on throat care", "health_hi": "गले की देखभाल पर ध्यान दें", "lucky_number": 6, "lucky_color": "Green"},
        "gemini": {"love_en": "Express your feelings today", "love_hi": "आज अपनी भावनाएं व्यक्त करें", "career_en": "Communication brings success", "career_hi": "संचार सफलता लाता है", "health_en": "Mental relaxation needed", "health_hi": "मानसिक विश्राम की आवश्यकता", "lucky_number": 5, "lucky_color": "Yellow"},
        "cancer": {"love_en": "Family bonding time", "love_hi": "परिवार से जुड़ने का समय", "career_en": "Home-based work excels", "career_hi": "घर-आधारित कार्य उत्कृष्ट", "health_en": "Stomach care important", "health_hi": "पेट की देखभाल महत्वपूर्ण", "lucky_number": 2, "lucky_color": "White"},
        "leo": {"love_en": "Passionate connections", "love_hi": "भावुक संबंध", "career_en": "Leadership opportunities", "career_hi": "नेतृत्व के अवसर", "health_en": "Heart health focus", "health_hi": "हृदय स्वास्थ्य पर ध्यान", "lucky_number": 1, "lucky_color": "Gold"},
        "virgo": {"love_en": "Thoughtful gestures matter", "love_hi": "सोच-समझकर किए गए इशारे मायने रखते हैं", "career_en": "Details lead to success", "career_hi": "विवरण सफलता की ओर ले जाते हैं", "health_en": "Digestive health focus", "health_hi": "पाचन स्वास्थ्य पर ध्यान", "lucky_number": 5, "lucky_color": "Green"},
        "libra": {"love_en": "Balance in relationships", "love_hi": "रिश्तों में संतुलन", "career_en": "Partnerships flourish", "career_hi": "साझेदारियां फलती-फूलती हैं", "health_en": "Lower back care", "health_hi": "कमर का ध्यान रखें", "lucky_number": 6, "lucky_color": "Blue"},
        "scorpio": {"love_en": "Deep emotional connections", "love_hi": "गहरे भावनात्मक संबंध", "career_en": "Research brings results", "career_hi": "शोध परिणाम लाता है", "health_en": "Manage stress levels", "health_hi": "तनाव का प्रबंधन करें", "lucky_number": 8, "lucky_color": "Maroon"},
        "sagittarius": {"love_en": "Adventure with partner", "love_hi": "साथी के साथ रोमांच", "career_en": "Travel brings opportunities", "career_hi": "यात्रा अवसर लाती है", "health_en": "Leg care needed", "health_hi": "पैरों की देखभाल आवश्यक", "lucky_number": 3, "lucky_color": "Purple"},
        "capricorn": {"love_en": "Commitment strengthens", "love_hi": "प्रतिबद्धता मजबूत होती है", "career_en": "Hard work rewarded", "career_hi": "कड़ी मेहनत का फल", "health_en": "Joint care important", "health_hi": "जोड़ों की देखभाल महत्वपूर्ण", "lucky_number": 8, "lucky_color": "Brown"},
        "aquarius": {"love_en": "Unique love expressions", "love_hi": "अनूठी प्रेम अभिव्यक्ति", "career_en": "Innovation pays off", "career_hi": "नवाचार रंग लाता है", "health_en": "Ankle care needed", "health_hi": "टखने की देखभाल आवश्यक", "lucky_number": 4, "lucky_color": "Electric Blue"},
        "pisces": {"love_en": "Dreamy romantic day", "love_hi": "स्वप्निल रोमांटिक दिन", "career_en": "Creativity shines", "career_hi": "रचनात्मकता चमकती है", "health_en": "Feet care important", "health_hi": "पैरों की देखभाल महत्वपूर्ण", "lucky_number": 7, "lucky_color": "Sea Green"}
    }
    
    prediction = daily_predictions.get(sign_lower, daily_predictions["aries"])
    return {**sign_info, **prediction, "date": datetime.now().strftime("%Y-%m-%d")}

# ==================== ENHANCED PANCHANG ====================

@api_router.get("/panchang/detailed")
async def get_detailed_panchang():
    now = datetime.now()
    day_of_year = now.timetuple().tm_yday
    
    tithis = ["Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami", "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami", "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi", "Purnima/Amavasya"]
    nakshatras = ["Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra", "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishtha", "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada", "Revati"]
    yogas = ["Vishkambha", "Preeti", "Ayushman", "Saubhagya", "Shobhana", "Atiganda", "Sukarma", "Dhriti", "Shoola", "Ganda", "Vriddhi", "Dhruva", "Vyaghata", "Harshana", "Vajra", "Siddhi", "Vyatipata", "Variyan", "Parigha", "Shiva", "Siddha", "Sadhya", "Shubha", "Shukla", "Brahma", "Indra", "Vaidhriti"]
    karanas = ["Bava", "Balava", "Kaulava", "Taitila", "Garaja", "Vanija", "Vishti", "Shakuni", "Chatushpada", "Naga", "Kimstughna"]
    
    paksha = "Shukla" if (day_of_year % 30) < 15 else "Krishna"
    tithi_idx = day_of_year % 15
    nakshatra_idx = day_of_year % 27
    yoga_idx = day_of_year % 27
    karana_idx = day_of_year % 11
    
    sunrise_h = 6 + (day_of_year % 2)
    sunrise_m = 15 + (day_of_year % 30)
    sunset_h = 18 + (day_of_year % 2)
    sunset_m = 10 + (day_of_year % 30)
    
    rahu_start_h = [15, 9, 12, 13, 10, 7, 16][now.weekday()]
    
    return {
        "date": now.strftime("%Y-%m-%d"),
        "day_en": now.strftime("%A"),
        "day_hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"][now.weekday()],
        "paksha": paksha,
        "tithi": f"{paksha} {tithis[tithi_idx]}",
        "tithi_hi": f"{paksha} {'प्रतिपदा' if tithi_idx == 0 else tithis[tithi_idx]}",
        "nakshatra": nakshatras[nakshatra_idx],
        "yoga": yogas[yoga_idx],
        "karana": karanas[karana_idx],
        "sunrise": f"{sunrise_h:02d}:{sunrise_m:02d} AM",
        "sunset": f"{sunset_h:02d}:{sunset_m:02d} PM",
        "moonrise": f"{8 + (day_of_year % 4):02d}:{20 + (day_of_year % 20):02d} PM",
        "rahukaal": f"{rahu_start_h:02d}:00 - {rahu_start_h+1:02d}:30",
        "abhijit_muhurat": "11:45 AM - 12:30 PM",
        "shubh_muhurat": ["06:30 AM - 07:30 AM", "11:45 AM - 12:30 PM", "03:30 PM - 04:15 PM"],
        "ashubh_muhurat": [f"{rahu_start_h:02d}:00 - {rahu_start_h+1:02d}:30"]
    }

# ==================== ADMIN ENDPOINTS ====================

@api_router.post("/admin/login")
async def admin_login(data: AdminLoginRequest):
    user_doc = await db.users.find_one({"email": data.email}, {"_id": 0})
    if not user_doc or not user_doc.get("password_hash") or not user_doc.get("is_admin"):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    if not verify_password(data.password, user_doc["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    token = create_jwt_token(user_doc["user_id"], user_doc["email"])
    session = UserSession(
        user_id=user_doc["user_id"],
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7)
    )
    sd = session.model_dump()
    sd["created_at"] = sd["created_at"].isoformat()
    sd["expires_at"] = sd["expires_at"].isoformat()
    await db.user_sessions.insert_one(sd)
    user_data = await db.users.find_one({"user_id": user_doc["user_id"]}, {"_id": 0, "password_hash": 0})
    return {"user": user_data, "token": token}

@api_router.get("/admin/bookings")
async def admin_get_all_bookings(request: Request):
    await check_admin(request)
    astrology = await db.astrology_bookings.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    vastu = await db.vastu_bookings.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    enquiries = await db.site_visit_enquiries.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    
    for bookings_list in [astrology, vastu]:
        for b in bookings_list:
            user_doc = await db.users.find_one({"user_id": b["user_id"]}, {"_id": 0, "password_hash": 0})
            b["client_name"] = user_doc.get("name", "Unknown") if user_doc else "Unknown"
            b["client_email"] = user_doc.get("email", "") if user_doc else ""
            b["client_phone"] = user_doc.get("phone", "") if user_doc else ""
    
    for e in enquiries:
        user_doc = await db.users.find_one({"user_id": e["user_id"]}, {"_id": 0, "password_hash": 0})
        e["client_email"] = user_doc.get("email", "") if user_doc else ""
    
    return {"astrology": astrology, "vastu": vastu, "enquiries": enquiries}

@api_router.post("/admin/booking/action")
async def admin_booking_action(data: AdminBookingActionRequest, request: Request):
    await check_admin(request)
    collection = "astrology_bookings" if data.booking_type == "astrology" else "vastu_bookings"
    update_data = {"status": data.action}
    if data.meet_link:
        update_data["meet_link"] = data.meet_link
    result = await db[collection].update_one(
        {"booking_id": data.booking_id},
        {"$set": update_data}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"message": f"Booking {data.action}ed successfully"}

@api_router.post("/admin/blog/create")
async def admin_create_blog(data: BlogCreateRequest, request: Request):
    await check_admin(request)
    post = BlogPost(
        title_en=data.title_en,
        title_hi=data.title_hi,
        content_en=data.content_en,
        content_hi=data.content_hi,
        category=data.category,
        image_url=data.image_url
    )
    post_dict = post.model_dump()
    post_dict["created_at"] = post_dict["created_at"].isoformat()
    await db.blog_posts.insert_one(post_dict)
    return {"message": "Blog post created", "post_id": post.post_id}

@api_router.delete("/admin/blog/{post_id}")
async def admin_delete_blog(post_id: str, request: Request):
    await check_admin(request)
    result = await db.blog_posts.delete_one({"post_id": post_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"message": "Blog post deleted"}

@api_router.post("/admin/custom-invoice")
async def admin_create_custom_invoice(data: AdminCustomInvoiceRequest, request: Request):
    await check_admin(request)
    enquiry = await db.site_visit_enquiries.find_one({"enquiry_id": data.enquiry_id}, {"_id": 0})
    if not enquiry:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    
    total = data.travel_cost + data.stay_cost + data.consultation_fee
    
    razorpay_link = razorpay_client.payment_link.create({
        "amount": int(total * 100),
        "currency": "INR",
        "description": f"Site Visit - {enquiry.get('property_type', 'Property')} at {enquiry.get('city', 'Location')}",
        "customer": {
            "name": enquiry.get("name", ""),
            "contact": enquiry.get("phone", "")
        },
        "notify": {"sms": True},
        "callback_url": "",
        "callback_method": ""
    })
    
    await db.site_visit_enquiries.update_one(
        {"enquiry_id": data.enquiry_id},
        {"$set": {
            "custom_amount": total,
            "travel_cost": data.travel_cost,
            "stay_cost": data.stay_cost,
            "consultation_fee": data.consultation_fee,
            "payment_link": razorpay_link.get("short_url", ""),
            "status": "invoice_sent"
        }}
    )
    
    return {
        "message": "Custom invoice created",
        "total_amount": total,
        "payment_link": razorpay_link.get("short_url", ""),
        "enquiry_id": data.enquiry_id
    }

@api_router.post("/admin/availability")
async def admin_set_availability(data: AdminAvailabilityRequest, request: Request):
    await check_admin(request)
    await db.availability.update_one(
        {"date": data.date},
        {"$set": {
            "date": data.date,
            "available": data.available,
            "custom_slots": data.custom_slots,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }},
        upsert=True
    )
    return {"message": f"Availability updated for {data.date}"}

@api_router.get("/admin/revenue")
async def admin_revenue(request: Request):
    await check_admin(request)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()
    
    all_astro = await db.astrology_bookings.find({"payment_status": "completed"}, {"_id": 0, "amount": 1, "created_at": 1}).to_list(1000)
    all_vastu = await db.vastu_bookings.find({"payment_status": "completed"}, {"_id": 0, "amount": 1, "created_at": 1}).to_list(1000)
    
    daily_total = sum(b.get("amount", 0) for b in all_astro + all_vastu if str(b.get("created_at", "")).startswith(today))
    weekly_total = sum(b.get("amount", 0) for b in all_astro + all_vastu if str(b.get("created_at", "")) >= week_ago)
    monthly_total = sum(b.get("amount", 0) for b in all_astro + all_vastu if str(b.get("created_at", "")) >= month_ago)
    total_revenue = sum(b.get("amount", 0) for b in all_astro + all_vastu)
    
    total_users = await db.users.count_documents({"is_admin": {"$ne": True}})
    total_bookings = len(all_astro) + len(all_vastu)
    pending_astro = await db.astrology_bookings.count_documents({"status": "pending"})
    pending_vastu = await db.vastu_bookings.count_documents({"status": "pending"})
    pending_enquiries = await db.site_visit_enquiries.count_documents({"status": "pending"})
    
    return {
        "daily_revenue": daily_total,
        "weekly_revenue": weekly_total,
        "monthly_revenue": monthly_total,
        "total_revenue": total_revenue,
        "total_users": total_users,
        "total_bookings": total_bookings,
        "pending_bookings": pending_astro + pending_vastu,
        "pending_enquiries": pending_enquiries
    }

@api_router.get("/admin/users")
async def admin_get_users(request: Request):
    await check_admin(request)
    users = await db.users.find({"is_admin": {"$ne": True}}, {"_id": 0, "password_hash": 0}).to_list(500)
    return users

# ==================== SITE VISIT WITH FILE UPLOAD ====================

@api_router.post("/enquiries/site-visit-with-file")
async def create_site_visit_with_file(
    name: str = Query(...),
    phone: str = Query(...),
    city: str = Query(...),
    state: str = Query(...),
    property_type: str = Query(...),
    property_size: str = Query(...),
    preferred_visit_date: str = Query(...),
    issues: str = Query(""),
    description: str = Query(""),
    file: UploadFile = File(None),
    request: Request = None
):
    user = await get_current_user(request, db)
    
    file_id = None
    if file and file.filename:
        ext = file.filename.split(".")[-1] if "." in file.filename else "bin"
        data = await file.read()
        path = f"{APP_NAME}/floor-plans/{user['user_id']}/{uuid.uuid4()}.{ext}"
        result = put_object(path, data, file.content_type or "application/octet-stream")
        file_record = {
            "file_id": f"FILE{uuid.uuid4().hex[:8].upper()}",
            "user_id": user["user_id"],
            "storage_path": result["path"],
            "original_filename": file.filename,
            "content_type": file.content_type,
            "size": result.get("size", len(data)),
            "description": description,
            "is_deleted": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.uploaded_files.insert_one(file_record)
        file_id = file_record["file_id"]
    
    enquiry = SiteVisitEnquiry(
        user_id=user["user_id"],
        name=name,
        phone=phone,
        city=city,
        state=state,
        property_type=property_type,
        property_size=property_size,
        preferred_visit_date=preferred_visit_date,
        issues=issues
    )
    enquiry_dict = enquiry.model_dump()
    enquiry_dict["created_at"] = enquiry_dict["created_at"].isoformat()
    enquiry_dict["floor_plan_file_id"] = file_id
    enquiry_dict["description"] = description
    await db.site_visit_enquiries.insert_one(enquiry_dict)
    
    return {"message": "Site visit enquiry submitted", "enquiry_id": enquiry.enquiry_id}

# ==================== BOOKING CANCELLATION ====================

class CancelBookingRequest(BaseModel):
    booking_id: str
    booking_type: str

@api_router.post("/bookings/cancel")
async def cancel_booking(data: CancelBookingRequest, request: Request):
    user = await get_current_user(request, db)
    collection = "astrology_bookings" if data.booking_type == "astrology" else "vastu_bookings"
    booking = await db[collection].find_one(
        {"booking_id": data.booking_id, "user_id": user["user_id"]},
        {"_id": 0}
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking["status"] not in ["pending", "confirmed"]:
        raise HTTPException(status_code=400, detail="Cannot cancel this booking")
    
    await db[collection].update_one(
        {"booking_id": data.booking_id},
        {"$set": {"status": "cancelled"}}
    )
    return {"message": "Booking cancelled successfully"}

# ==================== WALLET PAYMENT ====================

class WalletPayRequest(BaseModel):
    booking_id: str
    booking_type: str

@api_router.post("/bookings/pay-with-wallet")
async def pay_with_wallet(data: WalletPayRequest, request: Request):
    user = await get_current_user(request, db)
    collection = "astrology_bookings" if data.booking_type == "astrology" else "vastu_bookings"
    booking = await db[collection].find_one(
        {"booking_id": data.booking_id, "user_id": user["user_id"]},
        {"_id": 0}
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking["payment_status"] == "completed":
        raise HTTPException(status_code=400, detail="Already paid")
    
    amount = booking["amount"]
    if user.get("wallet_balance", 0) < amount:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance")
    
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$inc": {"wallet_balance": -amount}}
    )
    await db[collection].update_one(
        {"booking_id": data.booking_id},
        {"$set": {"payment_status": "completed", "status": "confirmed"}}
    )
    
    transaction = WalletTransaction(
        user_id=user["user_id"],
        amount=-amount,
        type="debit",
        description=f"Payment for booking {data.booking_id}"
    )
    td = transaction.model_dump()
    td["created_at"] = td["created_at"].isoformat()
    await db.wallet_transactions.insert_one(td)
    
    if not user.get("first_time_free_used"):
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"first_time_free_used": True}})
    
    loyalty_points = int(amount / 100)
    await db.users.update_one({"user_id": user["user_id"]}, {"$inc": {"loyalty_points": loyalty_points}})
    
    return {"message": "Paid with wallet", "booking_id": data.booking_id}

# ==================== LOYALTY POINTS REDEMPTION ====================

class RedeemPointsRequest(BaseModel):
    booking_id: str
    booking_type: str
    points_to_redeem: int

@api_router.post("/bookings/redeem-points")
async def redeem_loyalty_points(data: RedeemPointsRequest, request: Request):
    user = await get_current_user(request, db)
    if user.get("loyalty_points", 0) < data.points_to_redeem:
        raise HTTPException(status_code=400, detail="Insufficient loyalty points")
    if data.points_to_redeem < 100:
        raise HTTPException(status_code=400, detail="Minimum 100 points to redeem")
    
    discount = (data.points_to_redeem // 100) * 100
    points_used = (data.points_to_redeem // 100) * 100
    
    collection = "astrology_bookings" if data.booking_type == "astrology" else "vastu_bookings"
    booking = await db[collection].find_one({"booking_id": data.booking_id, "user_id": user["user_id"]}, {"_id": 0})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    new_amount = max(0, booking["amount"] - discount)
    await db[collection].update_one({"booking_id": data.booking_id}, {"$set": {"amount": new_amount, "loyalty_discount": discount}})
    await db.users.update_one({"user_id": user["user_id"]}, {"$inc": {"loyalty_points": -points_used}})
    
    return {"message": f"Redeemed {points_used} points for ₹{discount} discount", "new_amount": new_amount}

# ==================== KUNDLI SAVING ====================

@api_router.post("/kundli/save")
async def save_kundli(request: Request):
    user = await get_current_user(request, db)
    body = await request.json()
    kundli_doc = {
        "kundli_id": f"KDL{uuid.uuid4().hex[:8].upper()}",
        "user_id": user["user_id"],
        "name": body.get("name"),
        "dob": body.get("dob"),
        "time_of_birth": body.get("time_of_birth"),
        "place_of_birth": body.get("place_of_birth"),
        "gender": body.get("gender"),
        "kundli_content": body.get("kundli"),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.saved_kundlis.insert_one(kundli_doc)
    return {"message": "Kundli saved", "kundli_id": kundli_doc["kundli_id"]}

@api_router.get("/user/kundlis")
async def get_user_kundlis(request: Request):
    user = await get_current_user(request, db)
    kundlis = await db.saved_kundlis.find(
        {"user_id": user["user_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(50)
    return kundlis

# ==================== AI DAILY HOROSCOPE (CACHED) ====================

@api_router.post("/admin/horoscope/generate")
async def admin_generate_daily_horoscope(request: Request):
    await check_admin(request)
    today = datetime.now().strftime("%Y-%m-%d")
    
    existing = await db.daily_horoscopes.find_one({"date": today}, {"_id": 0})
    if existing:
        return {"message": "Horoscope already exists for today", "date": today}
    
    signs = ["aries", "taurus", "gemini", "cancer", "leo", "virgo", "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"]
    
    chat = LlmChat(
        api_key=os.environ.get("EMERGENT_LLM_KEY"),
        session_id=f"horoscope_{today}",
        system_message="You are an expert Vedic astrologer. Generate daily horoscope predictions in both Hindi and English. Be specific and meaningful."
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    
    prompt = f"""Generate daily horoscope for {today} for all 12 zodiac signs. 
For each sign provide JSON format:
{{
  "sign": "aries",
  "general_en": "...", "general_hi": "...",
  "love_en": "...", "love_hi": "...",
  "career_en": "...", "career_hi": "...",
  "health_en": "...", "health_hi": "...",
  "upay_en": "...", "upay_hi": "...",
  "lucky_number": 9, "lucky_color_en": "Red", "lucky_color_hi": "लाल",
  "lucky_stone_en": "Coral", "lucky_stone_hi": "मूंगा"
}}
Return a JSON array of 12 objects. Only return the JSON, no other text."""
    
    message = UserMessage(text=prompt)
    response_text = await chat.send_message(message)
    
    try:
        import json
        clean = response_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
        horoscopes = json.loads(clean)
    except:
        horoscopes = []
        for sign in signs:
            horoscopes.append({"sign": sign, "general_en": "Check back later for today's prediction.", "general_hi": "आज की भविष्यवाणी के लिए बाद में देखें।"})
    
    for h in horoscopes:
        h["date"] = today
        await db.daily_horoscopes.update_one(
            {"date": today, "sign": h.get("sign", "")},
            {"$set": h},
            upsert=True
        )
    
    return {"message": f"Generated horoscope for {today}", "count": len(horoscopes)}

@api_router.get("/horoscope/ai/{sign}")
async def get_ai_horoscope(sign: str):
    today = datetime.now().strftime("%Y-%m-%d")
    horoscope = await db.daily_horoscopes.find_one(
        {"date": today, "sign": sign.lower()},
        {"_id": 0}
    )
    if horoscope:
        return horoscope
    return {"sign": sign, "date": today, "general_en": "Horoscope not generated yet for today. Check back soon!", "general_hi": "आज का राशिफल अभी तैयार नहीं है। जल्द ही देखें!"}

# ==================== BLOG SEED + IMAGE UPLOAD ====================

@api_router.post("/admin/blog/upload-image")
async def admin_upload_blog_image(file: UploadFile = File(...), request: Request = None):
    await check_admin(request)
    ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    data = await file.read()
    path = f"{APP_NAME}/blog-images/{uuid.uuid4()}.{ext}"
    result = put_object(path, data, file.content_type or "image/jpeg")
    return {"image_url": result["path"], "storage_path": result["path"]}

async def seed_blog_posts():
    count = await db.blog_posts.count_documents({})
    if count > 0:
        return
    
    sample_posts = [
        {
            "post_id": "POST_SAMPLE_01",
            "title_en": "How Mercury Retrograde Affects Your Life",
            "title_hi": "बुध वक्री आपके जीवन को कैसे प्रभावित करता है",
            "content_en": "Mercury retrograde is one of the most talked-about astrological events. During this period, the planet Mercury appears to move backward in its orbit. This phenomenon occurs 3-4 times a year and each retrograde lasts about 3 weeks.\n\n## Effects on Communication\nMercury rules communication, technology, and travel. During retrograde, you may experience misunderstandings, email glitches, travel delays, and contract issues.\n\n## What to Do\n- Double-check all important documents\n- Back up your digital data\n- Avoid signing major contracts\n- Be patient with delays\n- Reconnect with old friends\n\n## Remedies\n- Chant 'Om Budhaya Namaha' 108 times\n- Wear green on Wednesdays\n- Donate green vegetables to the needy",
            "content_hi": "बुध वक्री ज्योतिष की सबसे चर्चित घटनाओं में से एक है। इस अवधि के दौरान, बुध ग्रह अपनी कक्षा में पीछे की ओर चलता दिखाई देता है।\n\n## संचार पर प्रभाव\nबुध संचार, तकनीक और यात्रा का कारक है। वक्री के दौरान गलतफहमी, तकनीकी समस्याएं और यात्रा में देरी हो सकती है।\n\n## उपाय\n- 'ॐ बुधाय नमः' का 108 बार जाप करें\n- बुधवार को हरे वस्त्र पहनें\n- हरी सब्जियां दान करें",
            "category": "astrology",
            "author": "Surendra Kumar Patawari",
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "post_id": "POST_SAMPLE_02",
            "title_en": "Vastu Tips for Home Entrance - Attract Prosperity",
            "title_hi": "घर के प्रवेश द्वार के वास्तु टिप्स - समृद्धि आकर्षित करें",
            "content_en": "The main entrance of your home is called the 'Mouth of Chi' in Vastu Shastra. It is where positive energy enters your living space.\n\n## Direction Matters\n- North or East-facing entrances are most auspicious\n- Avoid South-West facing main doors\n\n## Tips for a Vastu-Compliant Entrance\n1. Keep the entrance clean and clutter-free\n2. Place a bright light near the door\n3. Use a wooden door, avoid metal\n4. Place a Toran or auspicious symbols\n5. Avoid placing a mirror facing the main door\n6. Keep shoes outside or in a closed rack",
            "content_hi": "वास्तु शास्त्र में घर का मुख्य प्रवेश द्वार बहुत महत्वपूर्ण है। यहीं से सकारात्मक ऊर्जा आती है।\n\n## दिशा का महत्व\n- उत्तर या पूर्व मुखी प्रवेश सबसे शुभ\n- दक्षिण-पश्चिम मुखी दरवाजे से बचें\n\n## वास्तु टिप्स\n1. प्रवेश द्वार साफ रखें\n2. दरवाजे के पास तेज रोशनी रखें\n3. लकड़ी का दरवाजा उपयोग करें\n4. तोरण या शुभ चिह्न लगाएं\n5. मुख्य दरवाजे के सामने दर्पण न रखें",
            "category": "vastu",
            "author": "Surendra Kumar Patawari",
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "post_id": "POST_SAMPLE_03",
            "title_en": "Why Accurate Birth Time is Crucial for Kundli",
            "title_hi": "कुंडली के लिए सही जन्म समय क्यों जरूरी है",
            "content_en": "In Vedic astrology, even a difference of a few minutes in birth time can change your entire Kundli. The Ascendant (Lagna) changes approximately every 2 hours, and the Moon moves through a Nakshatra in about 1 day.\n\n## What Changes with Wrong Time\n- Wrong Lagna means wrong house predictions\n- Dasha calculations become inaccurate\n- Gemstone recommendations may be harmful\n\n## How to Find Your Exact Birth Time\n- Check birth certificate\n- Ask parents or relatives\n- Hospital records\n- Birth time rectification by an experienced astrologer",
            "content_hi": "वैदिक ज्योतिष में जन्म समय में कुछ मिनटों का अंतर भी पूरी कुंडली बदल सकता है।\n\n## गलत समय से क्या बदलता है\n- गलत लग्न = गलत भविष्यवाणी\n- दशा गणना गलत हो जाती है\n- रत्न सिफारिशें हानिकारक हो सकती हैं\n\n## सही जन्म समय कैसे जानें\n- जन्म प्रमाणपत्र देखें\n- माता-पिता से पूछें\n- अस्पताल रिकॉर्ड\n- अनुभवी ज्योतिषी से जन्म समय शोधन",
            "category": "astrology",
            "author": "Surendra Kumar Patawari",
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "post_id": "POST_SAMPLE_04",
            "title_en": "Diwali 2026 Muhurat - Best Times for Lakshmi Puja",
            "title_hi": "दिवाली 2026 मुहूर्त - लक्ष्मी पूजा के सर्वश्रेष्ठ समय",
            "content_en": "Diwali is the most important festival for performing Lakshmi Puja. The timing of the puja is crucial for maximum benefits.\n\n## Key Timings\n- Pradosh Kaal and Sthir Lagna are the ideal time\n- Amavasya Tithi must be active during puja\n\n## Puja Vidhi\n1. Clean and decorate your home\n2. Light diyas in all directions\n3. Place Lakshmi and Ganesh idols facing East\n4. Offer lotus flowers, sweets, and coins\n5. Chant Lakshmi Stotram",
            "content_hi": "दिवाली लक्ष्मी पूजा के लिए सबसे महत्वपूर्ण त्योहार है। पूजा का समय बहुत महत्वपूर्ण है।\n\n## प्रमुख समय\n- प्रदोष काल और स्थिर लग्न सर्वश्रेष्ठ\n- अमावस्या तिथि पूजा के दौरान सक्रिय होनी चाहिए\n\n## पूजा विधि\n1. घर की सफाई और सजावट करें\n2. सभी दिशाओं में दीप जलाएं\n3. लक्ष्मी-गणेश मूर्ति पूर्व दिशा में रखें\n4. कमल पुष्प, मिठाई और सिक्के अर्पित करें",
            "category": "panchang",
            "author": "Surendra Kumar Patawari",
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "post_id": "POST_SAMPLE_05",
            "title_en": "Rahu-Ketu Transit Effects and Remedies",
            "title_hi": "राहु-केतु गोचर के प्रभाव और उपाय",
            "content_en": "Rahu and Ketu are shadow planets that change signs approximately every 18 months. Their transit can bring sudden changes in life.\n\n## Effects of Rahu Transit\n- Sudden gains or losses\n- Foreign travel opportunities\n- Confusion in decision making\n- Obsessive behavior\n\n## Effects of Ketu Transit\n- Spiritual awakening\n- Detachment from material things\n- Past life karma surfacing\n\n## Remedies for Rahu\n- Donate black sesame on Saturdays\n- Keep a piece of silver in your wallet\n- Chant 'Om Raam Rahave Namaha'\n\n## Remedies for Ketu\n- Feed stray dogs\n- Donate blankets to the poor\n- Chant 'Om Kem Ketave Namaha'",
            "content_hi": "राहु और केतु छाया ग्रह हैं जो लगभग हर 18 महीने में राशि बदलते हैं।\n\n## राहु गोचर के प्रभाव\n- अचानक लाभ या हानि\n- विदेश यात्रा के अवसर\n- निर्णय लेने में भ्रम\n\n## केतु गोचर के प्रभाव\n- आध्यात्मिक जागृति\n- भौतिक वस्तुओं से वैराग्य\n\n## राहु के उपाय\n- शनिवार को काले तिल का दान करें\n- बटुए में चांदी का टुकड़ा रखें\n\n## केतु के उपाय\n- आवारा कुत्तों को खिलाएं\n- गरीबों को कंबल दान करें",
            "category": "remedies",
            "author": "Surendra Kumar Patawari",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    ]
    
    for post in sample_posts:
        await db.blog_posts.insert_one(post)
    logger.info(f"Seeded {len(sample_posts)} blog posts")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
