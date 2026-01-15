import os
import json
import time
import importlib
import threading
import httpx
import email.utils
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional

from cat.log import log
from cat.looking_glass.stray_cat import StrayCat
from cat.mad_hatter.decorators import plugin, endpoint, hook
from cat.looking_glass.cheshire_cat import CheshireCat
from cat.auth.permissions import AuthPermission, AuthResource, check_permissions
from cat.db import crud
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
        log.warning(json.dumps({
            "component": "ccat_memory_updater",
            "event": "memory_deletion_warning",
            "data": {
                "message": "No source provided for memory deletion"
            }
        }))
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
    log.info(json.dumps({
        "component": "ccat_memory_updater",
        "event": "memory_deletion_scan",
        "data": {
            "source": source,
            "points_found": point_count
        }
    }))
    
    if point_count > 0:
        # Delete the points
        collection.delete_points_by_metadata_filter({"source": source})
        log.info(json.dumps({
            "component": "ccat_memory_updater",
            "event": "memory_deletion_success",
            "data": {
                "source": source,
                "points_deleted": point_count
            }
        }))
    
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
            log.error(json.dumps({
                "component": "ccat_memory_updater",
                "event": "settings_load_error",
                "data": {
                    "error": str(e)
                }
            }))
    
    # Merge new settings with old ones
    updated_settings = {**old_settings, **settings}
    
    # Save settings to file
    try:
        with open(settings_file_path, "w") as json_file:
            json.dump(updated_settings, json_file, indent=4)
        return updated_settings
    except Exception as e:
        log.error(json.dumps({
            "component": "ccat_memory_updater",
            "event": "settings_save_error",
            "data": {
                "error": str(e)
            }
        }))
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
        log.warning(json.dumps({
            "component": "ccat_memory_updater",
            "event": "settings_warning",
            "data": {
                "message": "No link provided"
            }
        }))
        
    else:
        delete_memories_by_source_logic(link, ccat)
        
        if action == Action.REPLACE:
            # Upload new content from the link
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "content_upload_start",
                "data": {
                    "link": link
                }
            }))
            try:
                ccat.rabbit_hole.ingest_file(
                    cat=ccat,
                    file=link,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap
                )
                log.info(json.dumps({
                    "component": "ccat_memory_updater",
                    "event": "content_upload_success",
                    "data": {
                        "link": link
                    }
                }))
            except Exception as e:
                log.error(json.dumps({
                    "component": "ccat_memory_updater",
                    "event": "content_upload_error",
                    "data": {
                        "link": link,
                        "error": str(e)
                    }
                }))
        
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


# --- Thread-Safe Mock Classes for Parallel Execution ---
# (Removed as we no longer use the heavy hook-based check)

