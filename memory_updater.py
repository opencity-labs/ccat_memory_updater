import os
import json
import time
import importlib
import threading
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any
from langchain.document_loaders.blob_loaders.schema import Blob
from langchain_community.document_loaders.parsers.generic import MimeTypeBasedParser

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

class ThreadSafeWorkingMemory:
    """
    A thread-safe mock of WorkingMemory that uses thread-local storage
    to capture data put into working memory during hook execution.
    """
    def __init__(self, local_storage):
        self._local = local_storage

    def __setitem__(self, key, value):
        # Store in thread-local storage
        if not hasattr(self._local, 'data'):
            self._local.data = {}
        self._local.data[key] = value

    def get(self, key, default=None):
        if hasattr(self._local, 'data'):
            return self._local.data.get(key, default)
        return default
    
    def __getitem__(self, key):
        if hasattr(self._local, 'data') and key in self._local.data:
            return self._local.data[key]
        raise KeyError(key)

    def __contains__(self, key):
        return hasattr(self._local, 'data') and key in self._local.data

    # Allow attribute assignment to be stored on the instance
    # This is needed because Dietician does: cat.working_memory.ccat_dietician = ...
    # Since this is a mock object, standard attribute assignment works fine, 
    # but we need to ensure we don't overwrite our internal methods/properties.


class ThreadSafeCat:
    """
    A thread-safe mock of the Cat instance to be passed to hooks running in threads.
    It provides a thread-local working memory to capture side effects.
    """
    def __init__(self, original_cat):
        self.original_cat = original_cat
        self._local = threading.local()
        self.working_memory = ThreadSafeWorkingMemory(self._local)
        
        # Copy necessary attributes from original cat
        self.mad_hatter = original_cat.mad_hatter
        self.rabbit_hole = original_cat.rabbit_hole
        # Add other attributes if needed by hooks

    def get_working_memory_data(self):
        """Retrieve the data stored in the thread-local working memory."""
        if hasattr(self._local, 'data'):
            return self._local.data
        return {}


