from flask import Flask, render_template, request, jsonify, redirect, url_for, session as flask_session
from datetime import datetime, timedelta
import uuid
import logging
import threading
import requests
import time
from connections import SessionLocal
from models import User, HotspotSession, PaymentTransaction
from hotspot_manager import hotspot_manager
from mpesa_integration import MpesaIntegration
from mpesa_config import MPESA_CONFIG

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Check if M-Pesa is configured
if not MPESA_CONFIG.get('consumer_key') or not MPESA_CONFIG.get('consumer_secret') or MPESA_CONFIG.get('consumer_key') == 'your_sandbox_consumer_key_here':
    USE_MPESA_SIMULATION = True
    logger.warning("⚠️ M-Pesa credentials not configured. Using simulation mode.")
    mpesa = None
else:
    USE_MPESA_SIMULATION = False
    logger.info("✅ M-Pesa configured. Using real payments.")
    mpesa = MpesaIntegration(
        consumer_key=MPESA_CONFIG['consumer_key'],
        consumer_secret=MPESA_CONFIG['consumer_secret'],
        business_shortcode=MPESA_CONFIG['business_shortcode'],
        passkey=MPESA_CONFIG['passkey'],
        environment=MPESA_CONFIG['environment'],
        callback_url=MPESA_CONFIG['callback_url']
    )

# Hotspot packages
PACKAGES = {
    '1hour': {'name': '1 Hour', 'price': 10, 'duration': 1, 'description': 'Perfect for quick browsing'},
    '3hours': {'name': '3 Hours', 'price': 20, 'duration': 3, 'description': 'Great for streaming'},
    '5hours': {'name': '5 Hours', 'price': 30, 'duration': 5, 'description': 'Best value for casual use'},
    '1day': {'name': '24 Hours', 'price': 50, 'duration': 24, 'description': 'Full day of internet'}
}

@app.route('/')
def index():
    mac_address = request.args.get('mac', flask_session.get('device_mac', ''))
    ip_address = request.args.get('ip', flask_session.get('device_ip', ''))
    
    if mac_address:
        flask_session['device_mac'] = mac_address
    if ip_address:
        flask_session['device_ip'] = ip_address
    
    if mac_address:
        db = SessionLocal()
        try:
            active_session = db.query(HotspotSession).filter(
                HotspotSession.mac_address == mac_address,
                HotspotSession.is_active == True,
                HotspotSession.end_time > datetime.utcnow()
            ).first()
            
            if active_session:
                remaining = active_session.end_time - datetime.utcnow()
                return render_template('active_session.html', 
                                     session=active_session,
                                     remaining_hours=remaining.total_seconds() / 3600)
        finally:
            db.close()
    
    return render_template('index.html', packages=PACKAGES, mac=mac_address, ip=ip_address)

@app.route('/hotspot-login')
def hotspot_login():
    mac_address = request.args.get('mac', '')
    ip_address = request.args.get('ip', '')
    flask_session['device_mac'] = mac_address
    flask_session['device_ip'] = ip_address
    return redirect(url_for('index', mac=mac_address, ip=ip_address))

@app.route('/select-package', methods=['POST'])
def select_package():
    package_id = request.form.get('package')
    phone_number = request.form.get('phone_number')
    mac_address = request.form.get('mac_address', flask_session.get('device_mac', ''))
    ip_address = request.form.get('ip_address', flask_session.get('device_ip', ''))
    
    if not phone_number:
        return jsonify({'error': 'Phone number is required'}), 400
    
    if package_id not in PACKAGES:
        return jsonify({'error': 'Invalid package selected'}), 400
    
    package = PACKAGES[package_id]
    
    flask_session['pending_package'] = package_id
    flask_session['pending_phone'] = phone_number
    flask_session['pending_mac'] = mac_address
    flask_session['pending_ip'] = ip_address
    
    transaction_id = str(uuid.uuid4())
    flask_session['transaction_id'] = transaction_id
    
    db = SessionLocal()
    try:
        transaction = PaymentTransaction(
            transaction_id=transaction_id,
            phone_number=phone_number,
            mac_address=mac_address,
            amount=package['price'],
            package=package_id,
            status='pending'
        )
        db.add(transaction)
        db.commit()
        
        logger.info(f"Created pending transaction: {transaction_id} for {phone_number}")
        
        if USE_MPESA_SIMULATION or not mpesa:
            logger.info("Using simulated payment mode")
            return render_template('payment.html', 
                                 phone=phone_number,
                                 amount=package['price'],
                                 package=package['name'],
                                 transaction_id=transaction_id,
                                 simulation=True)
        
        result = mpesa.stk_push(
            phone_number=phone_number,
            amount=package['price'],
            package=package_id,
            transaction_id=transaction_id,
            account_reference=f"WiFi{package_id[:5]}"
        )
        
        if result and result.get('ResponseCode') == '0':
            checkout_id = result.get('CheckoutRequestID')
            logger.info(f"✅ STK Push sent. Checkout ID: {checkout_id}")
            
            return render_template('payment.html', 
                                 phone=phone_number,
                                 amount=package['price'],
                                 package=package['name'],
                                 transaction_id=transaction_id,
                                 checkout_id=checkout_id,
                                 simulation=False)
        else:
            error_msg = result.get('errorMessage', 'Failed to initiate payment') if result else 'No response from M-Pesa'
            logger.error(f"STK Push failed: {error_msg}")
            return render_template('payment_error.html', 
                                 error=error_msg,
                                 phone=phone_number,
                                 amount=package['price'])
        
    except Exception as e:
        logger.error(f"Error: {e}")
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

