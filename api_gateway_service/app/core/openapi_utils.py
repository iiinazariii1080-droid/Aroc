from typing import Any, Dict


def strip_prefix(path_url: str, prefix: str) -> str:
    if not prefix:
        return path_url
    if path_url == prefix:
        return "/"
    if path_url.startswith(prefix + "/"):
        return path_url[len(prefix):]
    return path_url


def rename_component_refs(obj: Any, service_prefix: str) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/components/"):
                parts = v.split("/")
                if len(parts) >= 4:
                    out[k] = f"#/components/{parts[2]}/{service_prefix}{parts[3]}"
                else:
                    out[k] = v
            else:
                out[k] = rename_component_refs(v, service_prefix)
        return out
    if isinstance(obj, list):
        return [rename_component_refs(i, service_prefix) for i in obj]
    return obj


def merge_component_sections(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    for sec, payload in source.items():
        tgt = target.setdefault(sec, {})
        for name, val in (payload or {}).items():
            tgt[name] = val
