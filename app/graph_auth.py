"""
Shared certificate-based Microsoft Graph token acquisition (app-only /
client-credentials flow). Used by app/email/graph_sender.py today; the
storage backend (app/graph.py's upload_to_onedrive, still secret-based)
will move onto this too in a later pass.
"""
import msal

DEFAULT_SCOPES = ["https://graph.microsoft.com/.default"]


class GraphAuthError(Exception):
    pass


def get_token(
    tenant_id: str,
    client_id: str,
    cert_path: str,
    cert_thumbprint: str,
    scopes: list[str] | None = None,
) -> str:
    try:
        with open(cert_path) as f:
            private_key = f.read()
    except OSError as e:
        raise GraphAuthError(f"Could not read GRAPH_CERT_PATH ({cert_path}): {e}") from e

    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential={"private_key": private_key, "thumbprint": cert_thumbprint},
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    result = app.acquire_token_for_client(scopes=scopes or DEFAULT_SCOPES)
    if "access_token" not in result:
        raise GraphAuthError(f"Token acquisition failed: {result.get('error_description', result)}")
    return result["access_token"]
