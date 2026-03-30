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
from mpesa_config import MPESA_CONFIG  # Import from config file

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'  # Change this!

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Validate M-Pesa configuration
if not MPESA_CONFIG.get('consumer_key') or not MPESA_CONFIG.get('consumer_secret'):
    logger.warning("⚠️ M-Pesa credentials not configured. Payments will use simulation mode.")
    # Set simulation mode flag
    USE_MPESA_SIMULATION = True
else:
    USE_MPESA_SIMULATION = False
    logger.info("✅ M-Pesa configured. Using real payments.")

# Initialize M-Pesa (only if credentials are provided)
if not USE_MPESA_SIMULATION:
    mpesa = MpesaIntegration(
        consumer_key=MPESA_CONFIG['consumer_key'],
        consumer_secret=MPESA_CONFIG['consumer_secret'],
        business_shortcode=MPESA_CONFIG['business_shortcode'],
        passkey=MPESA_CONFIG['passkey'],
        environment=MPESA_CONFIG['environment'],
        callback_url=MPESA_CONFIG['callback_url']
    )
else:
    mpesa = None
    logger.info("Using simulated payment mode")

# Hotspot packages
PACKAGES = {
    '1hour': {'name': '1 Hour', 'price': 10, 'duration': 1, 'description': 'Perfect for quick browsing'},
    '3hours': {'name': '3 Hours', 'price': 20, 'duration': 3, 'description': 'Great for streaming'},
    '5hours': {'name': '5 Hours', 'price': 30, 'duration': 5, 'description': 'Best value for casual use'},
    '1day': {'name': '24 Hours', 'price': 50, 'duration': 24, 'description': 'Full day of internet'}
}

@app.route('/')
def index():
    """Landing page with available packages"""
    # Get MAC from URL or session
    mac_address = request.args.get('mac', flask_session.get('device_mac', ''))
    ip_address = request.args.get('ip', flask_session.get('device_ip', ''))
    
    # Store in session
    if mac_address:
        flask_session['device_mac'] = mac_address
    if ip_address:
        flask_session['device_ip'] = ip_address
    
    # Check if this MAC already has an active session
    if mac_address:
        db = SessionLocal()
        try:
            active_session = db.query(HotspotSession).filter(
                HotspotSession.mac_address == mac_address,
                HotspotSession.is_active == True,
                HotspotSession.end_time > datetime.utcnow()
            ).first()
            
            if active_session:
                # User already has active internet
                remaining = active_session.end_time - datetime.utcnow()
                return render_template('active_session.html', 
                                     session=active_session,
                                     remaining_hours=remaining.total_seconds() / 3600)
        finally:
            db.close()
    
    return render_template('index.html', 
                         packages=PACKAGES,
                         mac=mac_address,
                         ip=ip_address,
                         simulation_mode=USE_MPESA_SIMULATION)

@app.route('/hotspot-login')
def hotspot_login():
    """Redirect to index page with MAC parameters"""
    mac_address = request.args.get('mac', '')
    ip_address = request.args.get('ip', '')
    target_url = request.args.get('url', '')
    
    # Store in session
    flask_session['device_mac'] = mac_address
    flask_session['device_ip'] = ip_address
    
    # Redirect to index with MAC in URL
    return redirect(url_for('index', mac=mac_address, ip=ip_address))

@app.route('/select-package', methods=['POST'])
def select_package():
    """Process package selection and initiate M-Pesa payment"""
    package_id = request.form.get('package')
    phone_number = request.form.get('phone_number')
    mac_address = request.form.get('mac_address', flask_session.get('device_mac', ''))
    ip_address = request.form.get('ip_address', flask_session.get('device_ip', ''))
    
    # Validate inputs
    if not phone_number:
        return jsonify({'error': 'Phone number is required'}), 400
    
    if package_id not in PACKAGES:
        return jsonify({'error': 'Invalid package selected'}), 400
    
    package = PACKAGES[package_id]
    
    # Store in session for callback
    flask_session['pending_package'] = package_id
    flask_session['pending_phone'] = phone_number
    flask_session['pending_mac'] = mac_address
    flask_session['pending_ip'] = ip_address
    
    # Generate unique transaction ID
    transaction_id = str(uuid.uuid4())
    flask_session['transaction_id'] = transaction_id
    
    # Save pending transaction to database
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
        
        # If using simulation mode or no M-Pesa configured
        if USE_MPESA_SIMULATION or not mpesa:
            logger.info("Using simulated payment mode")
            return render_template('payment.html', 
                                 phone=phone_number,
                                 amount=package['price'],
                                 package=package['name'],
                                 transaction_id=transaction_id,
                                 simulation=True)
        
        # Initiate M-Pesa STK Push
        result = mpesa.stk_push(
            phone_number=phone_number,
            amount=package['price'],
            package=package_id,
            transaction_id=transaction_id,
            account_reference=f"WiFi{package_id[:5]}"
        )
        
        if result and result.get('ResponseCode') == '0':
            # STK Push sent successfully
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
        logger.error(f"Error creating transaction: {e}")
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

