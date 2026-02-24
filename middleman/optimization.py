import json
import httpx
import time
import email.utils
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Set

from cat.log import log
from cat.looking_glass.cheshire_cat import CheshireCat
from cat.mad_hatter.decorators import hook


def check_url(url: str, cat: CheshireCat, user_agent: str = "Magic Browser") -> tuple:
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
            with_payload=True,
        )

        if not points:
            # New URL, must scrape
            log.info(
                json.dumps(
                    {
                        "component": "ccat_memory_updater",
                        "event": "check_url_decision",
                        "data": {
                            "url": url,
                            "decision": "update",
                            "reason": "new_url_no_memory",
                        },
                    }
                )
            )
            return url, True

        # Get ingestion timestamp from the first point
        # RabbitHole stores 'when' as a float timestamp in metadata
        metadata = points[0].payload.get("metadata", {})
        ingestion_timestamp = metadata.get("when")

        if not ingestion_timestamp:
            # No timestamp, assume update needed
            log.info(
                json.dumps(
                    {
                        "component": "ccat_memory_updater",
                        "event": "check_url_decision",
                        "data": {
                            "url": url,
                            "decision": "update",
                            "reason": "no_ingestion_timestamp",
                        },
                    }
                )
            )
            return url, True

        # Ensure ingestion_timestamp is a float
        try:
            ingestion_time = datetime.fromtimestamp(
                float(ingestion_timestamp), tz=timezone.utc
            )
        except (ValueError, TypeError):
            # Invalid timestamp, update
            log.warning(
                json.dumps(
                    {
                        "component": "ccat_memory_updater",
                        "event": "check_url_decision",
                        "data": {
                            "url": url,
                            "decision": "update",
                            "reason": "invalid_ingestion_timestamp",
                            "raw_timestamp": str(ingestion_timestamp),
                        },
                    }
                )
            )
            return url, True

        # 2. Fetch HTTP headers
        headers = {"User-Agent": user_agent}
        try:
            # Use HEAD request to get headers only
            response = httpx.head(
                url, headers=headers, timeout=10, follow_redirects=True
            )

            # If HEAD fails (e.g. 405 Method Not Allowed), try GET with stream=True to avoid downloading body
            if response.status_code == 405:
                response = httpx.get(
                    url, headers=headers, timeout=10, follow_redirects=True
                )
                response.close()  # Close immediately

            # If still error, assume update needed
            if response.status_code >= 400:
                log.warning(
                    json.dumps(
                        {
                            "component": "ccat_memory_updater",
                            "event": "check_url_decision",
                            "data": {
                                "url": url,
                                "decision": "update",
                                "reason": "http_error",
                                "status_code": response.status_code,
                            },
                        }
                    )
                )
                return url, True

        except Exception as e:
            log.warning(
                json.dumps(
                    {
                        "component": "ccat_memory_updater",
                        "event": "check_url_fetch_error",
                        "data": {"url": url, "error": str(e)},
                    }
                )
            )
            return url, True

        # 3. Compare timestamps
        server_etag = response.headers.get("ETag")

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
                    log.info(
                        json.dumps(
                            {
                                "component": "ccat_memory_updater",
                                "event": "check_url_decision",
                                "data": {
                                    "url": url,
                                    "decision": "skip",
                                    "reason": "unchanged",
                                    "server_time": str(server_time),
                                    "ingestion_time": str(ingestion_time),
                                },
                            }
                        )
                    )
                    return url, False
                else:
                    # Content is newer -> Update
                    log.info(
                        json.dumps(
                            {
                                "component": "ccat_memory_updater",
                                "event": "check_url_decision",
                                "data": {
                                    "url": url,
                                    "decision": "update",
                                    "reason": "content_newer",
                                    "server_time": str(server_time),
                                    "ingestion_time": str(ingestion_time),
                                },
                            }
                        )
                    )
                    return url, True
            except Exception as e:
                log.warning(
                    json.dumps(
                        {
                            "component": "ccat_memory_updater",
                            "event": "check_url_date_parse_error",
                            "data": {
                                "url": url,
                                "error": str(e),
                                "last_modified_header": server_last_modified,
                            },
                        }
                    )
                )
                return url, True
        else:
            log.info(
                json.dumps(
                    {
                        "component": "ccat_memory_updater",
                        "event": "check_url_decision",
                        "data": {
                            "url": url,
                            "decision": "update",
                            "reason": "missing_last_modified",
                        },
                    }
                )
            )
            # Fallback: Check ETag if Last-Modified is missing
            # Note: ETag comparison requires storing the ETag from previous scrape.
            # Since we don't store ETag in metadata currently, we can't use it for comparison yet.
            # We could store it now for future checks, but for this run we have to assume update if Last-Modified is missing.

            # log.debug(f"Check {url}: No Last-Modified header")
            return url, True

    except Exception as e:
        log.error(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "check_url_unexpected_error",
                    "data": {"url": url, "error": str(e)},
                }
            )
        )
        return url, True


