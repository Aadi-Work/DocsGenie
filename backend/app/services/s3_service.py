from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional

from app.config import get_settings
from app.utils.aws import build_client, is_ssl_error, set_boto_ssl_verify
from app.utils.file_utils import AppError, content_type_for, normalize_prefix, validate_key

log = logging.getLogger(__name__)


class S3Service:
    def __init__(self) -> None:
        import boto3

        settings = get_settings()
        self.bucket = settings.bucket
        self.templates_prefix = normalize_prefix(settings.s3_templates_prefix or "template/")
        self.documents_prefix = normalize_prefix(settings.s3_documents_prefix or "documents/generated/")
        self.previews_prefix = normalize_prefix(settings.s3_previews_prefix or "documents/previews/")
        self.metadata_prefix = normalize_prefix(settings.s3_metadata_prefix or "metadata/template/")
        self.kb_prefix = normalize_prefix(settings.s3_kb_prefix or "KB/")
        self.client, self._verify, self._client_kwargs = build_client("s3", region=settings.aws_region)
        self._boto3 = boto3

    def _relax_ssl(self, exc: BaseException) -> bool:
        if self._verify is False or not is_ssl_error(exc):
            return False
        set_boto_ssl_verify(False)
        self._verify = False
        self.client = self._boto3.client("s3", verify=False, **self._client_kwargs)
        log.warning("S3 TLS verification failed; retrying without certificate verification")
        return True

    def invoke(self, operation: str, **kwargs: Any) -> Any:
        try:
            return getattr(self.client, operation)(**kwargs)
        except Exception as exc:
            if self._relax_ssl(exc):
                return getattr(self.client, operation)(**kwargs)
            raise

    def _map(self, exc: Exception, key: str = "") -> AppError:
        msg = str(exc)
        name = type(exc).__name__
        code = ""
        resp = getattr(exc, "response", None)
        if isinstance(resp, dict):
            code = str((resp.get("Error") or {}).get("Code") or "")
        if code in {"404", "NoSuchKey", "NotFound"} or "Not Found" in msg:
            return AppError(404, f"Object not found in S3: {key or 'unknown'}")
        if code in {"403", "AccessDenied"} or "AccessDenied" in name:
            return AppError(403, "Access denied while reading S3. Check IAM permissions.")
        if is_ssl_error(exc) or "timeout" in msg.lower() or "Endpoint" in name:
            return AppError(503, "Could not reach AWS S3")
        log.exception("S3 error for %s", key or operation_name(exc))
        return AppError(500, "S3 request failed")

    def health(self) -> dict[str, Any]:
        info = {"bucket": self.bucket, "kb_prefix": self.kb_prefix}
        try:
            self.invoke("head_bucket", Bucket=self.bucket)
            return {"ok": True, **info}
        except Exception as exc:
            if self._relax_ssl(exc):
                try:
                    self.invoke("head_bucket", Bucket=self.bucket)
                    return {"ok": True, **info}
                except Exception as retry_exc:
                    exc = retry_exc
            return {"ok": False, **info, "error": type(exc).__name__}

    def list_objects(self, prefix: str, *, limit: int = 500) -> list[dict[str, Any]]:
        prefix = normalize_prefix(prefix) if prefix.endswith("/") or prefix == "" else prefix.lstrip("/")
        out: list[dict[str, Any]] = []
        token: Optional[str] = None
        try:
            while len(out) < limit:
                kwargs: dict[str, Any] = {
                    "Bucket": self.bucket,
                    "Prefix": prefix,
                    "MaxKeys": min(1000, limit - len(out)),
                }
                if token:
                    kwargs["ContinuationToken"] = token
                resp = self.invoke("list_objects_v2", **kwargs)
                for obj in resp.get("Contents") or []:
                    key = obj["Key"]
                    if str(key).endswith("/"):
                        continue
                    lm = obj.get("LastModified")
                    out.append(
                        {
                            "key": key,
                            "name": str(key).rsplit("/", 1)[-1],
                            "size": int(obj.get("Size") or 0),
                            "last_modified": lm.isoformat() if lm else None,
                            "etag": (obj.get("ETag") or "").strip('"') or None,
                        }
                    )
                    if len(out) >= limit:
                        break
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
        except Exception as exc:
            raise self._map(exc, prefix) from exc
        return out

    def get_object(self, key: str) -> bytes:
        key = validate_key(key)
        try:
            resp = self.invoke("get_object", Bucket=self.bucket, Key=key)
            data = resp["Body"].read()
        except Exception as exc:
            raise self._map(exc, key) from exc
        if not data:
            raise AppError(400, "S3 object is empty")
        return data

    def get_object_with_name(self, key: str) -> tuple[str, bytes]:
        key = validate_key(key)
        return key.rsplit("/", 1)[-1], self.get_object(key)

    def upload_object(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        key = validate_key(key)
        if not data:
            raise AppError(400, "Cannot upload an empty file")
        extra: dict[str, Any] = {
            "ContentType": content_type or content_type_for(key),
        }
        if metadata:
            extra["Metadata"] = {str(k): str(v)[:1024] for k, v in metadata.items()}
        try:
            self.invoke("put_object", Bucket=self.bucket, Key=key, Body=data, **extra)
        except Exception as exc:
            raise self._map(exc, key) from exc
        return {
            "bucket": self.bucket,
            "key": key,
            "filename": key.rsplit("/", 1)[-1],
            "size": len(data),
            "s3_uri": f"s3://{self.bucket}/{key}",
            "content_type": extra["ContentType"],
        }

    def delete_object(self, key: str) -> None:
        key = validate_key(key)
        try:
            self.invoke("delete_object", Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise self._map(exc, key) from exc

    def object_exists(self, key: str) -> bool:
        key = validate_key(key)
        try:
            self.invoke("head_object", Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            mapped = self._map(exc, key)
            if mapped.status_code == 404:
                return False
            raise mapped from exc

    def get_object_metadata(self, key: str) -> dict[str, Any]:
        key = validate_key(key)
        try:
            resp = self.invoke("head_object", Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise self._map(exc, key) from exc
        lm = resp.get("LastModified")
        return {
            "key": key,
            "size": resp.get("ContentLength"),
            "content_type": resp.get("ContentType"),
            "last_modified": lm.isoformat() if lm else None,
            "etag": (resp.get("ETag") or "").strip('"'),
            "s3_uri": f"s3://{self.bucket}/{key}",
            "metadata": {str(k): str(v) for k, v in (resp.get("Metadata") or {}).items()},
        }

    def generate_presigned_url(self, key: str, expiration: int = 3600) -> str:
        key = validate_key(key)
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expiration,
            )
        except Exception as exc:
            raise self._map(exc, key) from exc

    def list_object_versions(self, key: str, *, limit: int = 20) -> list[dict[str, Any]]:
        key = validate_key(key)
        try:
            resp = self.invoke("list_object_versions", Bucket=self.bucket, Prefix=key, MaxKeys=limit)
        except Exception:
            return []
        versions = []
        for item in resp.get("Versions") or []:
            if item.get("Key") != key:
                continue
            lm = item.get("LastModified")
            versions.append(
                {
                    "version_id": item.get("VersionId"),
                    "is_latest": bool(item.get("IsLatest")),
                    "size": int(item.get("Size") or 0),
                    "last_modified": lm.isoformat() if lm else None,
                    "etag": (item.get("ETag") or "").strip('"'),
                }
            )
        return versions[:limit]


def operation_name(exc: Exception) -> str:
    return type(exc).__name__


@lru_cache
def get_s3() -> S3Service:
    return S3Service()
