import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Dict, List, Optional

# Classes to help serialize various Alfred formats


class EnhancedJSONEncoder(json.JSONEncoder):
        def default(self, o):
            if is_dataclass(o):
                return asdict(o)
            elif isinstance(o, datetime):
                return o.isoformat()
            return super().default(o)


@dataclass
class ItemIcon():
    path: str


@dataclass
class Item():
    uid: str
    title: str
    subtitle: str
    arg: str
    variables: dict = None
    icon: Optional[ItemIcon] = None


@dataclass
class ScriptFilterOutput():
    """Script Filter Output format"""
    items: List[Item] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self, indent=2, cls=EnhancedJSONEncoder)


@dataclass
class AlfredWorkflow():
    arg: str
    config: Dict[str, str] = None
    variables: Dict[str, str] = None


@dataclass
class JsonUtilityFormat():
    """Alfred workflow output - JSON Utility"""
    alfredworkflow: AlfredWorkflow

    def to_json(self) -> str:
        return json.dumps(self, indent=2, cls=EnhancedJSONEncoder)

