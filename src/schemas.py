from datetime import datetime
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
import uuid

class GridCell(BaseModel):
    x: float
    y: float
    count: int = Field(..., ge=0)
    cell_id: Optional[str] = None

class CrowdDensityEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = "crowd_density"
    timestamp: datetime
    level: int
    grid_data: List[GridCell]
    total_people: int = Field(..., ge=0)
    metadata: Dict[str, str]

    @classmethod
    def create(cls, level: int, grid_data: List[dict], camera_id: str):
        cells = [GridCell(**cell) for cell in grid_data]
        total = sum(c.count for c in cells)
        return cls(
            timestamp=datetime.now(),
            level=level,
            grid_data=cells,
            total_people=total,
            metadata={"camera_id": camera_id}
        )

class QueueEvent(BaseModel):
    """Event received from downstream broker"""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = "queue_update"
    location_type: str
    location_id: str
    queue_length: int = Field(..., ge=0)
    timestamp: datetime
    metadata: Dict[str, str]

    @classmethod
    def create(cls, location_type: str, location_id: str, queue_length: int, camera_id: str):
        return cls(
            event_id=str(uuid.uuid4()),
            location_type=location_type,
            location_id=location_id,
            queue_length=queue_length,
            timestamp=datetime.now(),
            metadata={"camera_id": camera_id}
        )
