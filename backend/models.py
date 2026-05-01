from pydantic import BaseModel, Field, EmailStr, ConfigDict
from typing import Optional, List
from datetime import datetime
import uuid

class User(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str = Field(default_factory=lambda: f"user_{uuid.uuid4().hex[:12]}")
    email: EmailStr
    name: str
    password_hash: Optional[str] = None
    picture: Optional[str] = None
    phone: Optional[str] = None
    dob: Optional[str] = None
    time_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    gender: Optional[str] = None
    language: str = "hi"
    first_time_free_used: bool = False
    wallet_balance: float = 0.0
    loyalty_points: int = 0
    referral_code: str = Field(default_factory=lambda: f"REF{uuid.uuid4().hex[:8].upper()}")
    referred_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class UserSession(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str = Field(default_factory=lambda: f"session_{uuid.uuid4().hex}")
    user_id: str
    session_token: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AstrologyBooking(BaseModel):
    model_config = ConfigDict(extra="ignore")
    booking_id: str = Field(default_factory=lambda: f"AST{uuid.uuid4().hex[:8].upper()}")
    user_id: str
    package_type: str
    duration_minutes: int
    amount: float
    booking_date: str
    booking_time: str
    info_box: Optional[str] = None
    status: str = "pending"
    payment_status: str = "pending"
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    meet_link: Optional[str] = None
    session_start_time: Optional[datetime] = None
    session_end_time: Optional[datetime] = None
    actual_duration_minutes: Optional[int] = None
    final_amount: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class VastuBooking(BaseModel):
    model_config = ConfigDict(extra="ignore")
    booking_id: str = Field(default_factory=lambda: f"VST{uuid.uuid4().hex[:8].upper()}")
    user_id: str
    service_type: str
    amount: float
    booking_date: str
    booking_time: str
    info_box: Optional[str] = None
    status: str = "pending"
    payment_status: str = "pending"
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    floor_plan_url: Optional[str] = None
    report_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SiteVisitEnquiry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enquiry_id: str = Field(default_factory=lambda: f"SITE{uuid.uuid4().hex[:8].upper()}")
    user_id: str
    name: str
    phone: str
    city: str
    state: str
    property_type: str
    property_size: str
    preferred_visit_date: str
    issues: Optional[str] = None
    status: str = "pending"
    custom_amount: Optional[float] = None
    payment_link: Optional[str] = None
    payment_status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Invoice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    invoice_id: str = Field(default_factory=lambda: f"INV{uuid.uuid4().hex[:8].upper()}")
    user_id: str
    booking_id: str
    booking_type: str
    amount: float
    duration_minutes: Optional[int] = None
    invoice_url: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SessionReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    report_id: str = Field(default_factory=lambda: f"RPT{uuid.uuid4().hex[:8].upper()}")
    user_id: str
    booking_id: str
    booking_type: str
    report_content: str
    report_url: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class BlogPost(BaseModel):
    model_config = ConfigDict(extra="ignore")
    post_id: str = Field(default_factory=lambda: f"POST{uuid.uuid4().hex[:8].upper()}")
    title_en: str
    title_hi: str
    content_en: str
    content_hi: str
    author: str = "Surendra Kumar Patawari"
    category: str
    image_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class WalletTransaction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    transaction_id: str = Field(default_factory=lambda: f"TXN{uuid.uuid4().hex[:8].upper()}")
    user_id: str
    amount: float
    type: str
    description: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Referral(BaseModel):
    model_config = ConfigDict(extra="ignore")
    referral_id: str = Field(default_factory=lambda: f"REF{uuid.uuid4().hex[:8].upper()}")
    referrer_user_id: str
    referred_user_id: str
    referral_code: str
    status: str = "pending"
    referrer_credit: float = 0.0
    referred_discount: float = 200.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
