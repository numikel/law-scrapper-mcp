"""Quick test to verify server loads correctly."""

import sys
import importlib.util

def test_import(module_name):
    """Test if a module can be imported."""
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            print(f"❌ Module {module_name} not found")
            return False

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print(f"✓ Module {module_name} loaded successfully")
        return True
    except Exception as e:
        print(f"❌ Failed to load {module_name}: {e}")
        return False

if __name__ == "__main__":
    print("Testing Law Scrapper MCP v2.0 imports...\n")

    modules_to_test = [
        "law_scrapper_mcp.config",
        "law_scrapper_mcp.logging_config",
        "law_scrapper_mcp.client.cache",
        "law_scrapper_mcp.client.sejm_client",
        "law_scrapper_mcp.client.exceptions",
        "law_scrapper_mcp.models.enums",
        "law_scrapper_mcp.models.api_responses",
        "law_scrapper_mcp.models.tool_inputs",
        "law_scrapper_mcp.models.tool_outputs",
        "law_scrapper_mcp.services.content_processor",
        "law_scrapper_mcp.services.document_store",
        "law_scrapper_mcp.services.metadata_service",
        "law_scrapper_mcp.services.search_service",
        "law_scrapper_mcp.services.act_service",
        "law_scrapper_mcp.services.changes_service",
        "law_scrapper_mcp.services.response_enrichment",
        "law_scrapper_mcp.tools",
        "law_scrapper_mcp.server",
    ]

    success_count = 0
    for module in modules_to_test:
        if test_import(module):
            success_count += 1

    print(f"\n{'='*50}")
    print(f"Results: {success_count}/{len(modules_to_test)} modules loaded successfully")

    if success_count == len(modules_to_test):
        print("✓ All modules loaded successfully!")
        sys.exit(0)
    else:
        print("❌ Some modules failed to load")
        sys.exit(1)
