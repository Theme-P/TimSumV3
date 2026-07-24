from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any

from app.core.auth import get_current_admin
from app.models.user import UserData

try:
    import psutil
except ImportError:
    psutil = None

router = APIRouter()

@router.get("/resources")
def get_system_resources(current_user: UserData = Depends(get_current_admin)) -> Dict[str, Any]:
    """
    Get current system resource usage: CPU, RAM, Disk.
    Access restricted to Admin/SuperAdmin.
    """
    if psutil is None:
        raise HTTPException(status_code=503, detail="System resource monitoring requires psutil")

    try:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=0.5)
        
        # Memory
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used = round(mem.used / (1024**3), 2)
        ram_total = round(mem.total / (1024**3), 2)
        
        # Disk
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        disk_used = round(disk.used / (1024**3), 2)
        disk_total = round(disk.total / (1024**3), 2)
        
        import datetime
        return {
            "cpu_percent": cpu_percent,
            "memory": {
                "percent": ram_percent,
                "used_gb": ram_used,
                "total_gb": ram_total
            },
            "disk": {
                "percent": disk_percent,
                "used_gb": disk_used,
                "total_gb": disk_total
            },
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get system resources: {str(e)}")
