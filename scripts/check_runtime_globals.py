from pathlib import Path
import builtins
import symtable

ROOT = Path(__file__).resolve().parents[1]
IGNORE = {"__file__", "__name__", "__package__", "__spec__"}
failures = []
for path in ROOT.rglob("*.py"):
    if "tests" in path.parts or "__pycache__" in path.parts:
        continue
    source = path.read_text()
    table = symtable.symtable(source, str(path), "exec")
    module_defs = {
        name for name in table.get_identifiers()
        if table.lookup(name).is_assigned() or table.lookup(name).is_imported() or table.lookup(name).is_namespace()
    }
    missing = set()
    def walk(tab):
        for child in tab.get_children():
            for name in child.get_identifiers():
                sym = child.lookup(name)
                if (
                    sym.is_global() and sym.is_referenced()
                    and name not in module_defs
                    and name not in IGNORE
                    and not hasattr(builtins, name)
                ):
                    missing.add(name)
            walk(child)
    walk(table)
    if missing:
        failures.append((path.relative_to(ROOT), sorted(missing)))

if failures:
    for path, names in failures:
        print(f"FAIL {path}: undefined runtime globals: {', '.join(names)}")
    raise SystemExit(1)
print("Runtime global-symbol audit: PASS")
