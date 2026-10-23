# from fastapi.security import APIKeyHeader, APIKeyQuery
# from fastapi import Depends, HTTPException, Header, status
# from starlette.status import HTTP_401_UNAUTHORIZED
# from fastapi import Security, HTTPException
# import os
# from services.celery_logger import logger
# from services.celery_setup_env import FASTAPI_SECRET_KEY
# from dotenv import load_dotenv
# load_dotenv(override=True)


# API_KEY_NAME = FASTAPI_SECRET_KEY


# api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


# async def get_api_key(
#     api_key: str = Security(api_key_header)
# ):

#     if api_key == API_KEY_NAME:
#         return True
#     else:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid API Key"
#         )