@app.route('/simulate-payment', methods=['POST'])
def simulate_payment():
    """Simulate successful payment (fallback when M-Pesa is not configured)"""
    if not USE_MPESA_SIMULATION:
        return jsonify({'error': 'Not in simulation mode'}), 400
    
    transaction_id = flask_session.get('transaction_id')
    
    if not transaction_id:
        return jsonify({'error': 'No transaction found'}), 400
    
    db = SessionLocal()
    try:
        # Get the pending transaction
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.transaction_id == transaction_id
        ).first()
        
        if not transaction:
            return jsonify({'error': 'Transaction not found'}), 404
        
        # Mark as completed
        transaction.status = 'completed'
        transaction.mpesa_receipt = f"SIM_{transaction_id[:8]}"
        transaction.completed_at = datetime.utcnow()
        transaction.result_code = 0
        transaction.result_desc = "Success (Simulated)"
        db.commit()
        
        # Activate hotspot session
        package = PACKAGES[transaction.package]
        end_time = datetime.utcnow() + timedelta(hours=package['duration'])
        
        # Create hotspot session
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
        
        # Add MAC to MikroTik whitelist
        if transaction.mac_address:
            mac_added = hotspot_manager.add_mac_to_whitelist(
                mac_address=transaction.mac_address,
                phone_number=transaction.phone_number,
                package=transaction.package,
                duration_hours=package['duration']
            )
            
            if mac_added:
                logger.info(f"✅ Internet activated for MAC: {hotspot_session.mac_address}")
                return jsonify({
                    'success': True,
                    'message': 'Payment successful! Internet activated.',
                    'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'duration_hours': package['duration']
                })
        
        return jsonify({
            'success': True,
            'message': 'Payment successful!',
            'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S')
        })
        
    except Exception as e:
        logger.error(f"Error activating session: {e}")
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

@app.route('/check-payment-status')
def check_payment_status():
    """Check payment status for current transaction"""
    transaction_id = flask_session.get('transaction_id')
    
    if not transaction_id:
        return jsonify({'status': 'no_transaction'})
    
    db = SessionLocal()
    try:
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.transaction_id == transaction_id
        ).first()
        
        if transaction:
            # If still pending and we have a checkout ID and not in simulation mode
            if (transaction.status == 'pending' and 
                transaction.mpesa_receipt and 
                not USE_MPESA_SIMULATION and 
                mpesa):
                result = mpesa.check_transaction_status(transaction.mpesa_receipt)
                if result and result.get('ResultCode') == '0':
                    # Transaction completed
                    transaction.status = 'completed'
                    transaction.completed_at = datetime.utcnow()
                    db.commit()
                    
                    # Activate hotspot session
                    mpesa.activate_hotspot_session(transaction)
            
            return jsonify({
                'status': transaction.status,
                'mpesa_receipt': transaction.mpesa_receipt
            })
        else:
            return jsonify({'status': 'not_found'})
    finally:
        db.close()

@app.route('/mpesa-callback', methods=['POST'])
def mpesa_callback():
    """Handle M-Pesa callback"""
    if USE_MPESA_SIMULATION:
        logger.info("Callback received but in simulation mode")
        return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})
    
    try:
        callback_data = request.json
        logger.info(f"Received M-Pesa callback")
        
        # Process the callback
        if mpesa:
            success = mpesa.handle_callback(callback_data)
            
            if success:
                return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})
        
        return jsonify({'ResultCode': 1, 'ResultDesc': 'Failed'}), 400
            
    except Exception as e:
        logger.error(f"Error processing callback: {e}")
        return jsonify({'ResultCode': 1, 'ResultDesc': str(e)}), 500

# Rest of your routes (check-status, success, logout) remain the same...
# [Keep your existing check-status, success, logout routes here]

# Background thread to check and expire sessions periodically
def session_expiry_checker():
    """Background thread to check and expire sessions"""
    while True:
        time.sleep(60)  # Check every minute
        try:
            hotspot_manager.check_active_sessions()
            logger.info("Session expiry check completed")
        except Exception as e:
            logger.error(f"Error in expiry checker: {e}")

# Start the background thread
def start_background_thread():
    thread = threading.Thread(target=session_expiry_checker, daemon=True)
    thread.start()
    logger.info("Session expiry checker started")

# Start the background thread when app starts
with app.app_context():
    start_background_thread()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)