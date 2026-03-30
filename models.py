from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text
from datetime import datetime
from connections import Base
import uuid

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String(15), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

class HotspotSession(Base):
    __tablename__ = 'hotspot_sessions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), unique=True, default=lambda: str(uuid.uuid4()))
    phone_number = Column(String(15), nullable=False)
    mac_address = Column(String(17), nullable=False, index=True)  # MAC is key identifier
    ip_address = Column(String(45))
    package = Column(String(20))  # '1hour', '3hours', etc
    amount = Column(Float)
    mpesa_transaction_id = Column(String(100))
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime)
    is_active = Column(Boolean, default=True)
    data_used = Column(Float, default=0.0)  # MB used
    created_at = Column(DateTime, default=datetime.utcnow)

class PaymentTransaction(Base):
    __tablename__ = 'payment_transactions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String(100), unique=True)
    phone_number = Column(String(15))
    mac_address = Column(String(17))  # Store MAC for reference
    amount = Column(Float)
    package = Column(String(20))
    status = Column(String(20))  # pending, completed, failed
    mpesa_receipt = Column(String(100))
    result_code = Column(Integer)
    result_desc = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)