import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import logging
import os
import time
from typing import BinaryIO, Optional

import aioboto3
import boto3
from botocore import config
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException
import httpx

from utils.logger import logger
from utils.tracing import track_time
from utils.setup_env import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_REGION,
    BUCKET_NAME,
    MODE,
)
from utils.common_utils import sanitize_filename

# Only use endpoint_url in LOCAL mode (for LocalStack)
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL") or None

# Maximum presigned URL expiration: 7 days (604800 seconds)
PRESIGNED_URL_EXPIRATION_SECONDS = 604800
# Refresh buffer: regenerate if less than 1 day remaining
PRESIGNED_URL_REFRESH_BUFFER_SECONDS = 86400


async def get_s3_client():
    """Create and return an asynchronous S3 client session."""
    retry_count = 0
    max_retries = 3
    while retry_count < max_retries:
        try:
            session = aioboto3.Session()
            client_kwargs = {
                'aws_access_key_id': AWS_ACCESS_KEY_ID or None,
                'aws_secret_access_key': AWS_SECRET_ACCESS_KEY or None,
                'region_name': AWS_REGION,
                'config': Config(signature_version='s3v4', s3={'addressing_style': 'virtual'}),
            }
            if AWS_ENDPOINT_URL:
                # LocalStack requires path-style
                client_kwargs['config'] = Config(signature_version='s3v4', s3={'addressing_style': 'path'})
                client_kwargs['endpoint_url'] = AWS_ENDPOINT_URL
            return session.client('s3', **client_kwargs)
        except aioboto3.exceptions.ConnectionError as e:
            logger.error(f"Connection error while creating S3 client: {e}")
            break
        except Exception as e:
            logger.error(f"Error creating S3 client: {e}")
            retry_count += 1


async def read_file_bytes(file_url: str) -> bytes:
    """Read this installation's private S3 objects using workload credentials."""
    from urllib.parse import urlparse, unquote
    parsed = urlparse(file_url)
    owned_hosts = {f"{BUCKET_NAME}.s3.amazonaws.com", f"{BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com"}
    if parsed.scheme == "https" and parsed.hostname in owned_hosts:
        async with await get_s3_client() as client:
            response = await client.get_object(Bucket=BUCKET_NAME, Key=unquote(parsed.path.lstrip('/')))
            async with response['Body'] as body:
                return await body.read()
    async with httpx.AsyncClient() as client:
        async with client.stream('GET', file_url, follow_redirects=True) as response:
            response.raise_for_status()
            return await response.aread()


