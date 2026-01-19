import json
from bs4 import BeautifulSoup
from langchain_community.document_loaders.parsers.html.bs4 import BS4HTMLParser
from langchain.document_loaders.blob_loaders.schema import Blob

from cat.log import log
from cat.mad_hatter.decorators import hook


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
        soup = BeautifulSoup(content, 'html.parser')
        
        # 3. Remove divs with display: none
        if self.ignore_display_none:
            # Find all divs with a style attribute
            divs = soup.find_all('div', style=True)
            for div in divs:
                style_value = div['style'].lower()
                clean_style = style_value.replace(' ', '').replace(';', '')
                
                should_remove = False
                if 'display:none' in clean_style:
                    should_remove = True
                
                if not should_remove and ':' in style_value:
                    declarations = [d.strip() for d in style_value.split(';') if d.strip()]
                    for decl in declarations:
                        if ':' in decl:
                            prop, val = decl.split(':', 1)
                            if prop.strip() == 'display' and val.strip().startswith('none'):
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
            data=str(soup).encode('utf-8'),
            mime_type="text/html",
            path=blob.source
        )
        
        # To strictly avoid recursion if parent parse calls lazy_parse:
        # We manually invoke the logic of BS4HTMLParser.lazy_parse if available,
        # or we just rely on super().lazy_parse(new_blob) if it exists.
        
        yield from super().lazy_parse(new_blob)
        
    def parse(self, blob: Blob) -> list:
        # For convenience, just consume our lazy_parse
        return list(self.lazy_parse(blob))


@hook(priority=10) # Run late to ensure we override others
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
        log.info(f"ignore_display_none setting is: {ignore_display_none}")
        
        if ignore_display_none:
            file_handlers["text/html"] = CustomHTMLParser(ignore_display_none=True)
            
    except Exception as e:
        log.error(f"Error in rabbithole_instantiates_parsers: {e}")
        log.warning(json.dumps({
            "component": "ccat_memory_updater",
            "event": "html_parser_settings_error",
            "data": {
                "error": str(e)
            }
        }))
            
    return file_handlers
