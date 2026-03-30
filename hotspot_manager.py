import subprocess
import requests
import logging
from datetime import datetime, timedelta
from connections import SessionLocal
from models import HotspotSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MikroTikHotspotManager:
    def __init__(self, router_ip="192.168.1.1", router_user="admin", router_pass="", api_port=8728, simulation_mode=True):
        """
        Initialize MikroTik connection
        For testing without actual router, use simulation mode
        """
        self.router_ip = router_ip
        self.router_user = router_user
        self.router_pass = router_pass
        self.api_port = api_port
        self.simulation_mode = simulation_mode
        
        # API URL for REST API (if using RouterOS v7+)
        self.rest_api_url = f"http://{router_ip}:{api_port}/rest"
        
        # SSH command template (alternative method)
        self.ssh_command = f"sshpass -p '{router_pass}' ssh -o StrictHostKeyChecking=no {router_user}@{router_ip}"
    
    def add_mac_to_whitelist(self, mac_address, phone_number, package, duration_hours):
        """
        Add MAC address to hotspot whitelist
        This is the key function for MAC-based authentication
        """
        logger.info(f"Adding MAC {mac_address} to whitelist for {duration_hours} hours")
        
        if self.simulation_mode:
            logger.info(f"[SIMULATION] MAC {mac_address} would be added to whitelist")
            return True
        
        try:
            # Method 1: Using MikroTik REST API (if available)
            if self._check_api_available():
                return self._add_mac_via_api(mac_address, duration_hours)
            
            # Method 2: Using SSH commands
            else:
                return self._add_mac_via_ssh(mac_address, duration_hours)
                
        except Exception as e:
            logger.error(f"Failed to add MAC to whitelist: {e}")
            return False
    
    def _check_api_available(self):
        """Check if MikroTik REST API is available"""
        try:
            response = requests.get(f"{self.rest_api_url}/system/resource", 
                                   auth=(self.router_user, self.router_pass),
                                   timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def _add_mac_via_api(self, mac_address, duration_hours):
        """Add MAC to hotspot using REST API"""
        try:
            # Create hotspot user with MAC address
            payload = {
                "name": mac_address.replace(":", ""),
                "mac-address": mac_address,
                "profile": f"hotspot_{duration_hours}h",
                "limit-uptime": f"{duration_hours}h",
                "comment": f"Auto-created for {duration_hours} hours"
            }
            
            response = requests.put(
                f"{self.rest_api_url}/ip/hotspot/user/{mac_address.replace(':', '')}",
                auth=(self.router_user, self.router_pass),
                json=payload
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ MAC {mac_address} added to hotspot via API")
                return True
            else:
                logger.error(f"API returned: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"API error: {e}")
            return False
    
    def _add_mac_via_ssh(self, mac_address, duration_hours):
        """Add MAC to hotspot using SSH commands"""
        try:
            # Convert MAC format (AA:BB:CC:DD:EE:FF to aabbccddeeff)
            mac_clean = mac_address.replace(":", "").lower()
            
            # Create hotspot user
            commands = [
                f"/ip hotspot user add name={mac_clean} mac-address={mac_address} profile=hotspot_{duration_hours}h limit-uptime={duration_hours}h",
                f"/ip hotspot user enable {mac_clean}"
            ]
            
            for cmd in commands:
                full_cmd = f"{self.ssh_command} '{cmd}'"
                result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"SSH command failed: {result.stderr}")
                    return False
            
            logger.info(f"✅ MAC {mac_address} added to hotspot via SSH")
            return True
            
        except Exception as e:
            logger.error(f"SSH error: {e}")
            return False
    
    def remove_mac_from_whitelist(self, mac_address):
        """Remove MAC from whitelist when session expires"""
        logger.info(f"Removing MAC {mac_address} from whitelist")
        
        if self.simulation_mode:
            logger.info(f"[SIMULATION] MAC {mac_address} would be removed")
            return True
        
        try:
            mac_clean = mac_address.replace(":", "").lower()
            
            if self._check_api_available():
                response = requests.delete(
                    f"{self.rest_api_url}/ip/hotspot/user/{mac_clean}",
                    auth=(self.router_user, self.router_pass)
                )
                return response.status_code in [200, 204, 404]
            else:
                cmd = f"{self.ssh_command} '/ip hotspot user remove {mac_clean}'"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                return result.returncode == 0
                
        except Exception as e:
            logger.error(f"Failed to remove MAC: {e}")
            return False
    
    def check_active_sessions(self):
        """Check and expire sessions that have ended"""
        db = SessionLocal()
        try:
            # Find expired sessions that are still active
            expired_sessions = db.query(HotspotSession).filter(
                HotspotSession.is_active == True,
                HotspotSession.end_time < datetime.utcnow()
            ).all()
            
            for session in expired_sessions:
                logger.info(f"Expiring session for MAC {session.mac_address}")
                session.is_active = False
                self.remove_mac_from_whitelist(session.mac_address)
            
            db.commit()
            if expired_sessions:
                logger.info(f"Expired {len(expired_sessions)} sessions")
            
        except Exception as e:
            logger.error(f"Error checking sessions: {e}")
            db.rollback()
        finally:
            db.close()
    
    def get_active_macs(self):
        """Get list of active MAC addresses from MikroTik"""
        if self.simulation_mode:
            db = SessionLocal()
            try:
                active = db.query(HotspotSession).filter(
                    HotspotSession.is_active == True,
                    HotspotSession.end_time > datetime.utcnow()
                ).all()
                return [s.mac_address for s in active]
            finally:
                db.close()
        
        try:
            if self._check_api_available():
                response = requests.get(
                    f"{self.rest_api_url}/ip/hotspot/user",
                    auth=(self.router_user, self.router_pass)
                )
                if response.status_code == 200:
                    users = response.json()
                    return [user.get('mac-address') for user in users if user.get('mac-address')]
            return []
        except:
            return []
    
    def create_hotspot_profile(self, profile_name, duration_hours):
        """Create hotspot profile for specific package"""
        logger.info(f"Creating hotspot profile: {profile_name}")
        
        if self.simulation_mode:
            logger.info(f"[SIMULATION] Profile {profile_name} would be created")
            return True
        
        try:
            if self._check_api_available():
                payload = {
                    "name": profile_name,
                    "session-timeout": f"{duration_hours}h",
                    "idle-timeout": "15m",
                    "shared-users": 1
                }
                response = requests.put(
                    f"{self.rest_api_url}/ip/hotspot/user/profile/{profile_name}",
                    auth=(self.router_user, self.router_pass),
                    json=payload
                )
                return response.status_code in [200, 201]
        except Exception as e:
            logger.error(f"Failed to create profile: {e}")
            return False
    
    def setup_hotspot(self, interface="ether1"):
        """Initial hotspot setup (run once)"""
        logger.info("Setting up hotspot...")
        
        if self.simulation_mode:
            logger.info("[SIMULATION] Hotspot would be set up")
            return True
        
        commands = [
            f"/ip hotspot set enabled=yes",
            f"/ip hotspot add interface={interface} address-pool=dhcp_pool1",
            f"/ip pool add name=dhcp_pool1 ranges=192.168.100.2-192.168.100.254"
        ]
        
        for cmd in commands:
            if self._check_api_available():
                # API version
                requests.post(f"{self.rest_api_url}/ip/hotspot", 
                            auth=(self.router_user, self.router_pass),
                            json={"interface": interface})
            else:
                # SSH version
                full_cmd = f"{self.ssh_command} '{cmd}'"
                subprocess.run(full_cmd, shell=True)
        
        return True

# Create a global instance
hotspot_manager = MikroTikHotspotManager(
    router_ip="192.168.1.1",  
    router_user="admin",       
    router_pass="silas",            
    simulation_mode=False      
)