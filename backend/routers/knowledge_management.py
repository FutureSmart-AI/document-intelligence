from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, List
from utils.logger import logger
from utils.auth.dependencies import get_api_key_or_session, get_api_key_session_or_scoped_key, require_permission
from utils.auth.auth_utils import extract_owner_id_from_session
from utils.common_utils import generate_object_id as generate_knowledge_id
from utils.db.knowledge_invitations import list_shared_knowledge
from utils.db.knowledge_ops import (
	create_knowledge_service,
	get_knowledge_services_by_owner,
	get_knowledge_service,
	get_knowledge_summaries,
)
from utils.db.session import async_session
from utils.pydantic_models_knowledge import KnowledgeConfig, GenerateKnowledgeRequest, GenerateKnowledgeResponse
from utils.db.knowledge_setting_ops import delete_knowledge_config
from utils.pydantic_models_usage_limits import EstimatedMetric, Operation
from utils.usage.enforcement import enforce_operation


router = APIRouter()


def _caller_owner_id(auth: dict) -> Optional[str]:
	"""Normalized account id for a session or generated-key caller."""
	if auth.get("auth_type") == "user_api_key":
		user_id = auth.get("user_id")
		return str(user_id).strip().lower() if user_id else None
	return extract_owner_id_from_session(auth["session_data"])


@router.get("/v1/validate")
async def validate_knowledge(
	owner_id: str = Query(...),
	knowledge_id: str = Query(...),
	auth: dict = Depends(get_api_key_session_or_scoped_key),
	_: Optional[str] = Depends(require_permission("read")),
):
	"""
	Validate if a knowledge exists and belongs to the specified owner.
	Returns {"valid": true/false}.
	"""
	try:
		if not owner_id or not knowledge_id:
			return {"valid": False}

		# Session and generated-key callers can only validate their own owner_id
		if auth.get("auth_type") in ("session", "user_api_key"):
			caller_owner_id = _caller_owner_id(auth)
			if not caller_owner_id or caller_owner_id != owner_id.strip().lower():
				return {"valid": False}

		service_details = await get_knowledge_service(knowledge_id)
		service_owner_id = (service_details or {}).get("owner_id")
		if service_owner_id and str(service_owner_id).strip().lower() == owner_id.strip().lower():
			logger.info(f"Knowledge validation successful - owner_id: {owner_id}, knowledge_id: {knowledge_id}")
			return {"valid": True}
		logger.info(f"Knowledge validation failed - owner_id: {owner_id}, knowledge_id: {knowledge_id}")
		return {"valid": False}
	except Exception as e:
		logger.error(f"Error validating knowledge: {str(e)}")
		return {"valid": False}


@router.post("/v1", response_model=GenerateKnowledgeResponse)
async def create_knowledge(request: GenerateKnowledgeRequest, auth: dict = Depends(get_api_key_or_session)):
	"""
	Create a new knowledge service for an owner and return the knowledge_id.
	"""
	try:
		if not request.owner_id:
			raise HTTPException(status_code=400, detail="owner_id is required")

		if auth.get("auth_type") == "session":
			caller_owner_id = extract_owner_id_from_session(auth["session_data"])
			if not caller_owner_id or request.owner_id.strip().lower() != caller_owner_id:
				raise HTTPException(status_code=403, detail="You can only create knowledge services for your own email")

		knowledge_id = await generate_knowledge_id()
		normalized_owner_id = request.owner_id.strip().lower()
		collection_name = request.collection_name.strip() if request.collection_name else None

		# Refuse before writing the row — max_agents caps how many knowledge bases
		# ("agents") an owner may hold at once. Standing count, so deleting one frees
		# a slot. Enforce/observe policy and fail-open are in utils/usage/enforcement.py.
		await enforce_operation(
			owner_id=normalized_owner_id,
			operation=Operation.create_agent,
			estimated_metrics=[EstimatedMetric(name="agents", value=1)],
			source="create_agent",
		)

		await create_knowledge_service(knowledge_id=knowledge_id, owner_id=normalized_owner_id, collection_name=collection_name)
		logger.info(f"Created knowledge {knowledge_id} for owner {normalized_owner_id}" + (f" named {collection_name!r}" if collection_name else ""))

		# audit_access can't see knowledge_id (it's generated above, after the request
		# is received), so log the creation explicitly with the real id.
		from utils.audit_logging import log_access_event
		actor_user_id = (
			auth["session_data"].get("email") if auth.get("auth_type") == "session" else "master_api_key"
		)
		await log_access_event(
			user_id=actor_user_id,
			owner_id=normalized_owner_id,
			knowledge_id=knowledge_id,
			action="create_knowledge",
			method="POST",
			path="/v1",
		)

		return GenerateKnowledgeResponse(status="success", knowledge_id=knowledge_id, message="Knowledge service created successfully")
	except HTTPException:
		# Preserve the intended status/detail — a quota 429, an auth 403, and a
		# validation 400 all live inside this try block and must not be flattened to 500.
		raise
	except Exception as e:
		logger.error(f"Error creating knowledge: {str(e)}")
		raise HTTPException(status_code=500, detail=f"An unexpected error occurred while creating knowledge: {e}")


