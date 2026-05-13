"""内置工具 Handlers。"""

from backend.agent.tools.builtin.external import handle_search_external_docs
from backend.agent.tools.builtin.files import (
    handle_file_list,
    handle_file_read,
    handle_file_search,
    handle_file_write,
)
from backend.agent.tools.builtin.memory import (
    handle_memory_save,
    handle_memory_search,
)
from backend.agent.tools.builtin.profile import (
    handle_get_profile,
    handle_update_profile,
)

__all__ = [
    "handle_file_list",
    "handle_file_read",
    "handle_file_search",
    "handle_file_write",
    "handle_get_profile",
    "handle_memory_save",
    "handle_memory_search",
    "handle_search_external_docs",
    "handle_update_profile",
]
