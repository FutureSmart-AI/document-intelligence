"""
Knowledge deletion operations and status management.
"""

from datetime import datetime
from typing import Optional, List
from fastapi import HTTPException
from sqlmodel import select, and_

from utils.logger import logger
from utils.models.enums import DeletionStatus
from utils.pydantic_models_knowledge import (
    Document,
    # ExtractedMetadata,
)
from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.tracing import track_time

@retry_db_connection(max_retries=3)
@track_time("DB: Retrieve Knowledge Deletion Status")
async def retrieve_knowledge_deletion_status(knowledge_id: str, object_id: str, user_id: Optional[str]=None):
    """Retrieves the deletion status of a knowledge item."""
    try:
        async with async_session() as session:
            # First try to get status for non-deleted document
            statement = select(Document.deletion_status).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.object_id == object_id,
                    Document.deleted == False
                )
            )
            # if user_id:
            #     statement = statement.where(Document.user_id == user_id)
            result = await session.execute(statement)
            status = result.scalar_one_or_none()

            # If no status found and not deleted, try to get final status including deleted documents
            if status is None:
                statement = select(Document.deletion_status, Document.deleted).where(
                    and_(
                        Document.knowledge_id == knowledge_id,
                        Document.object_id == object_id
                    )
                )
                # if user_id:
                #     statement = statement.where(Document.user_id == user_id)
                result = await session.execute(statement)
                row = result.first()

                if row and row.deleted:
                    status = DeletionStatus.COMPLETED
                elif row:
                    status = row.deletion_status

        logger.info(f"Found deletion status: {status}" if status else "No deletion status found")
        return status
    except Exception as e:
        logger.error(f"Error retrieving deletion status: {e}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Set Initial Deletion Status")
async def set_initial_deletion_status(knowledge_id: str, object_ids: List[str], user_id: Optional[str] = None) -> List[dict]:
    """Sets the initial deletion status for documents and returns their status information."""
    try:
        processing_status = []
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.object_id.in_(object_ids)
                )
            )
            # if user_id:
            #     statement = statement.where(Document.user_id == user_id)
            result = await session.execute(statement)
            documents = result.scalars().all()
            
            for document in documents:
                document.processing_status = "deleting"
                document.deletion_status = DeletionStatus.DELETING
                processing_status.append({
                    "object_id": document.object_id,
                    "file_name": document.file_name,
                    "processing_status": "deleting"
                })
            await session.commit()
            
        logger.info(f"Set initial deletion status for {len(processing_status)} documents")
        return processing_status
    except Exception as e:
        logger.error(f"Database error setting initial deletion status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error setting initial deletion status: {str(e)}")


@retry_db_connection(max_retries=3)
@track_time("DB: Delete Chatbot Knowledge")
async def delete_chatbot_knowledge(knowledge_id: str, object_ids: Optional[List[str]] = None, user_id: str=None):
    try:
        async with async_session() as session:
            if object_ids:
                # Delete specific document IDs
                statement = select(Document).where(
                    and_(
                        Document.knowledge_id == knowledge_id,
                        Document.object_id.in_(object_ids)
                    )
                )
                # if user_id:
                #     statement = statement.where(Document.user_id == user_id)
                result = await session.execute(statement)
                documents = result.scalars().all()

                if not documents:
                    logger.info(f"No matching records found for deletion. User ID: {user_id}, Knowledge ID: {knowledge_id}, Object IDs: {object_ids}")
                    return False

                for document in documents:
                    document.deleted = True
                    document.updated_at = datetime.now()
                    document.user_id = user_id
                    document.processing_status = "deleted"
                    document.deletion_status = DeletionStatus.COMPLETED

                await session.commit()
                logger.info(f"Deleted {len(documents)} knowledge items for user ID: {user_id} and knowledge ID: {knowledge_id}")
                return True
            else:
                # Get all document IDs before deleting
                fetch_statement = select(Document).where(
                    and_(
                        Document.knowledge_id == knowledge_id,
                        Document.deleted == False
                    )
                )
                    # if user_id:
                    #     fetch_statement = fetch_statement.where(Document.user_id == user_id)
                fetch_result = await session.execute(fetch_statement)
                documents = fetch_result.scalars().all()

                if not documents:
                    logger.info(f"No knowledge items found to delete for User ID: {user_id}, Knowledge ID: {knowledge_id}")
                    return []

                deleted_object_ids = [doc.object_id for doc in documents]

                # Delete all documents
                for document in documents:
                    document.deleted = True
                    document.updated_at = datetime.now()
                    document.user_id = user_id
                    document.processing_status = "deleted"
                    document.deletion_status = DeletionStatus.COMPLETED

                await session.commit()
                logger.info(f"Deleted all knowledge for user ID: {user_id} and knowledge ID: {knowledge_id}")
                return deleted_object_ids
    except Exception as e:
        logger.error(f"Database error occurred during delete: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error occurred during delete: {str(e)}")

