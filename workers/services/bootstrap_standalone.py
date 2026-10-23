"""Add worker-owned tables after API bootstrap; never alter shared API tables."""
import json
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlmodel import SQLModel, Session, select
from services import celery_setup_env as env
from services import pydantic_models_db  # noqa: F401

if __name__ == "__main__":
    if env.MODE != "STANDALONE":
        raise SystemExit("Only MODE=STANDALONE is supported by this fresh-install bootstrap")
    engine = create_engine(URL.create("postgresql+psycopg2", username=env.POSTGRES_USER,
        password=env.POSTGRES_PASSWORD, host=env.POSTGRES_HOST,
        port=int(env.POSTGRES_PORT), database=env.POSTGRES_DB_NAME))
    # Shared tables are created by the API's fuller model definitions first.
    SQLModel.metadata.create_all(engine)
    # Exact global prompts exported from the existing product, 2026-10-20.
    # Do not replace prompts already configured by an installation operator.
    prompts = json.loads(Path(__file__).with_name('standalone_prompts.json').read_text(encoding='utf-8'))
    with Session(engine) as session:
        for prompt_type, content in prompts.items():
            exists = session.exec(select(pydantic_models_db.AdminPrompts).where(
                pydantic_models_db.AdminPrompts.prompt_type == prompt_type,
                pydantic_models_db.AdminPrompts.deleted == False,
            )).first()
            if not exists:
                session.add(pydantic_models_db.AdminPrompts(prompt_type=prompt_type, prompt_content=content))
        session.commit()
    engine.dispose()
    print("Standalone worker schema ready")
