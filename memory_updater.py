import os
import json
import importlib
from cat.log import log
from cat.looking_glass.stray_cat import StrayCat
from cat.mad_hatter.decorators import plugin, endpoint, hook
from cat.looking_glass.cheshire_cat import CheshireCat
from cat.auth.permissions import AuthPermission, AuthResource, check_permissions
from pydantic import BaseModel
from typing import Dict
from .settings import Action


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


def save_plugin_settings_to_file(settings: dict, plugin_path: str) -> dict:
    """
    Save plugin settings to settings.json file in the plugin directory.
    This replicates the default save behavior from the Cat framework.
    
    Args:
        settings: The settings dictionary to save
        plugin_path: The path to the plugin directory
        
    Returns:
        The updated settings dictionary, or empty dict if save failed
    """
    settings_file_path = os.path.join(plugin_path, "settings.json")
    
    # Load already saved settings (replicate load_settings behavior)
    old_settings = {}
    if os.path.exists(settings_file_path):
        try:
            with open(settings_file_path, "r") as json_file:
                old_settings = json.load(json_file)
        except Exception as e:
            log.error(f"Unable to load existing settings: {e}")
    
    # Merge new settings with old ones
    updated_settings = {**old_settings, **settings}
    
    # Save settings to file
    try:
        with open(settings_file_path, "w") as json_file:
            json.dump(updated_settings, json_file, indent=4)
        return updated_settings
    except Exception as e:
        log.error(f"Unable to save plugin settings: {e}")
        return {}


@plugin
def save_settings(settings):
    ccat = CheshireCat()

    link = settings.get("link", "")
    action_str = settings.get("action", Action.DELETE.value)
    action = Action(action_str) if action_str in [e.value for e in Action] else Action.DELETE
    chunk_size = settings.get("chunk_size", 1024)
    chunk_overlap = settings.get("chunk_overlap", 256)
    
    if not link:
        log.warning("No link provided")
        
    else:
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
        
        # reset the link to empty after processing
        settings["link"] = ""
    
    # Save settings using the extracted function (replicates default Cat behavior)
    plugin_path = os.path.dirname(os.path.abspath(__file__))
    return save_plugin_settings_to_file(settings, plugin_path)


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


# ScrapyCat Integration - Middleman hooks for Dietician coordination
@hook(priority=5)
def scrapycat_after_scrape(context_data: dict, cat: StrayCat):
    """
    Hook that listens to ScrapyCat completion and coordinates with Dietician
    for cleanup of outdated scraped content.
    """    
    DIETICIAN_ID = "ccat-dietician"
    SCRAPYCAT_ID = "cc_scrapycat"
    dietician_plugin = cat.mad_hatter.plugins[DIETICIAN_ID] if DIETICIAN_ID in cat.mad_hatter.plugins else False
    scrapycat_plugin = cat.mad_hatter.plugins[SCRAPYCAT_ID] if SCRAPYCAT_ID in cat.mad_hatter.plugins else False
    
    settings = cat.mad_hatter.get_plugin().load_settings()
    
    mega_condition = (
        not settings.get("dietician_scrapycat_middleman", False) or not dietician_plugin.active or not scrapycat_plugin.active
    )

    if mega_condition:
        return context_data
    
    # Get the remove_documents_by_metadata function from the dietician plugin
    try:
        # Dynamically import the dietician plugin module
        # Construct the module path based on the plugin location
        dietician_module_path = "cat.plugins.ccat-dietician.dietician"
        dietician_module = importlib.import_module(dietician_module_path)
        remove_documents_by_metadata = getattr(dietician_module, 'remove_documents_by_metadata', None)
        
        # if not remove_documents_by_metadata:
        #     log.warning("remove_documents_by_metadata function not found in dietician plugin")
        #     return context_data
        
        session_id = context_data.get('session_id')
        command = context_data.get('command')
        failed_pages = context_data.get('failed_pages', [])
        scraped_pages = context_data.get('scraped_pages', [])
        
        # if not command:
        #     log.warning("ScrapyCat context missing command, skipping cleanup")
        #     return context_data
        
        log.info(f"Starting ScrapyCat-Dietician cleanup for session {session_id}, command: {command}")
        log.debug(f"Cleanup filter: command={command}, excluding {len(scraped_pages)} scraped URLs")
        
        # Remove outdated documents (same command, but source not in scraped pages)
        # This handles cases where Dietician blocks re-ingestion of unchanged content
        cleanup_result = remove_documents_by_metadata(
            cat=cat,
            metadata_filter={"command": command},
            exclude_sources=scraped_pages
        )
        
        log.info(f"Cleanup completed: {cleanup_result}")
        log.debug(f"Removed URLs: {cleanup_result.get('removed_urls', [])}")
        
        # Retry failed pages if enabled
        retry_results = {"success_count": 0, "failed_count": 0, "errors": []}
        if failed_pages and settings.get("retry_failed_urls", True):
            log.info(f"Attempting to retry {len(failed_pages)} failed URLs")
            
            for failed_url in failed_pages:
                try:
                    # Retry ingestion with current session metadata
                    metadata = {
                        "url": failed_url,
                        "source": failed_url,
                        "session_id": session_id,
                        "command": command
                    }
                    
                    # Use default chunk settings from ScrapyCat context or fallback to defaults
                    chunk_size = context_data.get('chunk_size', 512)
                    chunk_overlap = context_data.get('chunk_overlap', 128)
                    
                    cat.rabbit_hole.ingest_file(
                        cat=cat, 
                        file=failed_url, 
                        chunk_size=chunk_size, 
                        chunk_overlap=chunk_overlap,
                        metadata=metadata
                    )
                    
                    retry_results["success_count"] += 1
                    log.info(f"Successfully retried failed URL: {failed_url}")
                    
                except Exception as e:
                    retry_results["failed_count"] += 1
                    error_msg = f"Retry failed for {failed_url}: {str(e)}"
                    log.error(error_msg)
                    retry_results["errors"].append(error_msg)
            
            log.info(f"Retry completed: {retry_results}")
            
            # Send notification about retry results
            if retry_results["success_count"] > 0:
                cat.send_ws_message(
                    f"🔄 Successfully retried {retry_results['success_count']} previously failed URLs"
                )
            
            if retry_results["failed_count"] > 0:
                cat.send_ws_message(
                    f"⚠️ {retry_results['failed_count']} URLs still failed after retry"
                )
                
        elif failed_pages and not settings.get("retry_failed_urls", True):
            log.info(f"Retry disabled: skipping {len(failed_pages)} failed URLs")
        
        # Log failed pages processing summary
        if failed_pages:
            log.info(f"Failed pages processing completed: {retry_results}")
        
        # Send notification to user about cleanup
        if cleanup_result["removed_count"] > 0 or cleanup_result["vector_removed_count"] > 0:
            cat.send_ws_message(
                f"🧹 Cleaned up {cleanup_result['removed_count']} outdated documents "
                f"and {cleanup_result['vector_removed_count']} vector chunks from previous scraping sessions"
            )
        
    except Exception as e:
        log.error(f"Error in ScrapyCat-Dietician middleman: {str(e)}")
        # Don't fail the entire scraping process due to cleanup errors
    
    return context_data