import json
import re
import inspect
from bs4 import BeautifulSoup
from langchain_community.document_loaders.parsers.html.bs4 import BS4HTMLParser
from langchain.document_loaders.blob_loaders.schema import Blob

from cat.log import log
from cat.mad_hatter.decorators import hook
from cat.rabbit_hole import RabbitHole
from cat.utils import singleton

# Monkey-patch RabbitHole.ingest_file to temporarily store metadata

original_ingest_file = None
RabbitHoleClass = None

# 1. Try to find the class in singleton.instances
# Keys in singleton.instances are CLASSES. We look for one named 'RabbitHole'
for cls in singleton.instances:
    if cls.__name__ == "RabbitHole":
        RabbitHoleClass = cls
        original_ingest_file = cls.ingest_file
        break

# 2. If not found (not instantiated yet), get it from the closure of the RabbitHole function
if (
    not RabbitHoleClass
    and hasattr(RabbitHole, "__closure__")
    and RabbitHole.__closure__
):
    for cell in RabbitHole.__closure__:
        if (
            inspect.isclass(cell.cell_contents)
            and cell.cell_contents.__name__ == "RabbitHole"
        ):
            RabbitHoleClass = cell.cell_contents
            original_ingest_file = RabbitHoleClass.ingest_file
            break

# 3. Fallback: try to find it in the module directly if inspect failed
if not RabbitHoleClass:
    import sys

    cat_module = sys.modules.get("cat.rabbit_hole")
    if cat_module:
        for name, obj in inspect.getmembers(cat_module):
            if inspect.isclass(obj) and name == "RabbitHole" and obj is not RabbitHole:
                RabbitHoleClass = obj
                original_ingest_file = obj.ingest_file
                break

if original_ingest_file and RabbitHoleClass:

    def custom_ingest_file(
        self, cat, file, chunk_size=None, chunk_overlap=None, metadata=None
    ):
        if metadata is None:
            metadata = {}

        # Store metadata temporarily in cat
        if hasattr(cat, "working_memory"):
            cat.working_memory.temp_ingest_metadata = metadata
        else:
            cat._temp_ingest_metadata = metadata

        try:
            # Call original
            return original_ingest_file(
                self, cat, file, chunk_size, chunk_overlap, metadata
            )
        finally:
            # Clean up
            if hasattr(cat, "working_memory") and hasattr(
                cat.working_memory, "temp_ingest_metadata"
            ):
                # Safely delete if exists
                if hasattr(cat.working_memory, "temp_ingest_metadata"):
                    del cat.working_memory.temp_ingest_metadata
            elif hasattr(cat, "_temp_ingest_metadata"):
                delattr(cat, "_temp_ingest_metadata")

    # Patch the class directly
    RabbitHoleClass.ingest_file = custom_ingest_file


@hook(priority=20)
def before_rabbithole_splits_text(doc, cat):
    # Retrieve metadata
    metadata = {}
    if hasattr(cat, "working_memory") and hasattr(
        cat.working_memory, "temp_ingest_metadata"
    ):
        metadata = cat.working_memory.temp_ingest_metadata
    elif hasattr(cat, "_temp_ingest_metadata"):
        metadata = cat._temp_ingest_metadata

    if metadata and "source" in metadata:
        source_url = metadata["source"]
        for d in doc:
            # Update the source metadata
            d.metadata["source"] = source_url

    return doc


