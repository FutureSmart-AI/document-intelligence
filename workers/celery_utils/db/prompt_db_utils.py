from typing import Optional, List, Dict, Any
from sqlmodel import select, and_, desc
from celery_utils.db.db_utils import get_sync_session
from services.celery_logger import logger
from services.pydantic_models_db import UserPrompts, AdminPrompts




def get_user_prompts_sync(
    chatbot_id: str,
    prompt_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get user prompts with optional filtering (synchronous version)."""
    try:
        with get_sync_session() as session:
            conditions = [
                UserPrompts.chatbot_id == chatbot_id,
                UserPrompts.deleted == False
            ]
            
            if prompt_type is not None:
                conditions.append(UserPrompts.prompt_type == prompt_type)
                
            statement = select(UserPrompts).where(and_(*conditions)).order_by(desc(UserPrompts.created_at))
            result = session.execute(statement)
            prompts = result.scalars().all()
            
            if not prompts:
                return []
            
            return [
                {
                    "prompt_id": prompt.prompt_id,
                    "chatbot_id": prompt.chatbot_id,
                    "prompt_content": prompt.prompt_content,
                    "prompt_type": prompt.prompt_type,
                    "created_at": prompt.created_at.isoformat() if prompt.created_at else None,
                    "updated_at": prompt.updated_at.isoformat() if prompt.updated_at else None
                }
                for prompt in prompts
            ]
    except Exception as e:
        logger.error(f"Error getting user prompts (sync): {e}")
        raise
    
def get_admin_prompts_sync(
    prompt_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get admin prompts with optional filtering (synchronous version)."""
    try:
        with get_sync_session() as session:
            conditions = [AdminPrompts.deleted == False]
            
            if prompt_type is not None:
                conditions.append(AdminPrompts.prompt_type == prompt_type)
            
            statement = select(AdminPrompts).where(and_(*conditions)).order_by(desc(AdminPrompts.created_at))
            result = session.execute(statement)
            prompts = result.scalars().all()
            
            return [
                {
                    "prompt_id": prompt.prompt_id,
                    "prompt_content": prompt.prompt_content,
                    "prompt_type": prompt.prompt_type,
                    "created_at": prompt.created_at.isoformat() if prompt.created_at else None,
                    "updated_at": prompt.updated_at.isoformat() if prompt.updated_at else None
                }
                for prompt in prompts
            ]
    except Exception as e:
        logger.error(f"Error getting admin prompts (sync): {e}")
        raise