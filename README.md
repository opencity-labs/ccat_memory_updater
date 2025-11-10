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

## Note

Deletion only affects the declarative memory.