@router.get("/v1")
async def list_knowledge(
	owner_id: Optional[str] = Query(None),
	include: Optional[str] = Query(None, description="Set to 'summary' to get per-collection document count, processing rollup, and field counts inline instead of a bare id list. Omit to keep the original bare-id response."),
	auth: dict = Depends(get_api_key_session_or_scoped_key),
):
	"""
	List knowledge services. If owner_id is provided, returns every collection
	that account can reach — owned and shared alike — as bare ids. If not
	provided, returns all non-deleted knowledge services (master API key only).

	include=summary exists because a Home-style "one card per collection" UI
	otherwise has to call GET /config and GET /enrichment per collection just
	to render a card — 1 + 2N requests for N collections, with no document
	count available at all no matter how many extra calls are made (no
	endpoint returns one). This aggregates that in one extra pass over
	Document/FieldConfiguration/KnowledgeConfig, grouped by knowledge_id, so
	it's a fixed number of queries regardless of N. See
	docs/screen-api-flow-document-intelligence.md §3 (G2+G3).

	Includes `collection_name` (G1) — null for a collection created before
	that column existed, or created without one.

	Owned vs. shared is deliberately NOT distinguished in this response (no
	`owner_id` per item either — see docs/api/knowledge-invitations.md). The
	UI's own Share flow calls `GET /v1/validate
	?owner_id=…&knowledge_id=…` once per id from this list to split them into
	its "My collections" / "Shared with me" tabs — that is the intended
	discovery contract this endpoint feeds, not a separate "shared" listing.
	"""
	try:
		# Session and generated-key callers: verify the requested owner_id belongs to
		# them. A key with no owner_id lists its own owner's collections — never the
		# all-collections listing, which is master-key only.
		if auth.get("auth_type") in ("session", "user_api_key"):
			caller_owner_id = _caller_owner_id(auth)
			if owner_id and (not caller_owner_id or owner_id.strip().lower() != caller_owner_id):
				raise HTTPException(status_code=403, detail="You can only list your own knowledge services")
			if auth.get("auth_type") == "user_api_key":
				if not caller_owner_id:
					raise HTTPException(status_code=401, detail="Could not resolve caller identity")
				owner_id = caller_owner_id

		if owner_id:
			owned_ids = await get_knowledge_services_by_owner(owner_id)
			shared_rows = await list_shared_knowledge(owner_id)
			shared_ids = [row["knowledge_id"] for row in shared_rows]
			# Union, de-duplicated, owned first — a collection can never be both,
			# but a stale/duplicate invitation row must not double-list one.
			knowledge_ids = list(dict.fromkeys([*owned_ids, *shared_ids]))
		else:
			# List all knowledge services (non-deleted)
			async with async_session() as session:
				from sqlmodel import select
				result = await session.execute(select(KnowledgeConfig.knowledge_id).where(KnowledgeConfig.deleted == False))
				knowledge_ids: List[str] = list(result.scalars().all())

		# A collection key sees only its own collection.
		key_knowledge_id = auth.get("knowledge_id") if auth.get("auth_type") == "user_api_key" else None
		if key_knowledge_id:
			knowledge_ids = [kid for kid in knowledge_ids if kid == key_knowledge_id]

		if include != "summary":
			if owner_id:
				return {"owner_id": owner_id, "knowledge_services": knowledge_ids, "count": len(knowledge_ids)}
			return {"knowledge_services": knowledge_ids, "count": len(knowledge_ids)}

		summaries = await get_knowledge_summaries(knowledge_ids)
		services = [
			{"knowledge_id": kid, **summaries.get(kid, {})}
			for kid in knowledge_ids
		]
		response = {"knowledge_services": services, "count": len(services)}
		if owner_id:
			response["owner_id"] = owner_id
		return response
	except HTTPException:
		# Preserve the intended status/detail — the 403 "not your own owner_id"
		# check above must not be flattened to a 500 by the catch-all below.
		# Pre-existing bug, found while testing this endpoint's shared-collection
		# fix (docs/plan/access-and-sharing/decisions.md, 2026-08-25).
		raise
	except Exception as e:
		logger.error(f"Error listing knowledge services: {str(e)}")
		raise HTTPException(status_code=500, detail=f"An unexpected error occurred while listing knowledge: {e}")


@router.delete("/v1/{knowledge_id}")
async def delete_knowledge(knowledge_id: str, _: Optional[str] = Depends(require_permission("delete"))):
	"""
	Soft delete a knowledge service by knowledge_id.
	"""
	try:
		deleted = await delete_knowledge_config(knowledge_id)
		if not deleted:
			raise HTTPException(status_code=404, detail="Knowledge not found or already deleted")
		logger.info(f"Deleted knowledge {knowledge_id}")
		return {"status": "success", "knowledge_id": knowledge_id}
	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"Error deleting knowledge: {str(e)}")
		raise HTTPException(status_code=500, detail=f"An unexpected error occurred while deleting knowledge: {e}")


