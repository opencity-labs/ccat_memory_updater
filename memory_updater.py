import os
import json
from typing import Dict

from cat.log import log
from cat.looking_glass.stray_cat import StrayCat
from cat.mad_hatter.decorators import plugin, endpoint
from cat.looking_glass.cheshire_cat import CheshireCat
from cat.auth.permissions import AuthPermission, AuthResource, check_permissions
from pydantic import BaseModel
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
        log.warning(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "memory_deletion_warning",
                    "data": {"message": "No source provided for memory deletion"},
                }
            )
        )
        return 0

    # Handle both StrayCat (endpoint) and CheshireCat (plugin) instances
    if hasattr(cat_or_ccat, "memory"):
        vector_memory = cat_or_ccat.memory.vectors
    else:
        vector_memory = cat_or_ccat.vectors

    collection = vector_memory.collections["declarative"]

    # First, count the points
    filter_obj = collection._qdrant_filter_from_dict({"source": source})
    points, _ = collection.client.scroll(
        collection_name=collection.collection_name,
        scroll_filter=filter_obj,
        limit=10000,
    )

    point_count = len(points)
    log.info(
        json.dumps(
            {
                "component": "ccat_memory_updater",
                "event": "memory_deletion_scan",
                "data": {"source": source, "points_found": point_count},
            }
        )
    )

    if point_count > 0:
        # Delete the points
        collection.delete_points_by_metadata_filter({"source": source})
        log.info(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "memory_deletion_success",
                    "data": {"source": source, "points_deleted": point_count},
                }
            )
        )

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
            log.error(
                json.dumps(
                    {
                        "component": "ccat_memory_updater",
                        "event": "settings_load_error",
                        "data": {"error": str(e)},
                    }
                )
            )

    # Merge new settings with old ones
    updated_settings = {**old_settings, **settings}

    # Save settings to file
    try:
        with open(settings_file_path, "w") as json_file:
            json.dump(updated_settings, json_file, indent=4)
        return updated_settings
    except Exception as e:
        log.error(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "settings_save_error",
                    "data": {"error": str(e)},
                }
            )
        )
        return {}


@plugin
def save_settings(settings):
    ccat = CheshireCat()

    link = settings.get("link", "")
    action_str = settings.get("action", Action.DELETE.value)
    action = (
        Action(action_str) if action_str in [e.value for e in Action] else Action.DELETE
    )
    chunk_size = settings.get("chunk_size", 1024)
    chunk_overlap = settings.get("chunk_overlap", 256)

    if not link:
        log.warning(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "settings_warning",
                    "data": {"message": "No link provided"},
                }
            )
        )

    else:
        delete_memories_by_source_logic(link, ccat)

        if action == Action.REPLACE:
            # Upload new content from the link
            log.info(
                json.dumps(
                    {
                        "component": "ccat_memory_updater",
                        "event": "content_upload_start",
                        "data": {"link": link},
                    }
                )
            )
            try:
                ccat.rabbit_hole.ingest_file(
                    cat=ccat,
                    file=link,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
                log.info(
                    json.dumps(
                        {
                            "component": "ccat_memory_updater",
                            "event": "content_upload_success",
                            "data": {"link": link},
                        }
                    )
                )
            except Exception as e:
                log.error(
                    json.dumps(
                        {
                            "component": "ccat_memory_updater",
                            "event": "content_upload_error",
                            "data": {"link": link, "error": str(e)},
                        }
                    )
                )

        # reset the link to empty after processing
        settings["link"] = ""

    # Save settings using the extracted function (replicates default Cat behavior)
    plugin_path = os.path.dirname(os.path.abspath(__file__))
    return save_plugin_settings_to_file(settings, plugin_path)


@endpoint.delete(path="/memory/delete-by-source", tags=["Memory Updater"])
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