def check_url(url: str, cat: CheshireCat) -> tuple[str, bool]:
    """
    Check if a URL should be updated by comparing server's Last-Modified/ETag
    with the ingestion timestamp of existing memories.
    
    Returns:
        tuple: (url, should_update)
    """
    try:
        # 1. Check if URL exists in vector memory
        collection = cat.memory.vectors.declarative
        filter_obj = collection._qdrant_filter_from_dict({"source": url})
        
        # We only need one point to get the metadata
        points, _ = collection.client.scroll(
            collection_name=collection.collection_name,
            scroll_filter=filter_obj,
            limit=1,
            with_payload=True
        )
        
        if not points:
            # New URL, must scrape
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "check_url_decision",
                "data": {
                    "url": url,
                    "decision": "update",
                    "reason": "new_url_no_memory"
                }
            }))
            return url, True
            
        # Get ingestion timestamp from the first point
        # RabbitHole stores 'when' as a float timestamp in metadata
        metadata = points[0].payload.get("metadata", {})
        ingestion_timestamp = metadata.get("when")
        
        if not ingestion_timestamp:
            # No timestamp, assume update needed
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "check_url_decision",
                "data": {
                    "url": url,
                    "decision": "update",
                    "reason": "no_ingestion_timestamp"
                }
            }))
            return url, True
            
        # Ensure ingestion_timestamp is a float
        try:
            ingestion_time = datetime.fromtimestamp(float(ingestion_timestamp), tz=timezone.utc)
        except (ValueError, TypeError):
            # Invalid timestamp, update
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "check_url_decision",
                "data": {
                    "url": url,
                    "decision": "update",
                    "reason": "invalid_ingestion_timestamp",
                    "raw_timestamp": str(ingestion_timestamp)
                }
            }))
            return url, True

        # 2. Fetch HTTP headers
        headers = {"User-Agent": "Magic Browser"}
        try:
            # Use HEAD request to get headers only
            response = httpx.head(url, headers=headers, timeout=10, follow_redirects=True)
            
            # If HEAD fails (e.g. 405 Method Not Allowed), try GET with stream=True to avoid downloading body
            if response.status_code == 405:
                 response = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
                 response.close() # Close immediately
            
            # If still error, assume update needed
            if response.status_code >= 400:
                log.info(json.dumps({
                    "component": "ccat_memory_updater",
                    "event": "check_url_decision",
                    "data": {
                        "url": url,
                        "decision": "update",
                        "reason": "http_error",
                        "status_code": response.status_code
                    }
                }))
                return url, True
                
        except Exception as e:
            log.warning(json.dumps({
                "component": "ccat_memory_updater",
                "event": "check_url_fetch_error",
                "data": {
                    "url": url,
                    "error": str(e)
                }
            }))
            return url, True
            
        # 3. Compare timestamps
        server_etag = response.headers.get("ETag")
        if server_etag:
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "check_url_etag_found",
                "data": {
                    "url": url,
                    "etag": server_etag
                }
            }))
        else:
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "check_url_no_etag",
                "data": {
                    "url": url
                }
            }))

        server_last_modified = response.headers.get("Last-Modified")
        
        if server_last_modified:
            try:
                # Parse HTTP date format (RFC 2822)
                server_time = email.utils.parsedate_to_datetime(server_last_modified)
                if server_time.tzinfo is None:
                    server_time = server_time.replace(tzinfo=timezone.utc)
                
                # Add a small buffer (e.g. 1 second) to avoid precision issues
                if server_time <= ingestion_time:
                    # Content is older or same age as ingestion -> Unchanged
                    log.info(json.dumps({
                        "component": "ccat_memory_updater",
                        "event": "check_url_decision",
                        "data": {
                            "url": url,
                            "decision": "skip",
                            "reason": "unchanged",
                            "server_time": str(server_time),
                            "ingestion_time": str(ingestion_time)
                        }
                    }))
                    return url, False
                else:
                    # Content is newer -> Update
                    log.info(json.dumps({
                        "component": "ccat_memory_updater",
                        "event": "check_url_decision",
                        "data": {
                            "url": url,
                            "decision": "update",
                            "reason": "content_newer",
                            "server_time": str(server_time),
                            "ingestion_time": str(ingestion_time)
                        }
                    }))
                    return url, True
            except Exception as e:
                log.warning(json.dumps({
                    "component": "ccat_memory_updater",
                    "event": "check_url_date_parse_error",
                    "data": {
                        "url": url,
                        "error": str(e),
                        "last_modified_header": server_last_modified
                    }
                }))
                return url, True
        else:
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "check_url_decision",
                "data": {
                    "url": url,
                    "decision": "update",
                    "reason": "missing_last_modified"
                }
            }))
        
        # Fallback: Check ETag if Last-Modified is missing
        # Note: ETag comparison requires storing the ETag from previous scrape.
        # Since we don't store ETag in metadata currently, we can't use it for comparison yet.
        # We could store it now for future checks, but for this run we have to assume update if Last-Modified is missing.
        
        # log.debug(f"Check {url}: No Last-Modified header")
        return url, True

    except Exception as e:
        log.error(json.dumps({
            "component": "ccat_memory_updater",
            "event": "check_url_unexpected_error",
            "data": {
                "url": url,
                "error": str(e)
            }
        }))
        return url, True