@track_time("API: S3 - Upload Extracted Text")
async def upload_extracted_text_to_s3(extracted_text: str, knowledge_id: str, file_name: str | None = None, weblink: str | None = None):
    """Upload extracted markdown text to S3 with metadata."""
    logger.info(f"Uploading extracted text to S3 for knowledge_id: {knowledge_id}, file_name: {file_name}, weblink: {weblink}")
    name = (file_name or '').rsplit('.', 1)[0]
    if weblink:
        name = (knowledge_id or '').rsplit('.', 1)[0]
    object_key = f"rag_demo_dev/extracted_text/{knowledge_id}_{name}.md"
    async with await get_s3_client() as s3:
        try:
            await s3.put_object(
                Bucket=BUCKET_NAME,
                Key=object_key,
                Body=extracted_text,
                Metadata={'knowledge_id': knowledge_id, 'file_name': file_name or weblink},
                ContentType='text/markdown',
            )
            logging.info(f"Uploaded object '{object_key}'")
        except ClientError as e:
            logging.error(f"Error uploading extracted text: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@track_time("API: S3 - Upload File")
async def upload_file_to_s3(knowledge_id: str, file_name: str, content_type: str, file_obj: BinaryIO) -> str:
    """Upload a user file to S3 using boto3 and return its public URL."""
    logger.info(f"Uploading user file to S3 for knowledge_id: {knowledge_id}, file_name: {file_name}")
    object_key = f"rag_demo_dev/user_uploads/{knowledge_id}_{file_name}"

    if not BUCKET_NAME or not AWS_REGION:
        logger.error(f"BUCKET_NAME or AWS_REGION is None! BUCKET_NAME={BUCKET_NAME}, AWS_REGION={AWS_REGION}")
        raise ValueError("S3 configuration is missing")
    try:
        def upload():
            s3_client = boto3.client("s3", endpoint_url=AWS_ENDPOINT_URL, region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID or None, aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None, config=Config(signature_version="s3v4", s3={"addressing_style": "path" if AWS_ENDPOINT_URL else "virtual"}))
            s3_client.upload_fileobj(
                file_obj,
                BUCKET_NAME,
                object_key,
                ExtraArgs={'ContentType': content_type},
            )

        await asyncio.get_event_loop().run_in_executor(None, upload)

        file_url = f"{AWS_ENDPOINT_URL}/{BUCKET_NAME}/{object_key}" if AWS_ENDPOINT_URL else f"https://{BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{object_key}"
        return file_url
    except Exception as e:
        logger.error(f"BUCKET_NAME= {BUCKET_NAME}, File: {file_obj}")
        logger.error(f"Error uploading file {file_name} to S3: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def extract_s3_key_from_url(s3_url: str) -> str | None:
    """Extract the S3 object key from a full S3 URL.

    Handles both virtual-hosted and path-style URLs:
    - https://{bucket}.s3.{region}.amazonaws.com/{key}
    - http://localhost:4566/{bucket}/{key}  (LocalStack path-style)
    """
    if not s3_url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(s3_url)
        path = parsed.path.lstrip('/')
        # For path-style URLs (e.g. LocalStack), the bucket name is in the path
        if path.startswith(f"{BUCKET_NAME}/"):
            path = path[len(BUCKET_NAME) + 1:]
        return path
    except Exception:
        return None


@track_time("API: S3 - Generate Presigned Url")
async def generate_presigned_url(document_items: list[dict], folder: str = "extracted_text", expiration: int = 3600) -> list[dict]:
    """Attach presigned S3 URLs to knowledge items and their sub-urls if present (v1 - backward compatible)."""
    logger.info(f"Generating presigned URL for document items")
    async with await get_s3_client() as s3:
        for document in document_items:
            name = (document['file_name'] or '').rsplit('.', 1)[0]
            object_key = f"rag_demo_dev/{folder}/{document['object_id']}_{name}.md"
            try:
                url = await s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': BUCKET_NAME, 'Key': object_key},
                    ExpiresIn=expiration,
                )
                document['s3_url'] = url
            except ClientError as e:
                logging.error(f"Error generating presigned URL for main URL: {e}")
            except Exception as e:
                logging.error(f"Unexpected error for main URL: {e}")

            if 'sub_urls' in document and document['sub_urls']:
                for sub_url in document['sub_urls']:
                    sub_name = (sub_url['object_id'] or '').rsplit('.', 1)[0]
                    sub_object_key = f"rag_demo_dev/{folder}/{sub_url['object_id']}_{sub_name}.md"
                    try:
                        sub_url_presigned = await s3.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': BUCKET_NAME, 'Key': sub_object_key},
                            ExpiresIn=expiration,
                        )
                        sub_url['s3_url'] = sub_url_presigned
                    except ClientError as e:
                        logging.error(f"Error generating presigned URL for sub URL: {e}")
                    except Exception as e:
                        logging.error(f"Unexpected error for sub URL: {e}")
    return document_items


@track_time("API: S3 - Generate Source Presigned Url")
async def generate_source_presigned_url(source_url: str) -> tuple[str, datetime]:
    """Generate a presigned URL for a source file with 7-day expiration.

    Returns:
        tuple: (presigned_url, expires_at_datetime)
    """
    source_key = extract_s3_key_from_url(source_url)
    if not source_key:
        raise ValueError(f"Could not extract S3 key from URL: {source_url}")

    async with await get_s3_client() as s3:
        presigned_url = await s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': source_key},
            ExpiresIn=PRESIGNED_URL_EXPIRATION_SECONDS,
        )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_EXPIRATION_SECONDS)
    return presigned_url, expires_at


def is_presigned_url_valid(expires_at: Optional[datetime], url: Optional[str] = None) -> bool:
    """Check if presigned URL is still valid with buffer time."""
    if not expires_at:
        return False
    if url and 'localhost' in url:
        return False
    buffer_time = datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_REFRESH_BUFFER_SECONDS)
    return expires_at > buffer_time


