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

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'  # Change this!

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
                HotspotSession.end_time > datetime.utcnow()  # Use naive datetime
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
                         ip=ip_address)

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
    """Process package selection and initiate payment"""
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
        
        return render_template('payment.html', 
                             phone=phone_number,
                             amount=package['price'],
                             package=package['name'],
                             transaction_id=transaction_id)
        
    except Exception as e:
        logger.error(f"Error creating transaction: {e}")
        db.rollback()
        return jsonify({'error': 'Failed to process payment'}), 500
    finally:
        db.close()

@app.route('/simulate-payment', methods=['POST'])
def simulate_payment():
    """Simulate successful payment and activate internet (testing without M-Pesa)"""
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
        
        # CRITICAL: Add MAC to MikroTik hotspot whitelist
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
            else:
                logger.error(f"❌ Failed to add MAC to hotspot: {hotspot_session.mac_address}")
                return jsonify({
                    'success': False,
                    'error': 'Failed to activate internet. Please contact support.'
                }), 500
        else:
            logger.warning("No MAC address provided, skipping whitelist addition")
            return jsonify({
                'success': True,
                'message': 'Payment successful! Please reconnect to WiFi.',
                'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
                'duration_hours': package['duration']
            })
        
    except Exception as e:
        logger.error(f"Error activating session: {e}")
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

@app.route('/check-status')
def check_status():
    """Check if current device has active internet"""
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
            remaining_seconds = remaining.total_seconds()
            
            return jsonify({
                'active': True,
                'remaining_seconds': remaining_seconds,
                'remaining_hours': round(remaining_seconds / 3600, 1),
                'package': active_session.package,
                'start_time': active_session.start_time.strftime('%Y-%m-%d %H:%M:%S'),
                'end_time': active_session.end_time.strftime('%Y-%m-%d %H:%M:%S')
            })
        else:
            return jsonify({'active': False})
            
    except Exception as e:
        logger.error(f"Error checking status: {e}")
        return jsonify({'active': False, 'error': str(e)})
    finally:
        db.close()

@app.route('/success')
def success():
    """Success page after payment"""
    return render_template('success.html')

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
            return jsonify({
                'status': transaction.status,
                'mpesa_receipt': transaction.mpesa_receipt
            })
        else:
            return jsonify({'status': 'not_found'})
    finally:
        db.close()

@app.route('/logout')
def logout():
    """Logout user and deactivate session"""
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
                
                # Remove from MikroTik
                hotspot_manager.remove_mac_from_whitelist(mac_address)
                logger.info(f"User logged out: {mac_address}")
        finally:
            db.close()
    
    return redirect(url_for('index'))

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