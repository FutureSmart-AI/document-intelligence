from fastapi import HTTPException
import os
import uuid
import urllib.parse
import boto3
import requests
from botocore.exceptions import BotoCoreError, NoCredentialsError, ClientError
from botocore.config import Config

from services import celery_setup_env
from services.celery_logger import logger
from celery_utils.shared.tracing import track_time, traced_reader

AWS_ACCESS_KEY_ID = celery_setup_env.AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = celery_setup_env.AWS_SECRET_ACCESS_KEY
AWS_REGION = celery_setup_env.AWS_REGION
BUCKET_NAME = celery_setup_env.BUCKET_NAME
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL") or None

def get_s3_client():
    """Creates a synchronous S3 client session"""
    retry_count = 0
    max_retries = 3
    while retry_count < max_retries:
        try:
            session = boto3.Session()
            return session.client(
                's3',
                endpoint_url=AWS_ENDPOINT_URL,
                aws_access_key_id=AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
                region_name=AWS_REGION,
                config=Config(signature_version='s3v4', s3={'addressing_style': 'path' if AWS_ENDPOINT_URL else 'virtual'})
            )
        except Exception as e:
            logger.error(f"Error creating S3 client: {e}")
            retry_count += 1
            if retry_count == max_retries:
                raise Exception(f"Failed to create S3 client after {max_retries} attempts: {e}")
    raise Exception("Failed to create S3 client due to connection error")