@track_time("API: S3 - Generate Presigned Url V2")
async def generate_presigned_url_v2(document_items: list[dict], folder: str = "extracted_text") -> list[dict]:
    """Attach presigned S3 URLs to knowledge items with 7-day expiration and DB caching (v2).

    This version:
    - Uses 7-day expiration for source_presigned_url
    - Checks if cached presigned URL is still valid before regenerating
    - Returns extracted_s3_url instead of s3_url
    - Processes documents concurrently via asyncio.gather (single S3 client)
    - Batches DB updates into a single transaction at the end
    """
    start_time = time.time()
    logger.info(f"[presigned_v2] Starting presigned URL generation for {len(document_items)} documents")

    # Import here to avoid circular imports
    from utils.db.knowledge_ops import batch_update_document_presigned_urls

    # Collect all DB updates to batch them in a single transaction
    db_updates = []
    cache_hits = 0
    cache_misses = 0

    async with await get_s3_client() as s3:
        s3_client_time = time.time()
        logger.info(f"[presigned_v2] S3 client created in {s3_client_time - start_time:.3f}s")

        async def _process_document(document: dict) -> None:
            nonlocal cache_hits, cache_misses
            doc_start = time.time()
            object_id = document['object_id']

            # --- 1. Generate extracted_s3_url for the extracted markdown text ---
            name = (document['file_name'] or '').rsplit('.', 1)[0]
            object_key = f"rag_demo_dev/{folder}/{object_id}_{name}.md"
            try:
                url = await s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': BUCKET_NAME, 'Key': object_key},
                    ExpiresIn=PRESIGNED_URL_EXPIRATION_SECONDS,
                )
                document['extracted_s3_url'] = url
            except ClientError as e:
                logger.error(f"[presigned_v2] Failed to generate extracted_s3_url for {object_id}: {e}")
            except Exception as e:
                logger.error(f"[presigned_v2] Unexpected error generating extracted_s3_url for {object_id}: {e}")

            # --- 2. Handle source_presigned_url (original file) with DB caching ---
            source_url = document.get('source_url')
            cached_presigned = document.get('source_presigned_url')
            cached_expires = document.get('source_presigned_url_expires_at')
            doc_file_name = document.get('file_name')
            content_type_val = document.get('content_type')

            # Can generate source URL if we have source_url in DB, or it's a file upload with a file_name
            can_generate = bool(source_url) or (content_type_val == 'file' and doc_file_name)

            if can_generate:
                if cached_presigned and is_presigned_url_valid(cached_expires, cached_presigned):
                    # Cache hit: reuse existing presigned URL (still valid with buffer)
                    document['source_presigned_url'] = cached_presigned
                    cache_hits += 1
                    logger.info(f"[presigned_v2] Cache hit for source URL of {object_id}")
                else:
                    # Cache miss: generate a new presigned URL
                    cache_misses += 1
                    try:
                        # Determine S3 key: extract from source_url or construct from object_id + file_name
                        if source_url:
                            source_key = extract_s3_key_from_url(source_url)
                            if not source_key:
                                raise ValueError(f"Could not extract S3 key from URL: {source_url}")
                        else:
                            # Fallback for docs without source_url: construct key from object_id + sanitized file_name
                            sanitized_name = sanitize_filename(doc_file_name)
                            source_key = f"rag_demo_dev/user_uploads/{object_id}_{sanitized_name}"

                        new_presigned = await s3.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': BUCKET_NAME, 'Key': source_key},
                            ExpiresIn=PRESIGNED_URL_EXPIRATION_SECONDS,
                        )
                        new_expires = datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_EXPIRATION_SECONDS)

                        document['source_presigned_url'] = new_presigned

                        # Queue for batch DB update
                        db_updates.append({
                            'knowledge_id': document.get('knowledge_id'),
                            'object_id': object_id,
                            'presigned_url': new_presigned,
                            'expires_at': new_expires,
                        })
                        logger.info(f"[presigned_v2] Generated new source presigned URL for {object_id}")
                    except Exception as e:
                        logger.error(f"[presigned_v2] Failed to generate source presigned URL for {object_id}: {e}")

            # Clean up internal fields not needed by UI
            document.pop('source_url', None)
            document.pop('source_presigned_url_expires_at', None)

            # --- 3. Handle sub_urls (extracted text for child documents) ---
            if 'sub_urls' in document and document['sub_urls']:
                for sub_url in document['sub_urls']:
                    sub_name = (sub_url['object_id'] or '').rsplit('.', 1)[0]
                    sub_object_key = f"rag_demo_dev/{folder}/{sub_url['object_id']}_{sub_name}.md"
                    try:
                        sub_url_presigned = await s3.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': BUCKET_NAME, 'Key': sub_object_key},
                            ExpiresIn=PRESIGNED_URL_EXPIRATION_SECONDS,
                        )
                        sub_url['extracted_s3_url'] = sub_url_presigned
                    except ClientError as e:
                        logger.error(f"[presigned_v2] Failed to generate sub_url presigned URL for {sub_url['object_id']}: {e}")
                    except Exception as e:
                        logger.error(f"[presigned_v2] Unexpected error for sub_url {sub_url['object_id']}: {e}")

            logger.info(f"[presigned_v2] Processed document {object_id} in {time.time() - doc_start:.3f}s")

        # Process all documents concurrently using a single shared S3 client
        await asyncio.gather(*[_process_document(doc) for doc in document_items])

    s3_done_time = time.time()
    logger.info(f"[presigned_v2] All S3 presigned URLs generated in {s3_done_time - start_time:.3f}s "
                f"(cache_hits={cache_hits}, cache_misses={cache_misses})")

    # Batch update all new presigned URLs in a single DB transaction
    if db_updates:
        db_start = time.time()
        await batch_update_document_presigned_urls(db_updates)
        logger.info(f"[presigned_v2] Batch DB update for {len(db_updates)} documents in {time.time() - db_start:.3f}s")

    logger.info(f"[presigned_v2] Total time: {time.time() - start_time:.3f}s for {len(document_items)} documents")
    return document_items


