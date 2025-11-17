from pydantic import BaseModel, Field
from cat.mad_hatter.decorators import plugin
from enum import Enum


class Action(Enum):
    DELETE = "delete"
    REPLACE = "replace"


# settings
class MemoryUpdaterSettings(BaseModel):
    """Settings for the Memory Updater plugin."""
    
    link: str = Field(
        default="",
        title="Link to Content",
        description="The URL or link to the content source for memory operations."
    )
    
    action: Action = Field(
        default=Action.DELETE,
        title="Action to Perform",
        description="Choose 'delete' to only delete memories with matching source, or 'replace' to delete and then upload new content from the link."
    )
    
    chunk_size: int = Field(
        default=1024,
        title="Chunk Size",
        description="The size of text chunks when uploading new content (only used for 'replace' action)."
    )
    
    chunk_overlap: int = Field(
        default=256,
        title="Chunk Overlap",
        description="The overlap between text chunks when uploading new content (only used for 'replace' action)."
    )
    
    dietician_scrapycat_middleman: bool = Field(
        default=False,
        title="Dietician ScrapyCat Middleman",
        description="Enable coordination between ScrapyCat and Dietician for automatic cleanup of outdated scraped content.",
    )
    
    retry_failed_urls: bool = Field(
        default=True,
        title="Retry Failed URLs",
        description="Automatically retry URLs that failed during the initial ScrapyCat ingestion process.",
    )
    


# Give your settings model to the Cat.
@plugin
def settings_model():
    return MemoryUpdaterSettings