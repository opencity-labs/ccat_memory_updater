import json
import httpx
import email.utils
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any

from cat.log import log
from cat.looking_glass.cheshire_cat import CheshireCat
from cat.mad_hatter.decorators import hook


def check_url(url: str, cat: CheshireCat) -> tuple:
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
    
    if scraped_pages:
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
        
        max_workers = 10 if settings.get("enable_parallel_check", False) else 1
        
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

    return context_data
