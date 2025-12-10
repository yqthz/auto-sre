import os
from typing import Optional

from minio import Minio, S3Error

from app.core.client import minio_client
from app.core.config import settings
from app.core.logger import logger


class StorageManager:
    def __init__(self, client: Minio, bucket: str):
        self.client = client
        self.bucket = bucket

    def upload_file(self, user_id: str, kb_name: str, local_path: str, object_name: Optional[str] = None) -> str:
        # 如果没有指定 object_name , 自动生成一个路径 user_id/kb_name/file_name
        object_name = object_name or f"{user_id}/{kb_name}/{os.path.basename(local_path)}"
        try:
            # 上传到 MiniO
            self.client.fput_object(self.bucket, object_name, local_path)
        except S3Error as e:
            logger.error(f'MinIO upload failed for {object_name}: {e}')
            raise
        url = f"http://{settings.MINIO_ENDPOINT}/{self.bucket}/{object_name}"
        return url


storage = StorageManager(minio_client, settings.MINIO_BUCKET)