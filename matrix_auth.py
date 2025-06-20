import asyncio
import os
from nio import AsyncClient, LoginError, LogoutError
import logging

logger = logging.getLogger(__name__)

class MatrixAuthManager:
    """
    Manages Matrix authentication, including login, token refresh, and session persistence.
    """
    
    def __init__(self, homeserver_url, user_id, password, device_name="CustomMatrixClient"):
        self.homeserver_url = homeserver_url
        self.user_id = user_id
        self.password = password
        self.device_name = device_name
        self.client = None
        
        # Store directory for session persistence
        self.store_path = "./matrix_store"
        
    async def get_authenticated_client(self):
        """
        Returns an authenticated AsyncClient instance.
        Handles login and session persistence with rate limiting protection.
        """
        try:
            # Create store directory if it doesn't exist
            os.makedirs(self.store_path, exist_ok=True)
            
            # Create client with persistent store
            self.client = AsyncClient(
                homeserver=self.homeserver_url,
                user=self.user_id,
                store_path=self.store_path,
                config=None
            )
            
            # Try to restore previous session first
            try:
                self.client.load_store()
                if self.client.access_token and self.client.device_id:
                    logger.info(f"Restored session from store - Token: {self.client.access_token[:20]}..., Device: {self.client.device_id}")
                    logger.info("Skipping login to avoid rate limiting")
                    return self.client
                else:
                    logger.info(f"Session load attempted but missing credentials - Token: {bool(self.client.access_token)}, Device: {bool(self.client.device_id)}")
            except Exception as e:
                logger.warning(f"Could not restore session: {e}")
            
            # Only attempt login if we don't have stored credentials
            if not self.client.access_token:
                logger.info("No stored session found, attempting login...")
                try:
                    response = await self.client.login(
                        password=self.password,
                        device_name=self.device_name
                    )
                    
                    if isinstance(response, LoginError):
                        logger.error(f"Login failed: {response.message}")
                        if "429" in str(response.message) or "rate" in str(response.message).lower():
                            logger.error("Rate limited! Please wait before trying again or use existing session.")
                        await self.client.close()
                        return None
                    
                    logger.info(f"Login successful. User ID: {self.client.user_id}, Device ID: {self.client.device_id}")
                    
                except Exception as login_error:
                    logger.error(f"Login attempt failed: {login_error}")
                    if "429" in str(login_error) or "rate" in str(login_error).lower():
                        logger.error("Rate limited! The Matrix server is temporarily blocking login attempts.")
                        logger.error("This is normal protection against brute force attacks.")
                        logger.error("Please wait for the rate limit to reset before trying again.")
                    await self.client.close()
                    return None
            
            return self.client
            
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            if self.client:
                await self.client.close()
            return None
    
    async def ensure_valid_token(self, client):
        """
        Ensures the client has a valid access token.
        Simplified version that assumes token is valid if client exists.
        """
        if not client or not client.access_token:
            logger.warning("No valid access token found")
            return False
        
        # If we have a client with an access token, assume it's valid
        # The nio library will handle token refresh automatically
        logger.debug("Token validation - client and access token present")
        return True
    
    async def logout(self):
        """
        Logs out and cleans up the session.
        """
        if self.client:
            try:
                response = await self.client.logout()
                if isinstance(response, LogoutError):
                    logger.error(f"Logout error: {response.message}")
                else:
                    logger.info("Logout successful")
                
                # Clean up store
                try:
                    store_file = f"{self.store_path}/{self.user_id}_{self.device_name}.db"
                    if os.path.exists(store_file):
                        os.remove(store_file)
                        logger.info("Session store cleaned up")
                except Exception as e:
                    logger.warning(f"Failed to clean up store: {e}")
                
            except Exception as e:
                logger.error(f"Logout failed: {e}")
            finally:
                await self.client.close()
                self.client = None
