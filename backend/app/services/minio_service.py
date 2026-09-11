import os
import io
from minio import Minio
from minio.error import S3Error

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
BUCKET_NAME = "pcf-raw-documents"

class MinIOService:
    def __init__(self):
        self.client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=False
        )
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        try:
            if not self.client.bucket_exists(BUCKET_NAME):
                self.client.make_bucket(BUCKET_NAME)
        except S3Error as e:
            print(f"[MinIO Error]: Failed to initialize bucket: {e}")

    def upload_file(self, object_name: str, file_data: bytes, content_type: str = "application/pdf") -> str:
        """Uploads raw file stream to MinIO bucket and returns the object key."""
        file_stream = io.BytesIO(file_data)
        self.client.put_object(
            bucket_name=BUCKET_NAME,
            object_name=object_name,
            data=file_stream,
            length=len(file_data),
            content_type=content_type
        )
        return f"{BUCKET_NAME}/{object_name}"

minio_service = MinIOService()