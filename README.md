<p align="center">
    <img src="memory_updater.png" alt="Plugin Logo" width="50" style="border-radius: 50%; vertical-align: middle; margin-right: 10px;" />
    <span style="font-size:2em; vertical-align: middle;"><b>Memory Updater</b></span>
</p>

[![CheshireCat AI Plugin - Memory Updater](https://custom-icon-badges.demolab.com/static/v1?label=&message=awesome+plugin&color=F4F4F5&style=for-the-badge&logo=cheshire_cat_black)](https://)

This plugin provides advanced memory management capabilities for the Cheshire Cat, acting as a bridge between ingestion tools and the vector memory. It supports manual updates, sophisticated cleaning of scraped content, and coordination between scraping plugins.

## Project Structure

The codebase is organized into three main components:

1.  **Main (`memory_updater.py`)**: Handles core plugin logic, manual operations via settings, and API endpoints.
2.  **Parser (`parser.py`)**: A custom HTML parser that enhances content quality before ingestion.
3.  **Middleman (`middleman/`)**: Coordinates workflows between **ScrapyCat** and **Dietician** plugins.
    *   `optimization.py`: Handles parallel checks to skip scraping unchanged content.
    *   `cleanup.py`: Manages retries for failed URLs and cleans up outdated memories.

## Features

### 1. Manual Memory Management
Directly from the plugin settings, you can manage memories by source URL:
- **Delete**: Remove all vector memories associated with a specific URL.
- **Replace**: Delete and immediately re-ingest content from a URL.

### 2. API Endpoint
**DELETE `/memory/delete-by-source`**
Programmatically delete memories by source.
```json
{ "source": "https://example.com/page" }
```

### 3. Smart HTML Parsing
Includes a custom HTML parser that detects and removes `div` elements with `style="display: none"`. This prevents hidden boilerplate text (like cookie banners or mobile menus) from polluting your vector memory.
- **Config**: Enable via `ignore_display_none` setting.

### 4. Middleman: ScrapyCat & Dietician Integration
When used with **ScrapyCat** and **Dietician**, this plugin acts as a coordinator to optimize ingestion pipelines:

*   **Smart Optimization**: Checks `Last-Modified` and `ETag` headers in parallel before scraping. If a page hasn't changed since the last ingestion, it's skipped to save resources.
*   **Robust Retry Protocol**: Automatically retries failed URLs from ScrapyCat.
    *   Distinguishes between transient errors (timeouts, 500s) and permanent errors (404s, unsupported types).
    *   Only retries transient errors (configurable attempts and delay).
*   **Auto-Cleanup**: Automatically detects and deletes memories for URLs that were *not* present in the latest scrape command, ensuring your memory stays in sync with your source list.
*   **Anonymizer Sync**: Automatically removes deleted sources.

## Settings

| Setting | Default | Description |
|:---|:---|:---|
| `link` | - | Target URL for manual delete/replace operations. |
| `action` | `delete` | Manual action to perform (`delete` or `replace`). |
| `chunk_size` | 1024 | Chunk size for ingestions. |
| `chunk_overlap` | 256 | Chunk overlap for ingestions. |
| `ignore_display_none`| `False` | Remove hidden divs during HTML parsing. |
| `enable_parallel_check`| `False` | Enable pre-scrape header checks (requires ScrapyCat). |
| `check_workers` | 10 | Number of threads for parallel checks. |
| `dietician_scrapycat_middleman`| `False` | Enable coordination between plugins. |
| `retry_failed_urls` | `True` | Auto-retry failed ScrapyCat URLs. |
| `max_retry_attempts` | 3 | Number of retry attempts. |
| `retry_delay_seconds` | 10 | Delay between retries. |

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

#### Main (`memory_updater.py`)
| Event Name | Description |
|------------|-------------|
| `memory_deletion_warning` | Logged when memory deletion is requested without a source |
| `memory_deletion_scan` | Logged when scanning for memories to delete |
| `memory_deletion_success` | Logged when memories are successfully deleted |
| `settings_load_error` | Logged when loading settings fails |
| `settings_save_error` | Logged when saving settings fails |
| `settings_warning` | Logged when settings are invalid (e.g. no link) |
| `content_upload_start` | Logged when starting to upload content from a link |
| `content_upload_success` | Logged when content upload succeeds |
| `content_upload_error` | Logged when content upload fails |

#### Parser (`parser.py`)
| Event Name | Description |
|------------|-------------|
| `html_parser_settings_error` | Logged when loading settings for the parser hook fails |

#### Optimization (`middleman/optimization.py`)
| Event Name | Description |
|------------|-------------|
| `check_url_decision` | Logged when deciding whether to update a URL |
| `check_url_fetch_error` | Logged when fetching a URL for checking fails |
| `check_url_etag_found` | Logged when an ETag header is found |
| `check_url_no_etag` | Logged when an ETag header is missing |
| `check_url_date_parse_error` | Logged when parsing Last-Modified date fails |
| `check_url_unexpected_error` | Logged when an unexpected error occurs during check |
| `optimization_check_start` | Logged when parallel URL checking starts |
| `optimization_progress` | Logged periodically during parallel URL checking |
| `optimization_check_error` | Logged when checking a specific URL fails |
| `optimization_complete` | Logged when optimization is complete |

#### Cleanup (`middleman/cleanup.py`)
| Event Name | Description |
|------------|-------------|
| `plugin_check_error` | Logged when checking plugin status fails |
| `middleman_hook_triggered` | Logged when the ScrapyCat-Dietician middleman hook runs |
| `middleman_error` | Logged when the middleman hook encounters an error |
| `cleanup_start` | Logged when cleanup starts |
| `retry_start` | Logged when retry process starts |
| `retry_success_all` | Logged when all retries succeed |
| `retry_attempt` | Logged for each retry attempt |
| `retry_url_success` | Logged when a single URL retry succeeds |
| `retry_skipped_permanent_error` | Logged when a permanent error causes retry skip |
| `retry_url_exhausted` | Logged when a URL fails all retry attempts |
| `retry_wait` | Logged when waiting between retries |
| `retry_complete` | Logged when retry process completes |
| `retry_disabled` | Logged when retries are disabled |
| `retry_summary` | Logged with summary of retry results |
| `url_scraped` | Logged for each successfully scraped URL |
| `document_cleanup` | Logged for each URL removed during cleanup |
| `anonymizer_cleanup` | Logged when sources are removed from anonymizer |
| `anonymizer_cleanup_error` | Logged when anonymizer cleanup fails |


