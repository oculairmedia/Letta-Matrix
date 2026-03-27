import asyncio
import os
from nio import AsyncClient, LoginError, LogoutError
import logging

logger = logging.getLogger(__name__)


class MatrixAuthManager:
    """
    Manages Matrix authentication, including login, token refresh, and session persistence.
    """

    def __init__(self, homeserver_url, user_id, password, device_name='CustomMatrixClient'):
        self.homeserver_url = homeserver_url
        self.user_id = user_id
        self.password = password
        self.device_name = device_name
        self.client = None

        # Store directory for session persistence
        self.store_path = './matrix_store'

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
                config=None,
            )

            # Try to restore previous session first
            try:
                self.client.load_store()
                if self.client.access_token and self.client.device_id and self.client.user_id:
                    logger.info(
                        f'Restored session from store - Token: {self.client.access_token[:20]}..., Device: {self.client.device_id}'
                    )
                    logger.info('Skipping login to avoid rate limiting')
                    return self.client
                else:
                    logger.info(
                        'Session load attempted but missing credentials - '
                        f'Token: {bool(self.client.access_token)}, '
                        f'Device: {bool(self.client.device_id)}, '
                        f'User: {bool(self.client.user_id)}'
                    )
                    self.client.access_token = ''
                    self.client.device_id = None
            except Exception as e:
                logger.warning(f'Could not restore session: {e}')
                self.client.access_token = ''
                self.client.device_id = None

            # Only attempt login if we don't have stored credentials
            if not (self.client.access_token and self.client.device_id and self.client.user_id):
                expected_user_id = self.user_id
                fallback_tokens = [
                    os.getenv('MATRIX_ACCESS_TOKEN'),
                    os.getenv('MATRIX_ADMIN_TOKEN'),
                ]
                for token_fallback in [token for token in fallback_tokens if token]:
                    try:
                        self.client.access_token = token_fallback
                        whoami = await self.client.whoami()
                        whoami_user = getattr(whoami, 'user_id', None)
                        if whoami_user == expected_user_id:
                            self.client.user_id = str(whoami_user)
                            logger.info(f'Authenticated via token fallback as {whoami_user}')
                            return self.client
                        logger.warning(
                            f'Token fallback belongs to {whoami_user}, expected {expected_user_id}; skipping token'
                        )
                    except Exception as token_error:
                        logger.warning(f'Token fallback auth failed: {token_error}')
                    self.client.access_token = ''

                logger.info('No valid stored session found, attempting login...')
                max_retries = int(os.getenv('MATRIX_LOGIN_MAX_RETRIES', '5'))
                base_delay = float(os.getenv('MATRIX_LOGIN_RETRY_DELAY', '2.0'))

                for attempt in range(1, max_retries + 1):
                    try:
                        response = await self.client.login(
                            password=self.password, device_name=self.device_name
                        )

                        if isinstance(response, LoginError):
                            logger.error(
                                f'Login failed (attempt {attempt}/{max_retries}): {response.message}'
                            )
                            if attempt < max_retries:
                                delay = base_delay * (2 ** (attempt - 1))
                                await asyncio.sleep(delay)
                                continue
                            await self.client.close()
                            return None

                        logger.info(
                            f'Login successful. User ID: {self.client.user_id}, Device ID: {self.client.device_id}'
                        )
                        break

                    except Exception as login_error:
                        logger.error(
                            f'Login attempt failed ({attempt}/{max_retries}): {login_error}'
                        )
                        if attempt < max_retries:
                            delay = base_delay * (2 ** (attempt - 1))
                            await asyncio.sleep(delay)
                            continue
                        await self.client.close()
                        return None

            return self.client

        except Exception as e:
            logger.error(f'Authentication failed: {e}')
            if self.client:
                await self.client.close()
            return None

    async def ensure_valid_token(self, client):
        """
        Ensures the client has a valid access token.
        Simplified version that assumes token is valid if client exists.
        """
        if not client or not client.access_token:
            logger.warning('No valid access token found')
            return False

        # If we have a client with an access token, assume it's valid
        # The nio library will handle token refresh automatically
        logger.debug('Token validation - client and access token present')
        return True

    async def logout(self):
        """
        Logs out and cleans up the session.
        """
        if self.client:
            try:
                response = await self.client.logout()
                if isinstance(response, LogoutError):
                    logger.error(f'Logout error: {response.message}')
                else:
                    logger.info('Logout successful')

                # Clean up store
                try:
                    store_file = f'{self.store_path}/{self.user_id}_{self.device_name}.db'
                    if os.path.exists(store_file):
                        os.remove(store_file)
                        logger.info('Session store cleaned up')
                except Exception as e:
                    logger.warning(f'Failed to clean up store: {e}')

            except Exception as e:
                logger.error(f'Logout failed: {e}')
            finally:
                await self.client.close()
                self.client = None