@app.route('/simulate-payment', methods=['POST'])
def simulate_payment():
    if not USE_MPESA_SIMULATION:
        return jsonify({'error': 'M-Pesa is configured. Use real payments.'}), 400
    
    transaction_id = flask_session.get('transaction_id')
    if not transaction_id:
        return jsonify({'error': 'No transaction found'}), 400
    
    db = SessionLocal()
    try:
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.transaction_id == transaction_id
        ).first()
        
        if not transaction:
            return jsonify({'error': 'Transaction not found'}), 404
        
        transaction.status = 'completed'
        transaction.mpesa_receipt = f"SIM_{transaction_id[:8]}"
        transaction.completed_at = datetime.utcnow()
        transaction.result_code = 0
        transaction.result_desc = "Success (Simulated)"
        db.commit()
        
        package = PACKAGES[transaction.package]
        end_time = datetime.utcnow() + timedelta(hours=package['duration'])
        
        hotspot_session = HotspotSession(
            phone_number=transaction.phone_number,
            mac_address=transaction.mac_address,
            ip_address=flask_session.get('pending_ip', ''),
            package=transaction.package,
            amount=transaction.amount,
            mpesa_transaction_id=transaction.mpesa_receipt,
            end_time=end_time,
            is_active=True
        )
        db.add(hotspot_session)
        db.commit()
        
        if transaction.mac_address:
            mac_added = hotspot_manager.add_mac_to_whitelist(
                mac_address=transaction.mac_address,
                phone_number=transaction.phone_number,
                package=transaction.package,
                duration_hours=package['duration']
            )
            
            if mac_added:
                logger.info(f"✅ Internet activated for MAC: {hotspot_session.mac_address}")
        
        return jsonify({'success': True, 'message': 'Payment successful!'})
        
    except Exception as e:
        logger.error(f"Error: {e}")
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

@app.route('/mpesa-callback', methods=['POST'])
def mpesa_callback():
    try:
        callback_data = request.get_json()
        if not callback_data:
            logger.warning("No JSON data received")
            return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'}), 200
        
        logger.info(f"Received M-Pesa callback")
        
        if mpesa:
            success = mpesa.handle_callback(callback_data)
            if success:
                logger.info("✅ Callback processed successfully")
            else:
                logger.error("❌ Callback processing failed")
        
        return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'}), 200
            
    except Exception as e:
        logger.error(f"Error processing callback: {e}")
        return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'}), 200

@app.route('/check-payment-status')
def check_payment_status():
    transaction_id = flask_session.get('transaction_id')
    if not transaction_id:
        return jsonify({'status': 'no_transaction'})
    
    db = SessionLocal()
    try:
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.transaction_id == transaction_id
        ).first()
        
        if transaction:
            return jsonify({'status': transaction.status})
        else:
            return jsonify({'status': 'not_found'})
    finally:
        db.close()

@app.route('/check-status')
def check_status():
    mac_address = request.args.get('mac', flask_session.get('device_mac', ''))
    if not mac_address:
        return jsonify({'active': False, 'error': 'No MAC address provided'})
    
    db = SessionLocal()
    try:
        active_session = db.query(HotspotSession).filter(
            HotspotSession.mac_address == mac_address,
            HotspotSession.is_active == True,
            HotspotSession.end_time > datetime.utcnow()
        ).first()
        
        if active_session:
            remaining = active_session.end_time - datetime.utcnow()
            return jsonify({
                'active': True,
                'remaining_seconds': remaining.total_seconds(),
                'remaining_hours': round(remaining.total_seconds() / 3600, 1),
                'package': active_session.package,
                'end_time': active_session.end_time.strftime('%Y-%m-%d %H:%M:%S')
            })
        else:
            return jsonify({'active': False})
    finally:
        db.close()

@app.route('/success')
def success():
    return render_template('success.html')

@app.route('/logout')
def logout():
    mac_address = request.args.get('mac', flask_session.get('device_mac', ''))
    if mac_address:
        db = SessionLocal()
        try:
            session_record = db.query(HotspotSession).filter(
                HotspotSession.mac_address == mac_address,
                HotspotSession.is_active == True
            ).first()
            if session_record:
                session_record.is_active = False
                db.commit()
                hotspot_manager.remove_mac_from_whitelist(mac_address)
        finally:
            db.close()
    return redirect(url_for('index'))

def session_expiry_checker():
    while True:
        time.sleep(60)
        try:
            hotspot_manager.check_active_sessions()
        except Exception as e:
            logger.error(f"Error in expiry checker: {e}")

def start_background_thread():
    thread = threading.Thread(target=session_expiry_checker, daemon=True)
    thread.start()
    logger.info("Session expiry checker started")

with app.app_context():
    start_background_thread()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)