@track_time("API: S3 - Generate Presigned Urls")
async def generate_s3_presigned_urls(object_key: str, content_type: str) -> tuple[str, str]:
    """Generate presigned URLs for PUT and GET on an S3 object."""
    try:
        async with await get_s3_client() as s3_client:
            upload_presigned_url = await s3_client.generate_presigned_url(
                "put_object",
                Params={"Bucket": BUCKET_NAME, "Key": object_key, "ContentType": content_type},
                ExpiresIn=300,
            )
            # The download URL is stored as the document's source_url and consumed by the
            # async indexing worker, which can lag well over an hour behind a large crawl
            # (e.g. a ClickUp sync of hundreds of pages). A 1-hour TTL let the URL expire
            # before the worker reached the file, surfacing as a misleading
            # "Unsupported file type" after a 403. Match the 7-day source-URL convention.
            download_presigned_url = await s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": BUCKET_NAME, "Key": object_key},
                ExpiresIn=PRESIGNED_URL_EXPIRATION_SECONDS,
            )
        logger.info(f"Generated presigned URLs for object_key: {object_key}")
        return upload_presigned_url, download_presigned_url
    except Exception as e:
        logger.error(f"Error generating presigned URLs: {e}")
        raise


async def upload_file_to_s3_storage(knowledge_id: str, filename: str, content_type: str, file_stream: BinaryIO) -> str:
    """High-level helper to upload a file stream to S3 and return its URL."""
    try:
        file_url = await upload_file_to_s3(knowledge_id, filename, content_type, file_stream)
        logger.info(f"File uploaded to S3: {file_url}")
        return file_url
    except Exception as e:
        logger.error(f"Error uploading file to S3: {e}")
        # raise


async def enhance_knowledge_with_presigned_urls(document_items: list) -> list:
    """Enhance knowledge items with presigned URLs for S3 files."""
    try:
        enhanced_items = await generate_presigned_url(document_items)
        logger.info(f"Enhanced {len(enhanced_items)} document items with presigned URLs")
        return enhanced_items
    except Exception as e:
        logger.error(f"Error enhancing document items with presigned URLs: {e}")
        # return knowledge_items
        raise


async def build_s3_key(user_id: str, chatbot_id: str, original_filename: str, knowledge_id: str) -> str:
    sanitized = sanitize_filename(original_filename)
    return f"{user_id}/{chatbot_id}/{knowledge_id}_{sanitized}"


def read_md_from_s3_presigned(url: str) -> str:
    with httpx.Client() as client:
        r = client.get(url)
        r.raise_for_status()
        return BytesIO(r.content).getvalue().decode("utf-8")

