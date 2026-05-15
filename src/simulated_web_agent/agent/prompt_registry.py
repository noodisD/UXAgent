"""
Unified lazy prompt loader.

All prompt directories are registered once. Prompts are loaded on first
access and cached. Callers use a single get(name) call regardless of which
directory the prompt lives in.
"""
from pathlib import Path


class PromptRegistry:
    def __init__(self, *directories: Path):
        self._dirs = directories
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            for d in self._dirs:
                p = d / f"{name}.txt"
                if p.exists():
                    self._cache[name] = p.read_text(encoding="utf-8")
                    return self._cache[name]
            raise FileNotFoundError(
                f"Prompt '{name}' not found in any registered directory: "
                + ", ".join(str(d) for d in self._dirs)
            )
        return self._cache[name]

    def register(self, directory: Path) -> None:
        """Add a directory to the search path (checked after existing dirs)."""
        self._dirs = (*self._dirs, directory)


_agent_dir = Path(__file__).parent.absolute()

# Module-level default registry: shop_prompts first, audit_prompts second.
# Callers use registry.get("perceive"), registry.get("heuristic_evaluate"), etc.
registry = PromptRegistry(
    _agent_dir / "shop_prompts",
    _agent_dir.parent / "audit_prompts",
)
