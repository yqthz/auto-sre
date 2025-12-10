import chromadb
from minio import Minio, S3Error

from app.core.config import settings
from app.core.logger import logger

minio_client = Minio(endpoint=settings.MINIO_ENDPOINT, access_key=settings.MINIO_ACCESS_KEY, secret_key=settings.MINIO_SECRET_KEY, secure=settings.MINIO_SECURE)
if not minio_client.bucket_exists(settings.MINIO_BUCKET):
    try:
        minio_client.make_bucket(settings.MINIO_BUCKET)
    except S3Error as e:
        logger.warning(f'Could not create bucket {settings.MINIO_BUCKET}: {e}')


client = chromadb.PersistentClient(path="./sre_knowledge_db")
