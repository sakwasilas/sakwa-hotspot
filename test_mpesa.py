import os
from dotenv import load_dotenv
import requests
import base64
import json
from datetime import datetime

# Load environment variables
load_dotenv()

# Get credentials
consumer_key = os.getenv('MPESA_CONSUMER_KEY')
consumer_secret = os.getenv('MPESA_CONSUMER_SECRET')
business_shortcode = os.getenv('MPESA_BUSINESS_SHORTCODE')
passkey = os.getenv('MPESA_PASSKEY')

print("=" * 50)
print("M-Pesa Sandbox Test")
print("=" * 50)
print(f"Consumer Key: {consumer_key[:20]}...")
print(f"Business Shortcode: {business_shortcode}")
print(f"Environment: Sandbox")
print()

# Test 1: Get Access Token
print("1. Testing Access Token...")
auth_url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
auth_string = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode()).decode()

headers = {
    "Authorization": f"Basic {auth_string}"
}

try:
    response = requests.get(auth_url, headers=headers, timeout=30)
    
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get('access_token')
        print(f"✅ Access token obtained successfully!")
        print(f"   Token: {access_token[:30]}...")
        
        # Test 2: STK Push
        print("\n2. Testing STK Push...")
        
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password_str = f"{business_shortcode}{passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "BusinessShortCode": business_shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": "10",
            "PartyA": "254708374149",  # Test phone number
            "PartyB": business_shortcode,
            "PhoneNumber": "254708374149",
            "CallBackURL": "https://your-ngrok-url.ngrok.io/mpesa-callback",
            "AccountReference": "TestWiFi",
            "TransactionDesc": "Test Payment"
        }
        
        stk_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        response = requests.post(stk_url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ResponseCode') == '0':
                print(f"✅ STK Push sent successfully!")
                print(f"   Checkout Request ID: {result.get('CheckoutRequestID')}")
                print(f"   Response Description: {result.get('ResponseDescription')}")
            else:
                print(f"❌ STK Push failed: {result}")
        else:
            print(f"❌ STK Push HTTP Error: {response.status_code}")
            print(f"   Response: {response.text}")
            
    else:
        print(f"❌ Failed to get access token: {response.status_code}")
        print(f"   Response: {response.text}")
        
except Exception as e:
    print(f"❌ Error: {e}")

print("\n" + "=" * 50)
print("If you see '✅' for both tests, your integration is working!")
print("If not, check your credentials and try again.")