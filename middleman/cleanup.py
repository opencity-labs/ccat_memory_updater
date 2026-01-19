import importlib
import json
import time

from cat.log import log
from cat.looking_glass.stray_cat import StrayCat
from cat.mad_hatter.decorators import hook
from cat.db import crud

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
        
        if plugin_id in active_plugins:
            return True
            
        # Method 2: Check database directly for active_plugins setting
        active_plugins_setting = crud.get_setting_by_name("active_plugins")
        if active_plugins_setting:
            db_active_plugins = active_plugins_setting.get("value", [])
            if plugin_id in db_active_plugins:
                return True
        else:
            pass
            
        # Method 3: Check if plugin exists and is loaded in mad_hatter.plugins
        if hasattr(cat.mad_hatter, 'plugins') and plugin_id in cat.mad_hatter.plugins:
            # Plugin is loaded, check if it's in active list (redundant but safe)
            return plugin_id in active_plugins
            
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
        dietician_module = None
        remove_documents_by_metadata = None
        
        try:
            dietician_module_path = f"cat.plugins.{DIETICIAN_ID}.dietician"
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
        
        session_id = context_data.get('session_id')
        command = context_data.get('command')
        failed_pages = context_data.get('failed_pages', [])
        scraped_pages = context_data.get('scraped_pages', [])

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
        
        # Remove outdated documents (same command, but source not in updated scraped pages)
        # This cleanup happens AFTER retries, so successful retries are preserved
        # We exclude:
        # 1. Pages that were just scraped/updated (updated_scraped)
        # 2. Pages that were checked and found unchanged (pages_ignored)
        # 3. Pages that failed (remaining_failed)
        
        exclude_sources = updated_scraped + context_data.get('ignored_pages', []) + remaining_failed
                
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
