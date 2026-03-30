import os
from pathlib import Path
from nio import AsyncClient, LoginError, LogoutError
from nio.exceptions import LocalProtocolError
import logging

from src.core.retry import retry_async

logger = logging.getLogger(__name__)


class MatrixLoginRetryError(Exception):
    pass


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

        self.store_path = './matrix_store'
        self.session_store_enabled = _env_bool('MATRIX_SESSION_STORE_ENABLED', True)
        self.store_max_files_per_user = int(
            os.getenv('MATRIX_STORE_MAX_FILES_PER_USER', '25')
        )
        self.store_prune_enabled = _env_bool('MATRIX_STORE_PRUNE_ENABLED', True)

    def _prune_stale_store_files(self) -> None:
        if not self.store_prune_enabled:
            return
        try:
            store_dir = Path(self.store_path)
            if not store_dir.exists():
                return
            user_prefix = f'{self.user_id}_'
            db_files = sorted(
                [
                    p
                    for p in store_dir.glob('*.db')
                    if p.name.startswith(user_prefix)
                ],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            stale = db_files[self.store_max_files_per_user :]
            for db_file in stale:
                db_file.unlink(missing_ok=True)
            if stale:
                logger.info(
                    'Pruned %d stale Matrix store files for %s',
                    len(stale),
                    self.user_id,
                )
        except OSError as prune_error:
            logger.warning(f'Could not prune stale Matrix store files: {prune_error}')

    async def get_authenticated_client(self):
        """
        Returns an authenticated AsyncClient instance.
        Handles login and session persistence with rate limiting protection.
        """
        try:
            if self.session_store_enabled:
                os.makedirs(self.store_path, exist_ok=True)
                self._prune_stale_store_files()

            self.client = AsyncClient(
                homeserver=self.homeserver_url,
                user=self.user_id,
                store_path=self.store_path if self.session_store_enabled else None,
                config=None,
            )

            if self.session_store_enabled:
                try:
                    if not self.client.user_id:
                        self.client.user_id = str(self.user_id)
                    self.client.load_store()
                    if self.client.access_token and self.client.device_id and self.client.user_id:
                        logger.info(
                            f'Restored session from store - Token: {self.client.access_token[:20]}..., Device: {self.client.device_id}'
                        )
                        logger.info('Skipping login to avoid rate limiting')
                        return self.client
                    logger.info(
                        'Session load attempted but missing credentials - '
                        f'Token: {bool(self.client.access_token)}, '
                        f'Device: {bool(self.client.device_id)}, '
                        f'User: {bool(self.client.user_id)}'
                    )
                    self.client.access_token = ''
                    self.client.device_id = None
                except (LocalProtocolError, OSError, RuntimeError, ValueError, TypeError) as e:
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
                    except (RuntimeError, ValueError, TypeError, OSError) as token_error:
                        logger.warning(f'Token fallback auth failed: {token_error}')
                    self.client.access_token = ''

                logger.info('No valid stored session found, attempting login...')
                max_retries = int(os.getenv('MATRIX_LOGIN_MAX_RETRIES', '5'))
                base_delay = float(os.getenv('MATRIX_LOGIN_RETRY_DELAY', '2.0'))
                client = self.client
                if client is None:
                    return None

                async def _login_once() -> None:
                    response = await client.login(
                        password=self.password, device_name=self.device_name
                    )
                    if isinstance(response, LoginError):
                        raise MatrixLoginRetryError(response.message)

                try:
                    await retry_async(
                        _login_once,
                        operation_name='Matrix login',
                        max_attempts=max_retries,
                        base_delay=base_delay,
                        logger=logger,
                        retryable_exceptions=(MatrixLoginRetryError, Exception),
                    )
                except (MatrixLoginRetryError, RuntimeError, ValueError, TypeError, OSError) as login_error:
                    logger.error(f'Login failed after {max_retries} attempts: {login_error}')
                    await client.close()
                    return None

                logger.info(
                    f'Login successful. User ID: {self.client.user_id}, Device ID: {self.client.device_id}'
                )

            return self.client

        except (RuntimeError, ValueError, TypeError, OSError) as e:
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
                except OSError as e:
                    logger.warning(f'Failed to clean up store: {e}')

            except (RuntimeError, ValueError, TypeError, OSError) as e:
                logger.error(f'Logout failed: {e}')
            finally:
                await self.client.close()
                self.client = None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}
