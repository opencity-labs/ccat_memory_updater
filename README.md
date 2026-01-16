<p align="center">
    <img src="memory_updater.png" alt="Plugin Logo" width="50" style="border-radius: 50%; vertical-align: middle; margin-right: 10px;" />
    <span style="font-size:2em; vertical-align: middle;"><b>Memory Updater</b></span>
</p>

[![CheshireCat AI Plugin - Memory Updater](https://custom-icon-badges.demolab.com/static/v1?label=&message=awesome+plugin&color=F4F4F5&style=for-the-badge&logo=cheshire_cat_black)](https://)


This plugin allows you to remove memories from the Cheshire Cat's declarative memory based on the source url.

## How to Use

1. In the plugin settings, set the `link` field to the source value you want to delete memories for.
2. Choose the `action`:
   - `delete`: Only delete memories with matching source
   - `replace`: Delete memories with matching source and then upload new content from the link
3. Save the settings. This will perform the selected action on all memories in the declarative collection that have the specified source in their metadata.

### API Endpoints

- **DELETE `/custom/memory/delete-by-source`** 
  
  Deletes all memories from the declarative memory collection that match the specified source.
  
  **Request Body:**
  ```json
  {
    "source": "string"
  }
  ```
  
  **Parameters:**
  - `source` (string, required): The source identifier to match for deletion
  
  **Response:**
  ```json
  {
    "message": "Successfully deleted {count} memories with source '{source}'"
  }
  ```
  
  **Permissions Required:** 
  - Resource: MEMORY
  - Permission: DELETE
  
  **Example:**
  ```bash
  curl -X DELETE http://localhost:1865/custom/memory/delete-by-source \
    -H "Content-Type: application/json" \
    -d '{"source": "https://example.com/page"}'
  ```

## Settings

- `link` *(string, default: "")*: The URL or link to the content source for memory operations.
- `action` *(enum: delete/replace, default: delete)*: The action to perform - either "delete" to only delete memories, or "replace" to delete and upload new content.
- `chunk_size` *(int, default: 1024)*: The size of text chunks when uploading new content. Only used for 'replace' action.
- `chunk_overlap` *(int, default: 256)*: The overlap between text chunks when uploading new content. Only used for 'replace' action.
- `ignore_display_none` *(bool, default: False)*: If enabled, ignores any div elements with style='display: none' during ingestion.

## Note

Deletion only affects the declarative memory.

## Log Schema

This plugin uses structured JSON logging to facilitate monitoring and debugging. All logs follow this base structure:

```json
{
  "component": "ccat_memory_updater",
  "event": "<event_name>",
  "data": {
    ... <event_specific_data>
  }
}
```

### Event Types

| Event Name | Description | Data Fields |
|------------|-------------|-------------|
| `memory_deletion_warning` | Logged when memory deletion is requested without a source | `message` |
| `memory_deletion_scan` | Logged when scanning for memories to delete | `source`, `points_found` |
| `memory_deletion_success` | Logged when memories are successfully deleted | `source`, `points_deleted` |
| `settings_load_error` | Logged when loading settings fails | `error` |
| `settings_save_error` | Logged when saving settings fails | `error` |
| `settings_warning` | Logged when settings are invalid (e.g. no link) | `message` |
| `content_upload_start` | Logged when starting to upload content from a link | `link` |
| `content_upload_success` | Logged when content upload succeeds | `link` |
| `content_upload_error` | Logged when content upload fails | `link`, `error` |
| `url_check_fetch_error` | Logged when fetching a URL for checking fails | `url`, `error` |
| `url_check_handler_error` | Logged when RabbitHole handlers are inaccessible | `message` |
| `url_check_parse_error` | Logged when parsing a URL fails | `url`, `error` |
| `url_check_hash_missing` | Logged when Dietician fails to compute a hash | `url`, `message` |
| `url_check_error` | Logged when checking a URL fails | `url`, `error` |
| `plugin_check_error` | Logged when checking plugin status fails | `plugin_id`, `error` |
| `optimization_skipped` | Logged when optimization is skipped (e.g. Dietician missing) | `reason` |
| `optimization_start` | Logged when optimization starts | `session_id` |
| `optimization_check_start` | Logged when parallel URL checking starts | `page_count` |
| `optimization_check_error` | Logged when checking a specific URL fails | `url`, `error` |
| `html_parser_element_removed` | Logged when a hidden div is removed during parsing | `source`, `element`, `reason` |
| `html_parser_replaced` | Logged when the default HTML parser is replaced | `parser`, `reason` |
| `html_parser_settings_error` | Logged when loading settings for the parser hook fails | `error` |
| `optimization_complete` | Logged when optimization is complete | `pages_to_update`, `pages_ignored` |
| `optimization_progress` | Logged periodically during parallel URL checking | `processed`, `total`, `percentage` |
| `middleman_hook_triggered` | Logged when the ScrapyCat-Dietician middleman hook runs | `dietician_plugin`, `scrapycat_plugin` |
| `middleman_error` | Logged when the middleman hook encounters an error | `error` |
| `cleanup_start` | Logged when cleanup starts | `session_id`, `command`, `scraped_count`, `failed_count` |
| `retry_start` | Logged when retry process starts | `failed_count`, `max_attempts` |
| `retry_success_all` | Logged when all retries succeed | `message` |
| `retry_attempt` | Logged for each retry attempt | `attempt`, `max_attempts`, `remaining_count` |
| `retry_url_success` | Logged when a single URL retry succeeds | `attempt`, `url` |
| `retry_url_failed` | Logged when a single URL retry fails | `attempt`, `url`, `error` |
| `retry_wait` | Logged when waiting between retries | `seconds` |
| `retry_complete` | Logged when retry process completes | `success_count`, `failed_count` |
| `retry_disabled` | Logged when retries are disabled | `skipped_count` |
| `retry_summary` | Logged with summary of retry results | `success_count`, `failed_count`, `errors` |
| `cleanup_complete` | Logged when cleanup is complete | `removed_count`, `vector_removed_count`, `removed_urls` |
