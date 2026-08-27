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

    def version_hash(self) -> str:
        """A short digest of every registered prompt's content.

        Goes into run_manifest.json so the report's Scope section (§6.1) can say
        which prompts produced the findings. Two runs whose hashes differ are not
        comparable — and the prompts move often enough for that to matter: the
        anchoring fixes alone rewrote first_impression, heuristic_evaluate and
        accessibility_summarize in one day.

        Hashes content, not mtimes, and sorts by name so two checkouts of the
        same commit agree.
        """
        import hashlib

        h = hashlib.sha256()
        seen: dict[str, bytes] = {}
        for d in self._dirs:
            if not d.is_dir():
                continue
            for p in d.glob("*.txt"):
                # First directory wins, matching get()'s search order.
                seen.setdefault(p.name, p.read_bytes())
        for name in sorted(seen):
            h.update(name.encode("utf-8"))
            h.update(seen[name])
        return h.hexdigest()[:12]

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
