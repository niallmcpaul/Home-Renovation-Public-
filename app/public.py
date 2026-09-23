from urllib.parse import urlparse

from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import ProtectedResourceMetadata
from starlette.applications import Starlette

from app import config, oauth
from app.mcp_server import build_server


def build_public_app() -> Starlette:
    auth = AuthSettings(
        issuer_url=config.PUBLIC_BASE_URL,
        resource_server_url=oauth.RESOURCE_URL,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
        validate_token_resource=True,
    )
    server = build_server(auth_server_provider=oauth.DBOAuthProvider(), auth=auth)
    server.custom_route(oauth.LOGIN_PATH, methods=["GET", "POST"], include_in_schema=False)(oauth.login_endpoint)
    prm = ProtectedResourceMetadataHandler(ProtectedResourceMetadata(
        resource=oauth.RESOURCE_URL, authorization_servers=[config.PUBLIC_BASE_URL]))
    server.custom_route("/.well-known/oauth-protected-resource", methods=["GET"], include_in_schema=False)(prm.handle)
    host = urlparse(config.PUBLIC_BASE_URL).netloc
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, "localhost:*", "127.0.0.1:*"],
        allowed_origins=[config.PUBLIC_BASE_URL, "https://claude.ai", "https://claude.com"],
    )
    return server.streamable_http_app(streamable_http_path="/mcp", transport_security=security)
