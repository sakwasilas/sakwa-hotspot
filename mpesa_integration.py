import requests
import base64
import datetime
import json
import logging
from connections import SessionLocal
from models import PaymentTransaction, HotspotSession
from datetime import datetime as dt, timedelta
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MpesaIntegration:
    def __init__(self, consumer_key, consumer_secret, business_shortcode, passkey, environment='sandbox', callback_url=None):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.business_shortcode = business_shortcode
        self.passkey = passkey
        self.environment = environment
        self.callback_url = callback_url
        
        if environment == 'sandbox':
            self.base_url = "https://sandbox.safaricom.co.ke"
        else:
            self.base_url = "https://api.safaricom.co.ke"
    
    def get_access_token(self):
        """Get OAuth2 access token from M-Pesa"""
        auth_url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        auth_string = base64.b64encode(f"{self.consumer_key}:{self.consumer_secret}".encode()).decode()
        
        headers = {
            "Authorization": f"Basic {auth_string}"
        }
        
        try:
            response = requests.get(auth_url, headers=headers, timeout=30)
            response.raise_for_status()
            token = response.json()['access_token']
            logger.info("✅ M-Pesa access token obtained")
            return token
        except Exception as e:
            logger.error(f"Failed to get access token: {e}")
            return None
    
    def stk_push(self, phone_number, amount, package, transaction_id, account_reference=None):
        """Send STK push to customer's phone"""
        access_token = self.get_access_token()
        if not access_token:
            return None
        
        timestamp = dt.now().strftime('%Y%m%d%H%M%S')
        password_str = f"{self.business_shortcode}{self.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()
        
        # Format phone number (remove leading 0 or +254)
        original_phone = phone_number
        if phone_number.startswith('0'):
            phone_number = '254' + phone_number[1:]
        elif phone_number.startswith('+'):
            phone_number = phone_number[1:]
        
        # Ensure phone number is exactly 12 digits
        if len(phone_number) != 12:
            logger.warning(f"Phone number {phone_number} may not be properly formatted")
        
        if not account_reference:
            account_reference = f"Hotspot_{package}"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "BusinessShortCode": self.business_shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone_number,
            "PartyB": self.business_shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference[:12],  # Max 12 characters
            "TransactionDesc": f"WiFi {package} package"
        }
        
        try:
            logger.info(f"Initiating STK push to {original_phone} for KSh {amount}")
            response = requests.post(
                f"{self.base_url}/mpesa/stkpush/v1/processrequest",
                headers=headers,
                json=payload,
                timeout=30
            )
            result = response.json()
            
            logger.info(f"STK push response: {result}")
            
            # Save transaction with checkout request ID
            if result.get('ResponseCode') == '0':
                session = SessionLocal()
                try:
                    # Update or create transaction with checkout request ID
                    transaction = session.query(PaymentTransaction).filter(
                        PaymentTransaction.transaction_id == transaction_id
                    ).first()
                    
                    if transaction:
                        transaction.mpesa_receipt = result.get('CheckoutRequestID')
                        transaction.result_desc = "Payment initiated"
                        session.commit()
                        logger.info(f"✅ STK push sent successfully. Checkout ID: {result.get('CheckoutRequestID')}")
                    else:
                        logger.error(f"Transaction {transaction_id} not found")
                    
                    return result
                finally:
                    session.close()
            else:
                logger.error(f"STK push failed: {result}")
                return result
                
        except Exception as e:
            logger.error(f"STK push exception: {e}")
            return None
    
    def check_transaction_status(self, checkout_request_id):
        """Check status of a pending transaction"""
        access_token = self.get_access_token()
        if not access_token:
            return None
        
        timestamp = dt.now().strftime('%Y%m%d%H%M%S')
        password_str = f"{self.business_shortcode}{self.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "BusinessShortCode": self.business_shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/mpesa/stkpushquery/v1/query",
                headers=headers,
                json=payload,
                timeout=30
            )
            return response.json()
        except Exception as e:
            logger.error(f"Error checking transaction status: {e}")
            return None
    
    def handle_callback(self, callback_data):
        """Handle M-Pesa callback after payment"""
        session = SessionLocal()
        try:
            logger.info(f"Received M-Pesa callback: {callback_data}")
            
            # Parse callback data
            body = callback_data.get('Body', {})
            stk_callback = body.get('stkCallback', {})
            
            result_code = stk_callback.get('ResultCode')
            checkout_request_id = stk_callback.get('CheckoutRequestID')
            result_desc = stk_callback.get('ResultDesc')
            callback_metadata = stk_callback.get('CallbackMetadata', {})
            
            # Find transaction by checkout request ID
            transaction = session.query(PaymentTransaction).filter(
                PaymentTransaction.mpesa_receipt == checkout_request_id
            ).first()
            
            if transaction:
                transaction.result_code = result_code
                transaction.result_desc = result_desc
                
                if result_code == 0:  # Payment successful
                    transaction.status = 'completed'
                    transaction.completed_at = dt.utcnow()
                    
                    # Get M-Pesa receipt number
                    if callback_metadata and callback_metadata.get('Item'):
                        for item in callback_metadata.get('Item', []):
                            if item.get('Name') == 'MpesaReceiptNumber':
                                transaction.mpesa_receipt = item.get('Value')
                                break
                    
                    session.commit()
                    logger.info(f"✅ Payment completed for transaction {transaction.transaction_id}")
                    
                    # Activate hotspot session
                    self.activate_hotspot_session(transaction)
                    return True
                    
                else:  # Payment failed
                    transaction.status = 'failed'
                    session.commit()
                    logger.warning(f"❌ Payment failed: {result_desc}")
                    return False
            else:
                logger.error(f"Transaction not found for CheckoutRequestID: {checkout_request_id}")
                return False
                
        except Exception as e:
            logger.error(f"Callback handling failed: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def activate_hotspot_session(self, transaction):
        """Activate hotspot session after successful payment"""
        from hotspot_manager import hotspot_manager
        
        session = SessionLocal()
        try:
            # Get package details
            packages = {
                '1hour': {'duration': 1, 'price': 10},
                '3hours': {'duration': 3, 'price': 20},
                '5hours': {'duration': 5, 'price': 30},
                '1day': {'duration': 24, 'price': 50}
            }
            
            package = packages.get(transaction.package)
            if not package:
                logger.error(f"Invalid package: {transaction.package}")
                return False
            
            end_time = dt.utcnow() + timedelta(hours=package['duration'])
            
            # Check if session already exists
            existing_session = session.query(HotspotSession).filter(
                HotspotSession.mpesa_transaction_id == transaction.mpesa_receipt
            ).first()
            
            if existing_session:
                logger.info(f"Session already exists for transaction {transaction.transaction_id}")
                return True
            
            # Create hotspot session
            hotspot_session = HotspotSession(
                phone_number=transaction.phone_number,
                mac_address=transaction.mac_address,
                package=transaction.package,
                amount=transaction.amount,
                mpesa_transaction_id=transaction.mpesa_receipt,
                end_time=end_time,
                is_active=True
            )
            session.add(hotspot_session)
            session.commit()
            
            # Add MAC to MikroTik whitelist
            if transaction.mac_address:
                mac_added = hotspot_manager.add_mac_to_whitelist(
                    mac_address=transaction.mac_address,
                    phone_number=transaction.phone_number,
                    package=transaction.package,
                    duration_hours=package['duration']
                )
                
                if mac_added:
                    logger.info(f"✅ Internet activated for MAC: {transaction.mac_address}")
                    return True
                else:
                    logger.error(f"❌ Failed to add MAC to hotspot: {transaction.mac_address}")
                    return False
            else:
                logger.warning("No MAC address provided")
                return True
                
        except Exception as e:
            logger.error(f"Error activating session: {e}")
            session.rollback()
            return False
        finally:
            session.close()

# Create global instance (will be configured in app.py)
mpesa = None