def get_sitemap_urls(root_url: str, user_agent: str) -> Set[str]:
    """Fetch and parse sitemap.xml for a given root URL."""
    sitemap_urls = set()
    sitemap_endpoint = f"{root_url.rstrip('/')}/sitemap.xml"
    time.sleep(5)
    try:
        headers = {"User-Agent": user_agent}

        response = None
        current_wait = 2
        max_retries = 5

        for attempt in range(max_retries):
            response = httpx.get(
                sitemap_endpoint, headers=headers, timeout=60, follow_redirects=True
            )

            log.info(
                json.dumps(
                    {
                        "component": "ccat_memory_updater",
                        "event": "sitemap_fetch_status",
                        "data": {
                            "sitemap_endpoint": sitemap_endpoint,
                            "root_url": root_url,
                            "status_code": response.status_code,
                            "attempt": attempt + 1,
                        },
                    }
                )
            )

            if response.status_code == 429:
                if attempt < max_retries - 1:
                    log.warning(
                        json.dumps(
                            {
                                "component": "ccat_memory_updater",
                                "event": "sitemap_rate_limit_wait",
                                "data": {
                                    "sitemap_endpoint": sitemap_endpoint,
                                    "status_code": response.status_code,
                                    "wait_seconds": current_wait,
                                    "next_retry_attempt": attempt + 2,
                                },
                            }
                        )
                    )
                    time.sleep(current_wait)
                    current_wait *= 2  # Exponential backoff
                    continue
                else:
                    log.warning(
                        json.dumps(
                            {
                                "component": "ccat_memory_updater",
                                "event": "sitemap_rate_limit_exceeded",
                                "data": {
                                    "sitemap_endpoint": sitemap_endpoint,
                                    "status_code": response.status_code,
                                    "attempts": max_retries,
                                },
                            }
                        )
                    )

            # If not 429, break the loop
            break

        if response and response.status_code == 200:
            # Parse XML content
            try:
                # Remove encoding declaration if present to avoid parsing issues with strings
                content = response.text
                root_element = ET.fromstring(content)

                # Check if it's a sitemap index or a regular urlset
                # Root tag usually is {namespace}urlset or {namespace}sitemapindex
                # We simply look for 'loc' text content in all descendants

                for elem in root_element.iter():
                    # Check if the tag ends with 'loc' (handling namespaces)
                    if elem.tag.endswith("loc") and elem.text:
                        url = elem.text.strip()
                        # Basic validation
                        if url.startswith("http"):
                            sitemap_urls.add(url)

                log.info(
                    json.dumps(
                        {
                            "component": "ccat_memory_updater",
                            "event": "sitemap_urls_found",
                            "data": {
                                "root_url": root_url,
                                "sitemap_endpoint": sitemap_endpoint,
                                "url_count": len(sitemap_urls),
                            },
                        }
                    )
                )

            except ET.ParseError as e:
                log.warning(
                    json.dumps(
                        {
                            "component": "ccat_memory_updater",
                            "event": "sitemap_parse_error",
                            "data": {
                                "sitemap_endpoint": sitemap_endpoint,
                                "root_url": root_url,
                                "error": str(e),
                            },
                        }
                    )
                )
                # Try simple regex fallback if XML parsing fails
                import re

                urls = re.findall(r"<loc>(http.*?)</loc>", response.text)
                for url in urls:
                    sitemap_urls.add(url.strip())
                log.info(
                    json.dumps(
                        {
                            "component": "ccat_memory_updater",
                            "event": "sitemap_regex_fallback_found",
                            "data": {
                                "sitemap_endpoint": sitemap_endpoint,
                                "root_url": root_url,
                                "url_count": len(sitemap_urls),
                            },
                        }
                    )
                )

    except Exception as e:
        log.warning(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "sitemap_fetch_error",
                    "data": {
                        "root_url": root_url,
                        "sitemap_endpoint": sitemap_endpoint,
                        "error": str(e),
                    },
                }
            )
        )

    return sitemap_urls