# Not Used
@retry_db_connection(max_retries=3)
@track_time("DB: Set Knowledge Deletion Complete")
async def set_knowledge_deletion_complete(user_id: str, knowledge_id: str, object_ids: List[str]) -> bool:
    """Sets the deletion status to complete and marks documents as deleted."""
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    # Document.user_id == user_id,
                    Document.knowledge_id == knowledge_id,
                    Document.object_id.in_(object_ids)
                )
            )
            result = await session.execute(statement)
            documents = result.scalars().all()

            if not documents:
                logger.warning(f"No documents found to mark as deleted for user_id={user_id}, knowledge_id={knowledge_id}")
                return False

            for document in documents:
                document.deletion_status = "complete"
                document.deleted = True
                document.processing_status = "deleted"
            await session.commit()

            logger.info(f"Successfully marked {len(documents)} documents as deleted for user_id={user_id}, knowledge_id={knowledge_id}")
            return True
    except Exception as e:
        logger.error(f"Database error setting deletion complete status: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Delete Documents Index")
async def delete_documents_index(knowledge_id: str, object_ids: Optional[List[str]] = None, user_id: str=None):
    try:
        async with async_session() as session:
            # REMOVED: extracted_metadata SQL deletes — table being phased out.
            # Metadata cleanup on document deletion is now covered by Qdrant point removal.
            # if object_ids:
            #     statement = select(ExtractedMetadata).where(
            #         and_(ExtractedMetadata.knowledge_id == knowledge_id,
            #              ExtractedMetadata.object_id.in_(object_ids))
            #     )
            #     result = await session.execute(statement)
            #     extracted_metadata = result.scalars().all()
            #     if not extracted_metadata:
            #         logger.info(f"No extracted metadata found for deletion. Knowledge ID: {knowledge_id}, Object IDs: {object_ids}")
            #         return False
            #     for metadata in extracted_metadata:
            #         await session.delete(metadata)
            #     await session.commit()
            #     logger.info(f"Deleted {len(extracted_metadata)} extracted metadata entries for knowledge ID: {knowledge_id}")
            #     return True
            # else:
            #     statement = select(ExtractedMetadata).where(
            #         ExtractedMetadata.knowledge_id == knowledge_id
            #     )
            #     result = await session.execute(statement)
            #     extracted_metadata = result.scalars().all()
            #     if not extracted_metadata:
            #         logger.info(f"No extracted metadata found for deletion. Knowledge ID: {knowledge_id}")
            #         return False
            #     for metadata in extracted_metadata:
            #         await session.delete(metadata)
            #     await session.commit()
            #     logger.info(f"Deleted all extracted metadata entries for knowledge ID: {knowledge_id}")
            #     return True
            return True
    except Exception as e:
        logger.error(f"Database error occurred during extracted metadata deletion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error occurred during extracted metadata deletion: {str(e)}")


@retry_db_connection(max_retries=3)
@track_time("DB: Set Knowledge Deletion Error")
async def set_knowledge_deletion_error(user_id: str, knowledge_id: str, object_ids: List[str]) -> bool:
    """Sets the deletion status to error when deletion fails."""
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    # Document.user_id == user_id,
                    Document.knowledge_id == knowledge_id,
                    Document.object_id.in_(object_ids)
                )
            )
            result = await session.execute(statement)
            documents = result.scalars().all()

            if not documents:
                logger.warning(f"No documents found to mark as error for user_id={user_id}, knowledge_id={knowledge_id}")
                return False

            for document in documents:
                document.deletion_status = DeletionStatus.ERROR
            await session.commit()

            logger.info(f"Marked {len(documents)} documents as error for user_id={user_id}, knowledge_id={knowledge_id}")
            return True
    except Exception as e:
        logger.error(f"Database error setting deletion error status: {str(e)}")
        raise


