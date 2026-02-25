import inspect
import sys
from cat.rabbit_hole import RabbitHole
from cat.utils import singleton
from cat.mad_hatter.decorators import hook

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


@hook(priority=10)
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
        for d in doc:
            d.metadata["source"] = metadata["source"]

    return doc