@hook
def scrapycat_before_scraping(
    context_data: Dict[str, Any], cat: CheshireCat
) -> Dict[str, Any]:
    """
    Hook called by ScrapyCat before scraping starts.
    We use this to fetch sitemaps and add URLs to the scraped pages list.
    """
    settings = cat.mad_hatter.plugins["ccat_memory_updater"].load_settings()
    user_agent = settings.get("user_agent", "Magic Browser")
    if user_agent:
        user_agent = user_agent.strip()

    # Let's try to parse command string to find URLs
    command = context_data.get("command", "")
    parts = command.split()

    # Simple extraction of URLs from command
    urls_from_command = []
    for part in parts:
        if part.startswith("http"):
            urls_from_command.append(part)

    if not urls_from_command:
        return context_data

    # Infer root domains
    root_domains = set()
    for url in urls_from_command:
        try:
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                root_domains.add(f"{parsed.scheme}://{parsed.netloc}")
        except Exception:
            continue

    # Initialize scraped_pages if not present
    if "scraped_pages" not in context_data:
        context_data["scraped_pages"] = []

    existing_urls_set = set(context_data["scraped_pages"])

    new_urls_from_sitemaps = set()
    for root in root_domains:
        sitemap_urls = get_sitemap_urls(root, user_agent)
        for s_url in sitemap_urls:
            if s_url not in existing_urls_set:
                new_urls_from_sitemaps.add(s_url)
                # log.info(
                #     json.dumps(
                #         {
                #             "component": "ccat_memory_updater",
                #             "event": "sitemap_url_discovered_before_scraping",
                #             "data": {"url": s_url, "root": root},
                #         }
                #     )
                # )

    if new_urls_from_sitemaps:
        # Extend scraped_pages
        # Note: These pages will be ingested by ScrapyCat but not necessarily crawled deeply if robot limit prevents it,
        # but ScrapyCat iterates scraped_pages for ingestion.
        current_pages = context_data.get("scraped_pages", [])
        current_pages.extend(list(new_urls_from_sitemaps))
        context_data["scraped_pages"] = current_pages

        log.info(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "sitemap_added_before_scraping",
                    "data": {
                        "added_count": len(new_urls_from_sitemaps),
                        "root_count": len(root_domains),
                    },
                }
            )
        )

    return context_data


@hook
def scrapycat_after_scraping(
    context_data: Dict[str, Any], cat: CheshireCat
) -> Dict[str, Any]:
    """
    Hook called by ScrapyCat after scraping is finished but before ingestion.
    We use this to parallelize the "should update" check.
    """

    # Load settings
    settings = cat.mad_hatter.plugins["ccat_memory_updater"].load_settings()
    user_agent = settings.get("user_agent", "Magic Browser")
    if user_agent:
        user_agent = user_agent.strip()

    session_id = context_data.get("session_id")
    scraped_pages = context_data.get("scraped_pages", [])

    # log.info(f"Optimization Hook Triggered. scraped_pages count: {len(scraped_pages)}")

    if scraped_pages:

        log.info(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "optimization_check_start",
                    "data": {
                        "page_count": len(scraped_pages),
                        "session_id": session_id,
                    },
                }
            )
        )

        pages_to_update = []
        pages_ignored = []

        max_workers = (
            settings.get("max_workers", 10)
            if settings.get("enable_parallel_check", False)
            else 1
        )

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all checks
                future_to_url = {
                    executor.submit(check_url, url, cat, user_agent): url
                    for url in scraped_pages
                }

                processed_count = 0
                total_pages = len(scraped_pages)

                for future in as_completed(future_to_url):
                    processed_count += 1
                    url = future_to_url[future]

                    if processed_count % 5 == 0 or processed_count == total_pages:
                        log.info(
                            json.dumps(
                                {
                                    "component": "ccat_memory_updater",
                                    "event": "optimization_progress",
                                    "data": {
                                        "processed": processed_count,
                                        "total": total_pages,
                                        "percentage": f"{processed_count/total_pages*100:.1f}%",
                                    },
                                }
                            )
                        )

                    try:
                        checked_url, should_update = future.result()
                        if should_update:
                            pages_to_update.append(checked_url)
                        else:
                            pages_ignored.append(checked_url)
                    except Exception as e:
                        log.error(
                            json.dumps(
                                {
                                    "component": "ccat_memory_updater",
                                    "event": "optimization_check_error",
                                    "data": {"url": url, "error": str(e)},
                                }
                            )
                        )
                        pages_to_update.append(url)  # Default to update on error
        except Exception as e:
            log.error(f"Error in ThreadPoolExecutor: {e}")
            # checking failed, update all
            pages_to_update = scraped_pages

        log.info(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "optimization_complete",
                    "data": {
                        "pages_to_update": len(pages_to_update),
                        "pages_ignored": len(pages_ignored),
                    },
                }
            )
        )

        # Update context
        context_data["scraped_pages"] = pages_to_update

        # Initialize ignored_pages if not present
        if "ignored_pages" not in context_data:
            context_data["ignored_pages"] = []
        context_data["ignored_pages"].extend(pages_ignored)

    return context_data
