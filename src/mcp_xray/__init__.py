"""mcp-xray -- one field instrument for MCP server reviews.

Many sensors, one voice. Wrapped tools contribute measurements only; the
grading engine owns all interpretation. See design/MCP_XRAY_PLAN.md.
"""

__version__ = "1.4.0"

from .finding import Finding
from .inventory import Inventory, Tool

__all__ = ["Finding", "Inventory", "Tool", "__version__"]