# ScrapyCat Integration - Middleman hooks for Dietician coordination
def check_plugin_active(plugin_id: str, cat: StrayCat) -> bool:
    """
    Check if a plugin is active using multiple reliable methods.
    
    This approach works whether plugins are installed via web interface or 
    downloaded directly to the plugin folder.
    
    Args:
        plugin_id: The plugin identifier to check
        cat: StrayCat instance for accessing mad_hatter
    
    Returns:
        bool: True if plugin is active, False otherwise
    """
    try:
        # Method 1: Check if plugin is in active_plugins list (most reliable)
        active_plugins = getattr(cat.mad_hatter, 'active_plugins', [])
        # log.debug(f"Active plugins from mad_hatter: {active_plugins}")
        
        if plugin_id in active_plugins:
            # log.debug(f"Plugin {plugin_id} found in active_plugins list")
            return True
            
        # Method 2: Check database directly for active_plugins setting
        active_plugins_setting = crud.get_setting_by_name("active_plugins")
        if active_plugins_setting:
            db_active_plugins = active_plugins_setting.get("value", [])
            # log.debug(f"Active plugins from database: {db_active_plugins}")
            if plugin_id in db_active_plugins:
                # log.debug(f"Plugin {plugin_id} found in database active_plugins")
                return True
        else:
            pass
            # log.debug("No active_plugins setting found in database")
            
        # Method 3: Check if plugin exists and is loaded in mad_hatter.plugins
        if hasattr(cat.mad_hatter, 'plugins') and plugin_id in cat.mad_hatter.plugins:
            # log.debug(f"Plugin {plugin_id} found in loaded plugins but not in active list")
            # Plugin is loaded, check if it's in active list (redundant but safe)
            return plugin_id in active_plugins
            
        # log.debug(f"Plugin {plugin_id} not found in any check method")
        return False
    except Exception as e:
        log.warning(json.dumps({
            "component": "ccat_memory_updater",
            "event": "plugin_check_error",
            "data": {
                "plugin_id": plugin_id,
                "error": str(e)
            }
        }))
        return False


@hook
def scrapycat_after_scraping(context_data: Dict[str, Any], cat: CheshireCat) -> Dict[str, Any]:
    """
    Hook called by ScrapyCat after scraping is finished but before ingestion.
    We use this to parallelize the "should update" check.
    """
    
    # Load settings
    settings = cat.mad_hatter.plugins["ccat_memory_updater"].load_settings()
    
    session_id = context_data.get('session_id')
    scraped_pages = context_data.get('scraped_pages', [])
    
    
    # --- Parallel "Should Update" Check ---
    
    # Only perform check if we have pages
    # But we can check if we want to enable this optimization in settings
    enable_parallel_check = settings.get("enable_parallel_check", False)
    
    if enable_parallel_check and scraped_pages:
        log.info(json.dumps({
            "component": "ccat_memory_updater",
            "event": "optimization_check_start",
            "data": {
                "page_count": len(scraped_pages),
                "session_id": session_id
            }
        }))
        
        pages_to_update = []
        pages_ignored = []
        
        max_workers = settings.get("check_workers", 10)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all checks
            future_to_url = {
                executor.submit(check_url, url, cat): url 
                for url in scraped_pages
            }
            
            processed_count = 0
            total_pages = len(scraped_pages)

            for future in as_completed(future_to_url):
                processed_count += 1
                url = future_to_url[future]

                if processed_count % 5 == 0 or processed_count == total_pages:
                    log.info(json.dumps({
                        "component": "ccat_memory_updater",
                        "event": "optimization_progress",
                        "data": {
                            "processed": processed_count,
                            "total": total_pages,
                            "percentage": f"{processed_count/total_pages*100:.1f}%"
                        }
                    }))

                try:
                    checked_url, should_update = future.result()
                    if should_update:
                        pages_to_update.append(checked_url)
                    else:
                        pages_ignored.append(checked_url)
                except Exception as e:
                    log.error(json.dumps({
                        "component": "ccat_memory_updater",
                        "event": "optimization_check_error",
                        "data": {
                            "url": url,
                            "error": str(e)
                        }
                    }))
                    pages_to_update.append(url) # Default to update on error
        
        log.info(json.dumps({
            "component": "ccat_memory_updater",
            "event": "optimization_complete",
            "data": {
                "pages_to_update": len(pages_to_update),
                "pages_ignored": len(pages_ignored)
            }
        }))
        
        # Update context
        context_data['scraped_pages'] = pages_to_update
        
        # Initialize ignored_pages if not present
        if 'ignored_pages' not in context_data:
            context_data['ignored_pages'] = []
        context_data['ignored_pages'].extend(pages_ignored)
        
        if pages_ignored:
            cat.send_ws_message(f"Skipped {len(pages_ignored)} unchanged pages.")

    return context_data


