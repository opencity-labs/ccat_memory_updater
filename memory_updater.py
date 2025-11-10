from cat.log import log
from cat.looking_glass.stray_cat import StrayCat
from cat.mad_hatter.decorators import plugin, endpoint
from cat.looking_glass.cheshire_cat import CheshireCat
from cat.auth.permissions import AuthPermission, AuthResource, check_permissions
from pydantic import BaseModel
from typing import Dict, Any
from .settings import Action
from fastapi import Request


class DeleteBySourceRequest(BaseModel):
    source: str


def delete_memories_by_source_logic(source: str, cat_or_ccat) -> int:
    """Delete all memories with a specific source and return the count of deleted points.
    
    Args:
        source: The source identifier to delete memories for
        cat_or_ccat: Either a StrayCat instance (from endpoint) or CheshireCat instance (from plugin)
    
    Returns:
        int: Number of points that were deleted
    """
    if not source:
        log.warning("No source provided for memory deletion")
        return 0
    
    # Handle both StrayCat (endpoint) and CheshireCat (plugin) instances
    if hasattr(cat_or_ccat, 'memory'):
        vector_memory = cat_or_ccat.memory.vectors
    else:
        vector_memory = cat_or_ccat.vectors
    
    collection = vector_memory.collections["declarative"]
    
    # First, count the points
    filter_obj = collection._qdrant_filter_from_dict({"source": source})
    points, _ = collection.client.scroll(
        collection_name=collection.collection_name,
        scroll_filter=filter_obj,
        limit=10000
    )
    
    point_count = len(points)
    log.info(f"Found {point_count} points with source: {source}")
    
    if point_count > 0:
        # Delete the points
        collection.delete_points_by_metadata_filter({"source": source})
        log.info(f"Deleted {point_count} memories with source: {source}")
    
    return point_count


@plugin
def save_settings(settings):
    ccat = CheshireCat()

    link = settings.get("link", "")
    action_str = settings.get("action", Action.DELETE.value)
    action = Action(action_str) if action_str in [e.value for e in Action] else Action.DELETE
    chunk_size = settings.get("chunk_size", 1024)
    chunk_overlap = settings.get("chunk_overlap", 256)
    
    if not link:
        log.error("No link provided")
        return None
    
    delete_memories_by_source_logic(link, ccat)
    
    if action == Action.REPLACE:
        # Upload new content from the link
        log.info(f"Uploading content from link: {link}")
        try:
            ccat.rabbit_hole.ingest_file(
                cat=ccat,
                file=link,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            log.info(f"Successfully uploaded content from {link}")
        except Exception as e:
            log.error(f"Failed to upload content from {link}: {e}")
    
    return None


@endpoint.delete(
    path="/memory/delete-by-source",
    tags=["Memory Updater"]
)
def delete_memories_by_source(
    request: DeleteBySourceRequest,
    cat: StrayCat = check_permissions(AuthResource.MEMORY, AuthPermission.DELETE),
) -> Dict[str, str]:
    """Delete all memories with a specific source."""
    
    source = request.source
    if not source:
        return {"error": "Source parameter is required"}
    
    deleted_count = delete_memories_by_source_logic(source, cat)
    
    return {
        "message": f"Successfully deleted {deleted_count} memories with source '{source}'"
    }