class CustomHTMLParser(BS4HTMLParser):
    """Custom HTML parser that can remove specific elements before parsing."""

    def __init__(self, ignore_display_none: bool = False):
        self.ignore_display_none = ignore_display_none
        super().__init__()

    def lazy_parse(self, blob: Blob):
        """Iterator for parsing"""
        # Direct yield to avoid recursion issues with super().parse calling lazy_parse calling parse
        # We process here directly since we want one document per blob usually

        # 1. Load content
        with blob.as_bytes_io() as f:
            content = f.read()

        # 2. Process with BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")

        # 3. Remove divs with display: none
        if self.ignore_display_none:
            # Find all divs with a style attribute
            divs = soup.find_all("div", style=True)
            for div in divs:
                style_value = div["style"].lower()
                clean_style = style_value.replace(" ", "").replace(";", "")

                should_remove = False
                if "display:none" in clean_style:
                    should_remove = True

                if not should_remove and ":" in style_value:
                    declarations = [
                        d.strip() for d in style_value.split(";") if d.strip()
                    ]
                    for decl in declarations:
                        if ":" in decl:
                            prop, val = decl.split(":", 1)
                            if prop.strip() == "display" and val.strip().startswith(
                                "none"
                            ):
                                should_remove = True
                                break

                if should_remove:
                    div.decompose()

        # 4. Use the parent class to parse the modified content
        # Important: We must call the parent's logic carefully.
        # BS4HTMLParser in Langchain typically implements parse() which calls lazy_parse().
        # If we call super().parse(), it might cycle back to us.
        # So we should reinstantiate the parent parser logic directly or call a safe method.

        # Create a new blob with the modified content
        new_blob = Blob.from_data(
            data=str(soup).encode("utf-8"), mime_type="text/html", path=blob.source
        )

        # To strictly avoid recursion if parent parse calls lazy_parse:
        # We manually invoke the logic of BS4HTMLParser.lazy_parse if available,
        # or we just rely on super().lazy_parse(new_blob) if it exists.

        yield from super().lazy_parse(new_blob)

    def parse(self, blob: Blob) -> list:
        # For convenience, just consume our lazy_parse
        return list(self.lazy_parse(blob))


@hook(priority=10)  # Run late to ensure we override others
def rabbithole_instantiates_parsers(file_handlers: dict, cat) -> dict:
    """Hook to replace the default HTML parser with our custom one."""

    # Load settings
    try:
        settings = {}
        # Try both plugin ID scenarios
        try:
            if "ccat_memory_updater" in cat.mad_hatter.plugins:
                settings = cat.mad_hatter.plugins["ccat_memory_updater"].load_settings()
                log.info(f"Loaded settings via plugins dict: {settings}")
            else:
                log.warning("ccat_memory_updater not found in cat.mad_hatter.plugins")
                raise KeyError("Plugin not found in dict")
        except KeyError:
            settings = cat.mad_hatter.get_plugin().load_settings()
            log.info(f"Loaded settings via get_plugin: {settings}")

        ignore_display_none = settings.get("ignore_display_none", False)

        if ignore_display_none:
            file_handlers["text/html"] = CustomHTMLParser(ignore_display_none=True)

        # file_handlers["application/pdf"] = HybridPDFParser()
        # log.info("Switched to HybridPDFParser for application/pdf")

    except Exception as e:
        log.error(f"Error in rabbithole_instantiates_parsers: {e}")
        log.warning(
            json.dumps(
                {
                    "component": "ccat_memory_updater",
                    "event": "html_parser_settings_error",
                    "data": {"error": str(e)},
                }
            )
        )

    return file_handlers


@hook
def after_rabbithole_splitted_text(chunks, cat):
    filtered_chunks = []
    # Regex to catch sequences of (cid:XXX) repeated at least twice
    cid_pattern = re.compile(r"(\(cid:\d+\)\s*){2,}")

    for chunk in chunks:
        # 1. check if the length of the chunk text is less than 50 chars
        if len(chunk.page_content) < 50:
            continue

        # 2. check if the chunk's source metadata ends with .pdf
        source = chunk.metadata.get("source", "")
        if source.lower().endswith(".pdf"):

            # a. check for repeated characters (ignoring spaces)
            s = chunk.page_content.replace(" ", "").strip()
            if s and s.count(s[0]) == len(s):
                continue

            # b. check for CID pattern
            if cid_pattern.search(chunk.page_content):
                continue

        filtered_chunks.append(chunk)
    if len(filtered_chunks) == 0:
        log.warning(
            "All chunks were filtered out in after_rabbithole_splitted_text hook."
        )
    return filtered_chunks