@hook(priority=10)
def scrapycat_after_ingestion(context_data: dict, cat: StrayCat):
    """
    Hook that listens to ScrapyCat completion and coordinates with Dietician
    for cleanup of outdated scraped content.
    """
    DIETICIAN_ID = "ccat_dietician"
    SCRAPYCAT_ID = "cc_scrapycat"
    
    # Use robust plugin checking method
    dietician_plugin = check_plugin_active(DIETICIAN_ID, cat)
    scrapycat_plugin = check_plugin_active(SCRAPYCAT_ID, cat)
    
    log.info(json.dumps({
        "component": "ccat_memory_updater",
        "event": "middleman_hook_triggered",
        "data": {
            "dietician_plugin": dietician_plugin,
            "scrapycat_plugin": scrapycat_plugin
        }
    }))
    
    settings = cat.mad_hatter.get_plugin().load_settings()
    
    available = (
        settings.get("dietician_scrapycat_middleman", False) and dietician_plugin and scrapycat_plugin
    )

    if not available:
        return context_data
    
    try:
        # Dynamically import the dietician plugin module
        # Construct the module path based on the plugin location
        # Try to import dietician plugin - it could be in either location
        dietician_module = None
        remove_documents_by_metadata = None
        
        try:
            # First try: with hyphen (ccat-dietician)
            dietician_module_path = "cat.plugins.ccat-dietician.dietician"
            dietician_module = importlib.import_module(dietician_module_path)
            remove_documents_by_metadata = getattr(dietician_module, 'remove_documents_by_metadata', None)
        except ImportError:
            try:
            # Second try: with underscore (ccat_dietician)
                dietician_module_path = "cat.plugins.ccat_dietician.dietician"
                dietician_module = importlib.import_module(dietician_module_path)
                remove_documents_by_metadata = getattr(dietician_module, 'remove_documents_by_metadata', None)
            except ImportError:
                log.error(json.dumps({
                    "component": "ccat_memory_updater",
                    "event": "middleman_error",
                    "data": {
                        "error": "Dietician plugin module not found in either location"
                    }
                }))
                return context_data
        
        if not remove_documents_by_metadata:
            log.error(json.dumps({
                "component": "ccat_memory_updater",
                "event": "middleman_error",
                "data": {
                    "error": "remove_documents_by_metadata function not found in dietician plugin"
                }
            }))
            return context_data
        
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
        
        log.info(json.dumps({
            "component": "ccat_memory_updater",
            "event": "cleanup_start",
            "data": {
                "session_id": session_id,
                "command": command,
                "scraped_count": len(scraped_pages),
                "failed_count": len(failed_pages)
            }
        }))
        
        # Retry failed pages if enabled (before cleanup)
        retry_results = {"success_count": 0, "failed_count": 0, "errors": []}
        remaining_failed = list(failed_pages)  # Create a mutable copy
        updated_scraped = list(scraped_pages)  # Create a mutable copy
        
        if failed_pages and settings.get("retry_failed_urls", True):
            max_attempts = settings.get("max_retry_attempts", 3)
            retry_delay = settings.get("retry_delay_seconds", 10)
            
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "retry_start",
                "data": {
                    "failed_count": len(failed_pages),
                    "max_attempts": max_attempts
                }
            }))
            
            for attempt in range(1, max_attempts + 1):
                if not remaining_failed:
                    log.info(json.dumps({
                        "component": "ccat_memory_updater",
                        "event": "retry_success_all",
                        "data": {
                            "message": "All failed URLs successfully retried, stopping early"
                        }
                    }))
                    break
                
                log.info(json.dumps({
                    "component": "ccat_memory_updater",
                    "event": "retry_attempt",
                    "data": {
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "remaining_count": len(remaining_failed)
                    }
                }))
                
                urls_to_retry = list(remaining_failed)  # Copy current failed list
                
                for failed_url in urls_to_retry:
                    try:
                        # Retry ingestion with current session metadata
                        metadata = {
                            "url": failed_url,
                            "source": failed_url,
                            "session_id": session_id,
                            "command": command
                        }
                        
                        # Use default chunk settings from ScrapyCat context or fallback to defaults
                        chunk_size = context_data.get('chunk_size', 1024)
                        chunk_overlap = context_data.get('chunk_overlap', 256)
                        
                        cat.rabbit_hole.ingest_file(
                            cat=cat, 
                            file=failed_url, 
                            chunk_size=chunk_size, 
                            chunk_overlap=chunk_overlap,
                            metadata=metadata
                        )
                        
                        # Success: move from failed to scraped
                        remaining_failed.remove(failed_url)
                        updated_scraped.append(failed_url)
                        retry_results["success_count"] += 1
                        log.info(json.dumps({
                            "component": "ccat_memory_updater",
                            "event": "retry_url_success",
                            "data": {
                                "attempt": attempt,
                                "url": failed_url
                            }
                        }))
                        
                    except Exception as e:
                        error_msg = f"[Attempt {attempt}] Retry failed for {failed_url}: {str(e)}"
                        log.warning(json.dumps({
                            "component": "ccat_memory_updater",
                            "event": "retry_url_failed",
                            "data": {
                                "attempt": attempt,
                                "url": failed_url,
                                "error": str(e)
                            }
                        }))
                        retry_results["errors"].append(error_msg)
                
                # Wait before next attempt (if there are more attempts and still failed URLs)
                if attempt < max_attempts and remaining_failed:
                    log.info(json.dumps({
                        "component": "ccat_memory_updater",
                        "event": "retry_wait",
                        "data": {
                            "seconds": retry_delay
                        }
                    }))
                    time.sleep(retry_delay)
            
            # Count final failures
            retry_results["failed_count"] = len(remaining_failed)
            
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "retry_complete",
                "data": {
                    "success_count": retry_results['success_count'],
                    "failed_count": retry_results['failed_count']
                }
            }))
            
            # Send notification about retry results
            if retry_results["success_count"] > 0:
                cat.send_ws_message(
                    f"Successfully retried {retry_results['success_count']} previously failed URLs"
                )
            
            if retry_results["failed_count"] > 0:
                cat.send_ws_message(
                    f"{retry_results['failed_count']} URLs still failed after {max_attempts} retry attempts"
                )
                
        elif failed_pages and not settings.get("retry_failed_urls", True):
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "retry_disabled",
                "data": {
                    "skipped_count": len(failed_pages)
                }
            }))
        
        # Log failed pages processing summary
        if failed_pages:
            log.info(json.dumps({
                "component": "ccat_memory_updater",
                "event": "retry_summary",
                "data": retry_results
            }))
        
        # Update context_data with retry results so main process sees the updated state
        context_data['scraped_pages'] = updated_scraped
        context_data['failed_pages'] = remaining_failed
        # log.debug(f"Updated context: {len(updated_scraped)} total scraped URLs, {len(remaining_failed)} remaining failed URLs")
        
        # Remove outdated documents (same command, but source not in updated scraped pages)
        # This cleanup happens AFTER retries, so successful retries are preserved
        
        # We exclude:
        # 1. Pages that were just scraped/updated (updated_scraped)
        # 2. Pages that were checked and found unchanged (pages_ignored)
        # 3. Pages that failed (remaining_failed)
        
        exclude_sources = updated_scraped + context_data.get('ignored_pages', []) + remaining_failed
        
        # log.debug(f"Cleanup filter: command={command}, excluding {len(exclude_sources)} URLs")
        
        cleanup_result = remove_documents_by_metadata(
            cat=cat,
            metadata_filter={"command": command},
            exclude_sources=exclude_sources
        )
        
        # Remove sources from anonymizer allowedlist if present
        removed_urls = cleanup_result.get('removed_urls', [])
        if removed_urls:
            try:
                # Try to import remove_source from ccat_anonymizer
                # We use dynamic import to avoid hard dependency
                anonymizer_module = importlib.import_module("cat.plugins.ccat_anonymizer.allowedlist")
                remove_source = getattr(anonymizer_module, "remove_source", None)
                
                if remove_source:
                    for url in removed_urls:
                        remove_source(url)
                    
                    log.info(json.dumps({
                        "component": "ccat_memory_updater",
                        "event": "anonymizer_cleanup",
                        "data": {
                            "removed_sources_count": len(removed_urls)
                        }
                    }))
            except ImportError:
                # Plugin not installed or different name, ignore
                pass
            except Exception as e:
                log.error(json.dumps({
                    "component": "ccat_memory_updater",
                    "event": "anonymizer_cleanup_error",
                    "data": {
                        "error": str(e)
                    }
                }))

        log.info(json.dumps({
            "component": "ccat_memory_updater",
            "event": "cleanup_complete",
            "data": cleanup_result
        }))
        # log.debug(f"Removed URLs: {cleanup_result.get('removed_urls', [])}")
        
        # Send notification to user about cleanup
        if cleanup_result["removed_count"] > 0 or cleanup_result["vector_removed_count"] > 0:
            cat.send_ws_message(
                f"Cleaned up {cleanup_result['removed_count']} outdated documents "
                f"and {cleanup_result['vector_removed_count']} vector chunks from previous scraping sessions"
            )
        
    except Exception as e:
        log.error(json.dumps({
            "component": "ccat_memory_updater",
            "event": "middleman_error",
            "data": {
                "error": str(e)
            }
        }))
    
    return context_data