import asyncio
import sys
from pathlib import Path
from datetime import datetime

from sqlmodel import select, and_

# Ensure project root is importable when running directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Try to use app's env setup; fallback to dotenv/env directly if unavailable
try:
    from utils.setup_env import (
        POSTGRES_USER,
        POSTGRES_PASSWORD,
        POSTGRES_HOST,
        POSTGRES_PORT,
        POSTGRES_DB_NAME,
    )
except Exception:
    import os
    try:
        from dotenv import load_dotenv  # type: ignore
        mode = os.getenv("MODE", "LOCAL").upper()
        env_file = ".env.prod" if mode == "PROD" else (".env.dev" if mode == "DEV" else ".env.local")
        load_dotenv(Path(ROOT, env_file), override=True)
    except Exception:
        pass
    POSTGRES_USER = os.getenv("POSTGRES_USER")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
    POSTGRES_HOST = os.getenv("POSTGRES_HOST")
    POSTGRES_PORT = os.getenv("POSTGRES_PORT")
    POSTGRES_DB_NAME = os.getenv("POSTGRES_DB_NAME")

from utils.db.session import async_session
from utils.pydantic_models_enrichment import FieldConfiguration
from utils.pydantic_models_knowledge import KnowledgeConfig

# chunk_idx field configuration
CHUNK_IDX_CONFIG = {
    "field_name": "chunk_idx",
    "description": "The order number of the chunk in the document. Start from 0.",
    "field_type": "int",
    "is_active": True,
    "is_custom": False,
    "is_required": True,
    "sample_values": None,
    "restrict_to_choices": False,
    "max_items": None,
    "level": "chunk"
}


async def upgrade() -> None:
    """Add chunk_idx field configuration to all existing knowledge IDs."""
    created_count = 0
    restored_count = 0
    skipped_count = 0
    error_count = 0
    
    print("Starting migration: Add chunk_idx field configuration to all knowledge IDs...")
    
    async with async_session() as session:
        # Get all non-deleted knowledge IDs
        statement = select(KnowledgeConfig.knowledge_id).where(
            KnowledgeConfig.deleted == False
        )
        result = await session.execute(statement)
        knowledge_ids = list(result.scalars().all())
        
        total_count = len(knowledge_ids)
        print(f"Found {total_count} knowledge IDs to process")
        
        for idx, knowledge_id in enumerate(knowledge_ids, 1):
            try:
                # Check if chunk_idx field configuration already exists (including deleted ones)
                field_statement = select(FieldConfiguration).where(
                    and_(
                        FieldConfiguration.knowledge_id == knowledge_id,
                        FieldConfiguration.field_name == "chunk_idx"
                    )
                )
                field_result = await session.execute(field_statement)
                existing_field = field_result.scalars().first()
                
                if not existing_field:
                    # Create new field configuration
                    field_config = FieldConfiguration(
                        knowledge_id=knowledge_id,
                        **CHUNK_IDX_CONFIG
                    )
                    session.add(field_config)
                    created_count += 1
                    if idx % 10 == 0:
                        print(f"  Progress: {idx}/{total_count} - Created chunk_idx for {knowledge_id}")
                elif existing_field.deleted:
                    # Restore deleted field configuration
                    existing_field.deleted = False
                    existing_field.deleted_at = None
                    existing_field.updated_at = datetime.utcnow()
                    # Update other fields to match default config
                    for key, value in CHUNK_IDX_CONFIG.items():
                        setattr(existing_field, key, value)
                    session.add(existing_field)
                    restored_count += 1
                    if idx % 10 == 0:
                        print(f"  Progress: {idx}/{total_count} - Restored chunk_idx for {knowledge_id}")
                else:
                    # Field already exists and is active, skip it
                    skipped_count += 1
                    if idx % 10 == 0:
                        print(f"  Progress: {idx}/{total_count} - Skipped {knowledge_id} (already exists)")
                
                # Commit in batches to avoid long transactions
                if idx % 50 == 0:
                    await session.commit()
                    print(f"  Committed batch at {idx}/{total_count}")
                    
            except Exception as e:
                error_count += 1
                print(f"  Error processing knowledge_id {knowledge_id}: {str(e)}")
                await session.rollback()
                continue
        
        # Final commit for remaining changes
        await session.commit()
    
    print("\nMigration summary:")
    print(f"  Total knowledge IDs processed: {total_count}")
    print(f"  Created: {created_count}")
    print(f"  Restored: {restored_count}")
    print(f"  Skipped (already exists): {skipped_count}")
    print(f"  Errors: {error_count}")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Soft delete chunk_idx field configurations from all knowledge IDs."""
    deleted_count = 0
    error_count = 0
    
    print("Starting migration downgrade: Soft delete chunk_idx field configuration from all knowledge IDs...")
    
    async with async_session() as session:
        # Get all active chunk_idx field configurations
        statement = select(FieldConfiguration).where(
            and_(
                FieldConfiguration.field_name == "chunk_idx",
                FieldConfiguration.deleted == False
            )
        )
        result = await session.execute(statement)
        field_configs = list(result.scalars().all())
        
        total_count = len(field_configs)
        print(f"Found {total_count} active chunk_idx field configurations to soft delete")
        
        for idx, field_config in enumerate(field_configs, 1):
            try:
                # Soft delete the field configuration
                field_config.deleted = True
                field_config.deleted_at = datetime.utcnow()
                field_config.updated_at = datetime.utcnow()
                session.add(field_config)
                deleted_count += 1
                
                if idx % 10 == 0:
                    print(f"  Progress: {idx}/{total_count} - Soft deleted chunk_idx for knowledge_id {field_config.knowledge_id}")
                
                # Commit in batches
                if idx % 50 == 0:
                    await session.commit()
                    print(f"  Committed batch at {idx}/{total_count}")
                    
            except Exception as e:
                error_count += 1
                print(f"  Error processing field_config id {field_config.id}: {str(e)}")
                await session.rollback()
                continue
        
        # Final commit for remaining changes
        await session.commit()
    
    print("\nMigration downgrade summary:")
    print(f"  Total field configurations processed: {total_count}")
    print(f"  Soft deleted: {deleted_count}")
    print(f"  Errors: {error_count}")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 005_add_chunk_idx_config.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()

