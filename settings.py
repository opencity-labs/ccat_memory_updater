from pydantic import BaseModel, Field, validator
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
    
    max_retry_attempts: int = Field(
        default=3,
        title="Max Retry Attempts",
        description="Maximum number of retry attempts for failed URLs (only used when retry_failed_urls is enabled)."
    )
    
    retry_delay_seconds: int = Field(
        default=2,
        title="Retry Delay (seconds)",
        description="Delay in seconds between retry attempts for failed URLs."
    )
    
    @validator('max_retry_attempts')
    def validate_max_retry_attempts(cls, v):
        """Validate that max retry attempts is between 1 and 10"""
        if not 1 <= v <= 10:
            raise ValueError('Max retry attempts must be between 1 and 10')
        return v
    
    @validator('retry_delay_seconds')
    def validate_retry_delay_seconds(cls, v):
        """Validate that retry delay is between 0 and 60"""
        if not 0 <= v <= 60:
            raise ValueError('Retry delay must be between 0 and 60 seconds')
        return v


# Give your settings model to the Cat.
@plugin
def settings_model():
    return MemoryUpdaterSettings