from __future__ import annotations

from typing import Any


def clean(value: str) -> str:
    return (value or "").strip().strip('"').strip("'").strip()


_SSL_VERIFY: Any = None


def is_ssl_error(exc: BaseException) -> bool:
    cur: BaseException | None = exc
    for _ in range(8):
        if cur is None:
            break
        name = type(cur).__name__.lower()
        msg = str(cur).lower()
        if "ssl" in name or "ssl" in msg or "certificate_verify_failed" in msg:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def boto_ssl_verify() -> Any:
    global _SSL_VERIFY
    if _SSL_VERIFY is not None:
        return _SSL_VERIFY
    import os

    from app.config import get_settings

    settings = get_settings()
    explicit = getattr(settings, "aws_verify_ssl", True)
    if isinstance(explicit, str) and explicit.strip().lower() in {"0", "false", "no"}:
        _SSL_VERIFY = False
        return _SSL_VERIFY
    if explicit is False:
        _SSL_VERIFY = False
        return _SSL_VERIFY
    bundle = clean(getattr(settings, "aws_ca_bundle", "") or "") or (
        os.environ.get("AWS_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE") or ""
    ).strip()
    _SSL_VERIFY = bundle or True
    return _SSL_VERIFY


def set_boto_ssl_verify(value: Any) -> None:
    global _SSL_VERIFY
    _SSL_VERIFY = value
    if value is False:
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass


def boto3_client_kwargs(region: str = "", *, endpoint_url: str = "") -> dict[str, Any]:
    """IAM keys, env keys, or default chain (instance profile / shared config / AWS_PROFILE)."""
    import os

    from app.config import get_settings

    settings = get_settings()
    region = clean(region) or clean(settings.aws_region) or "us-east-1"
    access_key = clean(settings.aws_access_key_id)
    secret_key = clean(settings.aws_secret_access_key)
    session_token = clean(settings.aws_session_token)
    profile = clean(settings.aws_profile) or clean(os.environ.get("AWS_PROFILE") or "")

    kwargs: dict[str, Any] = {"region_name": region}
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
        if access_key.startswith("ASIA") and session_token:
            kwargs["aws_session_token"] = session_token
    elif profile:
        import boto3

        session = boto3.Session(profile_name=profile, region_name=region)
        creds = session.get_credentials()
        frozen = creds.get_frozen_credentials() if creds else None
        if frozen:
            kwargs["aws_access_key_id"] = frozen.access_key
            kwargs["aws_secret_access_key"] = frozen.secret_key
            if frozen.token:
                kwargs["aws_session_token"] = frozen.token

    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url

    verify = boto_ssl_verify()
    if verify is False or isinstance(verify, str):
        kwargs["verify"] = verify

    api_key = clean(getattr(settings, "bedrock_api_key", "") or "")
    if api_key and not (access_key and secret_key):
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key

    return kwargs


def build_client(service: str, *, region: str = ""):
    import boto3
    from botocore.config import Config

    kwargs = boto3_client_kwargs(region)
    verify = kwargs.pop("verify", boto_ssl_verify())
    kwargs["config"] = Config(
        retries={"max_attempts": 2, "mode": "standard"},
        connect_timeout=5,
        read_timeout=60,
    )
    return boto3.client(service, verify=verify, **kwargs), verify, kwargs
