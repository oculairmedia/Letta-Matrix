import pytest
import socket
from unittest.mock import patch, Mock
from src.utils.ssrf_protection import SSRFError, build_pinned_connector, validate_url


class TestSSRFProtectionDirect:
    @patch('socket.getaddrinfo')
    def test_allows_public_http_url(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        ]
        url = "http://example.com/image.png"
        result = validate_url(url)
        assert result == url

    @patch('socket.getaddrinfo')
    def test_allows_public_https_url(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.35', 443))
        ]
        url = "https://cdn.example.com/file.pdf"
        result = validate_url(url)
        assert result == url

    def test_blocks_localhost(self):
        with pytest.raises(SSRFError):
            validate_url("http://localhost/secret")

    def test_blocks_127_0_0_1(self):
        with pytest.raises(SSRFError):
            validate_url("http://127.0.0.1/admin")

    def test_blocks_ipv6_loopback(self):
        with pytest.raises(SSRFError):
            validate_url("http://[::1]/admin")

    def test_blocks_10_x(self):
        with pytest.raises(SSRFError):
            validate_url("http://10.0.0.1/internal")

    def test_blocks_172_16_x(self):
        with pytest.raises(SSRFError):
            validate_url("http://172.16.0.1/internal")

    def test_blocks_192_168_x(self):
        with pytest.raises(SSRFError):
            validate_url("http://192.168.1.1/router")

    def test_blocks_169_254_metadata(self):
        with pytest.raises(SSRFError):
            validate_url("http://169.254.169.254/latest/meta-data")

    def test_blocks_metadata_google_internal(self):
        with pytest.raises(SSRFError):
            validate_url("http://metadata.google.internal/computeMetadata")

    def test_blocks_ftp_scheme(self):
        with pytest.raises(SSRFError):
            validate_url("ftp://example.com/file")

    def test_blocks_file_scheme(self):
        with pytest.raises(SSRFError):
            validate_url("file:///etc/passwd")

    def test_blocks_no_hostname(self):
        with pytest.raises(SSRFError):
            validate_url("http:///path")

    def test_blocks_0_0_0_0(self):
        with pytest.raises(SSRFError):
            validate_url("http://0.0.0.0/")

    @patch('socket.getaddrinfo')
    def test_blocks_dns_rebinding(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))
        ]
        with pytest.raises(SSRFError):
            validate_url("http://evil.com/")

    @patch('socket.getaddrinfo')
    def test_allows_dns_resolving_to_public_ip(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        ]
        url = "http://example.com/"
        result = validate_url(url)
        assert result == url

    @patch('socket.getaddrinfo')
    def test_blocks_dns_failure(self, mock_getaddrinfo):
        mock_getaddrinfo.side_effect = socket.gaierror("Name resolution failed")
        with pytest.raises(SSRFError):
            validate_url("http://invalid-domain-that-does-not-exist.test/")


class TestAgentMediaIntegration:
    @pytest.mark.asyncio
    async def test_fetch_and_send_image_blocks_ssrf(self):
        from src.matrix.agent_media import fetch_and_send_image
        
        mock_config = Mock()
        mock_config.homeserver_url = "http://test-tuwunel:6167"
        mock_logger = Mock()
        
        with patch('src.matrix.agent_media.build_pinned_connector') as mock_build:
            mock_build.side_effect = SSRFError("Blocked IP: 10.0.0.1")
            
            result = await fetch_and_send_image(
                room_id="!test:matrix.test",
                image_url="http://10.0.0.1/secret.png",
                alt="test",
                config=mock_config,
                logger=mock_logger
            )
            
            assert result is None
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_fetch_and_send_file_blocks_ssrf(self):
        from src.matrix.agent_media import fetch_and_send_file
        
        mock_config = Mock()
        mock_config.homeserver_url = "http://test-tuwunel:6167"
        mock_logger = Mock()
        
        with patch('src.matrix.agent_media.build_pinned_connector') as mock_build:
            mock_build.side_effect = SSRFError("Blocked IP: 10.0.0.1")
            
            result = await fetch_and_send_file(
                room_id="!test:matrix.test",
                file_url="http://10.0.0.1/secret.pdf",
                filename="secret.pdf",
                config=mock_config,
                logger=mock_logger
            )
            
            assert result is None
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_fetch_and_send_video_blocks_ssrf(self):
        from src.matrix.agent_media import fetch_and_send_video
        
        mock_config = Mock()
        mock_config.homeserver_url = "http://test-tuwunel:6167"
        mock_logger = Mock()
        
        with patch('src.matrix.agent_media.build_pinned_connector') as mock_build:
            mock_build.side_effect = SSRFError("Blocked IP: 10.0.0.1")
            
            result = await fetch_and_send_video(
                room_id="!test:matrix.test",
                video_url="http://10.0.0.1/secret.mp4",
                alt="test",
                config=mock_config,
                logger=mock_logger
            )
            
            assert result is None
            mock_logger.warning.assert_called()


class TestPinnedResolver:
    @pytest.mark.asyncio
    @patch('socket.getaddrinfo')
    async def test_build_pinned_connector_uses_resolved_public_ip(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        ]
        _, connector = build_pinned_connector("http://example.com/file.png")
        resolver = connector._resolver
        try:
            result = await resolver.resolve("example.com", 80)
        finally:
            await connector.close()

        assert result[0]["host"] == "93.184.216.34"
        mock_getaddrinfo.assert_called_once()

    @patch('socket.getaddrinfo')
    def test_build_pinned_connector_blocks_private_dns_target(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 80))
        ]
        with pytest.raises(SSRFError):
            build_pinned_connector("http://evil.example/file.png")