def check_url(url: str, cat: CheshireCat, dietician_module) -> tuple[str, bool]:
    """
    Check if a URL should be updated by running the ingestion pipeline logic
    (fetching, parsing, hooks) and comparing the hash.
    
    Returns:
        tuple: (url, should_update)
    """
    try:
        # 1. Fetch content using httpx (same as RabbitHole)
        # We use the same User-Agent as RabbitHole to ensure we get the same content
        headers = {"User-Agent": "Magic Browser"}
        
        try:
            # RabbitHole does not follow redirects and does not raise for status
            # We must match this behavior exactly to get the same content hash
            response = httpx.get(url, headers=headers, timeout=10)
            
            # Get content type and bytes, just like RabbitHole
            content_type = response.headers.get("Content-Type", "text/html").split(";")[0]
            file_bytes = response.content
                
        except Exception as e:
            log.warning(json.dumps({
                "component": "ccat_memory_updater",
                "event": "url_check_fetch_error",
                "data": {
                    "url": url,
                    "error": str(e)
                }
            }))
            # If we can't fetch it to check, we assume it needs update (or let the main process fail it)
            return url, True

        # 2. Parse content using RabbitHole's parsers
        # This ensures we extract exactly the same text as the ingestion process
        try:
            blob = Blob(data=file_bytes, mimetype=content_type, source=url)
            
            # Access file handlers from the cat instance
            # Note: We access the private attribute via the property if available, or directly
            handlers = getattr(cat.rabbit_hole, "file_handlers", None)
            if not handlers:
                # Fallback to accessing private attribute if property doesn't exist
                handlers = getattr(cat.rabbit_hole, "_RabbitHole__file_handlers", None)
            
            if not handlers:
                log.warning(json.dumps({
                    "component": "ccat_memory_updater",
                    "event": "url_check_handler_error",
                    "data": {
                        "message": "Could not access RabbitHole file handlers. Falling back to update."
                    }
                }))
                return url, True

            parser = MimeTypeBasedParser(handlers=handlers)
            documents = parser.parse(blob)
            
        except Exception as e:
            log.warning(json.dumps({
                "component": "ccat_memory_updater",
                "event": "url_check_parse_error",
                "data": {
                    "url": url,
                    "error": str(e)
                }
            }))
            return url, True

        # 3. Run the `before_rabbithole_splits_text` hook
        # WORKAROUND: We execute this hook manually here to trigger Dietician's hash computation logic.
        # Dietician computes the hash and stores it in the cat's working memory (which we mocked with ThreadSafeCat).
        # This allows us to reuse Dietician's exact logic without duplicating code, ensuring consistency.
        thread_safe_cat = ThreadSafeCat(cat)
        
        # Execute the hook
        # We ignore the return value as we only need the side effect (hash computation)
        _ = cat.mad_hatter.execute_hook(
            "before_rabbithole_splits_text", 
            documents, 
            cat=thread_safe_cat
        )
        
        # 4. Retrieve the hash from the working memory
        # Dietician stores it as an attribute 'ccat_dietician' which is a dict containing 'hash'
        computed_hash = None
        
        # Check standard location (attribute on working_memory)
        if hasattr(thread_safe_cat.working_memory, "ccat_dietician"):
            dietician_data = thread_safe_cat.working_memory.ccat_dietician
            if isinstance(dietician_data, dict):
                computed_hash = dietician_data.get("hash")
        
        # Check temp storage (fallback if Dietician used cat._ccat_dietician_temp)
        if not computed_hash and hasattr(thread_safe_cat, "_ccat_dietician_temp"):
             dietician_data = thread_safe_cat._ccat_dietician_temp
             if isinstance(dietician_data, dict):
                computed_hash = dietician_data.get("hash")
        
        if not computed_hash:
            log.warning(json.dumps({
                "component": "ccat_memory_updater",
                "event": "url_check_hash_missing",
                "data": {
                    "url": url,
                    "message": "Dietician did not compute a hash. Is the plugin active?"
                }
            }))
            return url, True
            
        # log.debug(f"Parallel check hash for {url}: {computed_hash}")

        # 4. Check if we should update using Dietician's logic
        # We pass the computed hash directly to avoid re-computation
        should_update = dietician_module.check_should_update(
            url, 
            cat, 
            provided_hash=computed_hash
        )
        
        return url, should_update

    except Exception as e:
        log.error(json.dumps({
            "component": "ccat_memory_updater",
            "event": "url_check_error",
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
def scrapycat_after_crawl(context_data: Dict[str, Any], cat: CheshireCat) -> Dict[str, Any]:
    """
    Hook called by ScrapyCat after crawling is finished but before ingestion.
    We use this to parallelize the "should update" check.
    """
    
    # Load settings
    settings = cat.mad_hatter.plugins["ccat_memory_updater"].load_settings()
    
    # Check if Dietician is available
    try:
        dietician_module = importlib.import_module("cat.plugins.ccat_dietician.dietician")
        check_should_update = getattr(dietician_module, "check_should_update", None)
        remove_documents_by_metadata = getattr(dietician_module, "remove_documents_by_metadata", None)
    except ImportError:
        log.warning(json.dumps({
            "component": "ccat_memory_updater",
            "event": "optimization_skipped",
            "data": {
                "reason": "Dietician plugin not found"
            }
        }))
        return context_data

    if not check_should_update or not remove_documents_by_metadata:
        log.warning(json.dumps({
            "component": "ccat_memory_updater",
            "event": "optimization_skipped",
            "data": {
                "reason": "Dietician functions not found"
            }
        }))
        return context_data

    session_id = context_data.get('session_id')
    command = context_data.get('command')
    scraped_pages = context_data.get('scraped_pages', [])
    failed_pages = context_data.get('failed_pages', [])
    
    log.info(json.dumps({
        "component": "ccat_memory_updater",
        "event": "optimization_start",
        "data": {
            "session_id": session_id
        }
    }))
    
    # --- Parallel "Should Update" Check ---
    
    # Only perform check if we have pages and it's not a forced update (logic inside check_should_update handles force)
    # But we can check if we want to enable this optimization in settings
    enable_parallel_check = settings.get("enable_parallel_check", True)
    
    if enable_parallel_check and scraped_pages:
        log.info(json.dumps({
            "component": "ccat_memory_updater",
            "event": "optimization_check_start",
            "data": {
                "page_count": len(scraped_pages)
            }
        }))
        
        pages_to_update = []
        pages_ignored = []
        
        max_workers = settings.get("check_workers", 10)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all checks
            future_to_url = {
                executor.submit(check_url, url, cat, dietician_module): url 
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
            cat.send_ws_message(f"ℹ️ Skipped {len(pages_ignored)} unchanged pages.")

    return context_data


@hook(priority=10)
def scrapycat_after_scrape(context_data: dict, cat: StrayCat):
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
                    f"🔄 Successfully retried {retry_results['success_count']} previously failed URLs"
                )
            
            if retry_results["failed_count"] > 0:
                cat.send_ws_message(
                    f"⚠️ {retry_results['failed_count']} URLs still failed after {max_attempts} retry attempts"
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
        
        log.info(json.dumps({
            "component": "ccat_memory_updater",
            "event": "cleanup_complete",
            "data": cleanup_result
        }))
        # log.debug(f"Removed URLs: {cleanup_result.get('removed_urls', [])}")
        
        # Send notification to user about cleanup
        if cleanup_result["removed_count"] > 0 or cleanup_result["vector_removed_count"] > 0:
            cat.send_ws_message(
                f"🧹 Cleaned up {cleanup_result['removed_count']} outdated documents "
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