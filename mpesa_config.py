import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# M-Pesa Configuration
MPESA_CONFIG = {
    'consumer_key': os.getenv('MPESA_CONSUMER_KEY'),
    'consumer_secret': os.getenv('MPESA_CONSUMER_SECRET'),
    'business_shortcode': os.getenv('MPESA_BUSINESS_SHORTCODE', '174379'),
    'passkey': os.getenv('MPESA_PASSKEY'),
    'environment': os.getenv('MPESA_ENVIRONMENT', 'sandbox'),
    'callback_url': os.getenv('MPESA_CALLBACK_URL', 'https://your-domain.com/mpesa-callback')
}

# Validate required credentials
def validate_config():
    """Check if all required credentials are set"""
    required = ['consumer_key', 'consumer_secret', 'passkey']
    missing = [key for key in required if not MPESA_CONFIG.get(key)]
    
    if missing:
        print(f"⚠️ Warning: Missing M-Pesa credentials: {', '.join(missing)}")
        print("Please set them in your .env file")
        return False
    return True

# Print status
if __name__ == "__main__":
    if validate_config():
        print("✅ M-Pesa configuration loaded successfully")
        print(f"Environment: {MPESA_CONFIG['environment']}")
        print(f"Business Shortcode: {MPESA_CONFIG['business_shortcode']}")
        print(f"Callback URL: {MPESA_CONFIG['callback_url']}")
    else:
        print("❌ M-Pesa configuration incomplete")