@traced_reader("DB: Upload Extracted Text To S3", inputs=("file_name", "weblink", "object_id"))
def upload_extracted_text_to_s3(extracted_text, file_name=None, weblink=None, object_id=None):
    """
    Uploads extracted text to S3 with custom metadata.
    """
    logger.info(f"Uploading extracted text to S3 for object_id: {object_id}, file_name: {file_name}, weblink: {weblink}")
    name = (file_name or '').rsplit('.', 1)[0]
    if weblink:
        name = (object_id or '').rsplit('.', 1)[0]
    object_key = f"rag_demo_dev/extracted_text/{object_id}_{name}.md"
    
    s3 = get_s3_client()
    try:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=object_key,
            Body=extracted_text,  # String or bytes, no encoding needed here
            Metadata={'object_id': object_id, 'file_name': file_name or weblink},
            ContentType='text/markdown'
        )
        logger.info(f"Uploaded object '{object_key}'")
    except ClientError as e:
        logger.error(f"Error uploading extracted text: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        s3.close()

from pathlib import Path
import boto3
from urllib.parse import urlparse

def get_local_file_path(s3_url: str) -> Path:
    """Works on real AWS + LocalStack, returns Path to downloaded file"""
    is_localstack = bool(AWS_ENDPOINT_URL)
    
    s3 = boto3.client(
        "s3",
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
        region_name=AWS_REGION,
        config=boto3.session.Config(s3={"addressing_style": "path"}) if is_localstack else None
    )

    parsed = urlparse(s3_url)
    bucket = parsed.path.split("/", 2)[1]
    key = parsed.path.split("/", 2)[2].split("?", 1)[0] if len(parsed.path.split("/", 2)) > 2 else parsed.path.split("/", 1)[1].split("?", 1)[0]

    local_dir = Path("uploads")
    local_dir.mkdir(exist_ok=True)
    
    # Secure + unique filename
    suffix = Path(key).suffix or ".pdf"
    safe_name = f"{Path(key).stem}_{os.urandom(4).hex()}{suffix}"
    local_path = local_dir / safe_name
    
    s3.download_file(bucket, key, str(local_path))
    return local_path   # ← only one return value


def download_file_s3(s3_url: str) -> tuple[str, str, str]:
    """
    Downloads a file from an S3 URL and saves it to the uploads folder.

    Args:
        s3_url (str): The full S3 URL of the file (e.g., https://my-bucket.s3.us-east-1.amazonaws.com/myfile.txt).

    Returns:
        tuple: (local_file_path, file_extension, unique_filename)
    """
    try:
        # Parse the S3 URL
        parsed_url = urllib.parse.urlparse(s3_url)
        bucket_name = parsed_url.netloc.split(".")[0]  # Extracts bucket name
        object_key = parsed_url.path.lstrip("/")  # Extracts file key
        
        # Extract file name and extension from object_key
        file_name = os.path.splitext(os.path.basename(object_key))[0]
        file_extension = os.path.splitext(os.path.basename(object_key))[1]

        # Define the save path
        UPLOADS_FOLDER = "uploads"
        os.makedirs(UPLOADS_FOLDER, exist_ok=True)
        file_uuid = uuid.uuid4()
        unique_filename = f"{file_name}_{str(file_uuid)}{file_extension}"
        file_path = os.path.join(UPLOADS_FOLDER, unique_filename)

        # Download the full file directly to the local file
        s3_client = get_s3_client()
        try:
            with open(file_path, "wb") as f:
                s3_client.download_fileobj(bucket_name, object_key, f)
            print(f"File downloaded successfully: {file_path}")
            return file_path, file_extension, unique_filename
        finally:
            s3_client.close()

    
    except (BotoCoreError, NoCredentialsError) as e:
        print(f"AWS S3 error: {e}")
        return "", "", ""
    except Exception as e:
        print(f"Error downloading file from S3: {e}")
        return "", "", ""

def download_presigned_url(s3_url: str) -> tuple[str, str, str]:
    """
    Downloads a file from a GET presigned S3 URL using HTTP and saves it locally.
    
    Returns:
        tuple: (local_file_path, file_extension, unique_filename)
    """
    try:
        parsed_url = urllib.parse.urlparse(s3_url)
        file_name = os.path.splitext(os.path.basename(parsed_url.path))[0]
        file_extension = os.path.splitext(os.path.basename(parsed_url.path))[1]

        file_uuid = uuid.uuid4()
        unique_filename = f"{file_name}_{str(file_uuid)}{file_extension}"
        uploads_folder = "uploads"
        os.makedirs(uploads_folder, exist_ok=True)
        file_path = os.path.join(uploads_folder, unique_filename)

        response = requests.get(s3_url)
        if response.status_code == 200:
            with open(file_path, 'wb') as f:
                f.write(response.content)
            print(f"Downloaded file from presigned URL to: {file_path}")
            return file_path, file_extension, unique_filename
        else:
            print(f"Failed to download file. Status: {response.status_code}")
            print(f"Response message: {response.text}")
            return "", "", ""

    except Exception as e:
        print(f"Error downloading from presigned URL: {str(e)}")
        return "", "", ""

@traced_reader("DB: Download File From S3", inputs=("file_url",))
def download_file(file_url: str) -> tuple[str, str, str]:
    """
    Auto-detects the type of S3 URL and routes to the appropriate download method.

    Returns:
        tuple: (file_path, file_extension, unique_filename)
    """
    parsed_url = urllib.parse.urlparse(file_url)
    query_params = urllib.parse.parse_qs(parsed_url.query)

    # More robust check for common presigned URL params
    presigned_keys = {"X-Amz-Signature", "X-Amz-Credential", "X-Amz-Date", "AWSAccessKeyId", "Signature"}
    is_presigned = any(key in query_params for key in presigned_keys)

    if AWS_ENDPOINT_URL and file_url.startswith(AWS_ENDPOINT_URL.rstrip("/") + "/"):
        file_path = get_local_file_path(file_url)
        file_extension = os.path.splitext(file_path)[1]
        unique_filename = os.path.basename(file_path)
        logger.info(f"Downloaded file from local URL to: {file_path}, file_extension: {file_extension}, unique_filename: {unique_filename}")
        return str(file_path), file_extension, unique_filename
    
    if is_presigned:
        return download_presigned_url(file_url)
    else:
        return download_file_s3(file_url)
